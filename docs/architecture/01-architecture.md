# Architecture — `mynewtowt` V3

> Étape 9 — `/architecture`. Architecture cible, flux, et organisation
> de l'application au regard des personas et des nouveaux modules.

## 1. Vue d'ensemble

### 1.1 Diagramme C4 niveau 1 — Contexte

```
                      ┌─────────────────────┐
                      │  Pipedrive (CRM)    │
                      └─────────┬───────────┘
                                │ sync
                                ▼
   ┌────────────────────────────────────────────────────┐
   │  mynewtowt — Plateforme NEWTOWT (FastAPI + PG 16)  │
   │                                                    │
   │  Surfaces :                                        │
   │  · Public (landing, search, /api/v1)               │
   │  · Espace client (/me)                             │
   │  · ERP staff (/dashboard...)                       │
   │  · PWA pont (/onboard)                             │
   └─────────┬──────────────────┬────────────────────┬──┘
             │                  │                    │
             ▼                  ▼                    ▼
   ┌──────────────┐   ┌─────────────────┐   ┌───────────────┐
   │   Stripe     │   │ Anthropic Claude│   │ Windy / Mapbox│
   │  paiements   │   │ chatbot + RAG   │   │ météo + cartes│
   └──────────────┘   └─────────────────┘   └───────────────┘
             ▲                  ▲                    ▲
             │                  │                    │
   ┌──────────────┐   ┌─────────────────┐   ┌───────────────┐
   │ Prospects    │   │  Clients B2B    │   │ Staff NEWTOWT │
   │ Linkedin Ads │   │ navigateur/API  │   │ navigateur    │
   └──────────────┘   └─────────────────┘   └───────────────┘
```

### 1.2 Diagramme C4 niveau 2 — Conteneurs

```
┌────────────────────────────────────────────────────────────────┐
│                     mynewtowt (docker-compose)                 │
│                                                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │  nginx   │  │  app     │  │  worker  │  │ dbt-runner    │  │
│  │  TLS     │←→│ FastAPI  │←→│ celery / │  │ scheduled     │  │
│  │  WAF     │  │ uvicorn  │  │ rq       │  │ jobs analytics│  │
│  └──────────┘  └────┬─────┘  └────┬─────┘  └──────┬────────┘  │
│                     │             │                │           │
│                     ▼             ▼                ▼           │
│                ┌────────────────────────────────────────┐      │
│                │            PostgreSQL 16                │      │
│                │  · schema public  (OLTP)                │      │
│                │  · schema analytics (OLAP/dbt)          │      │
│                │  · extension pgvector (RAG)             │      │
│                └────────────────────────────────────────┘      │
│                                                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                     │
│  │ Metabase │  │ Prometheus│  │  Loki    │                     │
│  │ BI       │  │ + Grafana │  │  logs    │                     │
│  └──────────┘  └──────────┘  └──────────┘                     │
└────────────────────────────────────────────────────────────────┘
```

### 1.3 Diagramme niveau 3 — Composants applicatifs

```
┌─────────────────────────────────────────────────────────┐
│                       FastAPI app                       │
│                                                         │
│  middlewares :                                          │
│    CORS → Security → Maintenance → CSRF → ForcePwd      │
│                                                         │
│  routers :                                              │
│  ┌────────────────┬────────────────┬────────────────┐  │
│  │   public/      │   client/      │   staff/       │  │
│  │ - landing      │ - dashboard    │ - dashboard    │  │
│  │ - search       │ - bookings     │ - planning     │  │
│  │ - about        │ - invoices     │ - commercial   │  │
│  │ - api/v1       │ - tracking     │ - cargo        │  │
│  └────────────────┴────────────────┴────────────────┘  │
│                                                         │
│  services :                                             │
│  ┌────────┬──────────┬─────────┬──────────┬─────────┐  │
│  │ auth   │ booking  │ pricing │ analytics│ chatbot │  │
│  ├────────┼──────────┼─────────┼──────────┼─────────┤  │
│  │ perm   │ capacity │ invoice │ co2 cert │ stowage │  │
│  └────────┴──────────┴─────────┴──────────┴─────────┘  │
│                                                         │
│  models : 30+ SQLAlchemy async                          │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## 2. Architecture des données

### 2.1 Modèle conceptuel macro

```
                 ┌─────────────┐
                 │   Vessel    │
                 └──────┬──────┘
                        │ n
                 ┌──────▼──────┐
                 │     Leg     │───┬─── LegFinance
                 └──────┬──────┘   ├─── LegKPI
                        │ n        ├─── EscaleOperation
        ┌───────────────┼───────────┼─── DockerShift
        │               │           ├─── SofEvent
   ┌────▼───┐     ┌─────▼───┐       ├─── NoonReport
   │ Order  │     │ Booking │       ├─── WatchLog
   └────┬───┘     └────┬────┘       ├─── OnboardChecklist
        │              │            └─── VisitorLog
        │              │
   ┌────▼────┐    ┌────▼─────┐
   │PackList │    │BookingIt.│
   └────┬────┘    └──────────┘
        │
   ┌────▼─────┐
   │ Batch    │
   └──────────┘

   ┌─────────────────┐         ┌─────────────────┐
   │ ClientAccount   │─────────│ ClientInvoice   │
   └────────┬────────┘         └─────────────────┘
            │
            └──────── CO2Certificate

   ┌─────────────────┐         ┌─────────────────┐
   │     User        │─────────│   ActivityLog   │
   └─────────────────┘         └─────────────────┘
```

### 2.2 Stratégie de séparation OLTP / OLAP

- `public.*` : tables transactionnelles (write-heavy).
- `analytics.*` : vues matérialisées + dbt models (read-heavy).
- Pas de cross-schema FK : les analytics référencent des IDs par
  convention.

### 2.3 Migrations

- **Alembic** comme source de vérité.
- Migrations atomiques + reversible.
- Scripts SQL ad hoc tolérés pour les data backfills (idempotents).
- Convention de nommage : `YYYYMMDD_HHMM_short_desc.py`.

## 3. Architecture des flux

### 3.1 Flux booking client (synchrone)

```
[Client]
   │ HTTP POST /booking/new/step-4-pay
   ▼
[BookingRouter]
   │
   ▼
[BookingService.confirm()]
   │
   ├──▶ CapacityService.check_and_lock(leg_id)
   │     └─▶ SELECT FOR UPDATE legs
   │
   ├──▶ PricingService.compute(items)
   │
   ├──▶ StripeService.create_payment_intent()
   │     └─▶ External call Stripe API
   │
   ├──▶ Booking.status = 'submitted'
   │     INSERT bookings row
   │
   ├──▶ NotificationService.dispatch('booking_submitted')
   │     ├─▶ Email client (template)
   │     └─▶ Email ops (template)
   │
   └──▶ ActivityLog.record(action='create_booking', entity_id=...)

[Client] ← HTTP 303 redirect /me/bookings/BK-... + cookie session
```

### 3.2 Flux décalage ETD (cascade)

```
[Staff édite leg]
   │ POST /planning/legs/{id}/edit
   ▼
[PlanningRouter]
   │
   ▼
[DatePropagationService.resequence_and_recalc(leg_id)]
   │
   ├──▶ Recalcule eta_eta des legs en aval
   ├──▶ Recalcule planned dates EscaleOperation
   ├──▶ Recalcule planned dates DockerShift
   ├──▶ Recalcule delivery dates Order
   ├──▶ Recalcule loading dates PackingListBatch
   ├──▶ Recalcule ETA bookings impactés
   │
   └──▶ DateShiftEventService.publish_all()
         ├─▶ Email clients bookings impactés
         ├─▶ Push PWA pont (commandant)
         └─▶ Ticket P2 auto si shift > 12h
```

### 3.3 Flux chatbot Kairos AI

```
[User input "Où est Anemos ?"]
   │ HTTP POST /chat/messages (HTMX, SSE)
   ▼
[ChatRouter]
   │
   ▼
[ChatbotService.respond(message, user)]
   │
   ├──▶ DetectInjection(message) — refuse si pattern
   │
   ├──▶ RAGService.retrieve(query) — pgvector top-5 chunks
   │
   ├──▶ Anthropic.create_message(
   │       system=system_prompt + rag_context,
   │       tools=[search_leg, search_escale, search_order, get_vessel_position, get_user_activity],
   │       cache_control={"type":"ephemeral"}  # prompt cache
   │     )
   │
   ├──▶ Pour chaque tool_use de Claude :
   │       └─▶ Wrapper qui vérifie require_permission(user, module, 'C')
   │           └─▶ Si OK : exécute la query SQL
   │           └─▶ Sinon : return {"error":"permission_denied"}
   │
   └──▶ Streame la réponse au client + log + cost tracking
```

### 3.4 Flux webhook Stripe

```
[Stripe envoie POST /webhooks/stripe]
   │ headers: stripe-signature
   ▼
[StripeWebhookRouter]
   │
   ├──▶ verify_signature(payload, signature, webhook_secret)
   │     └─▶ Refuser si signature invalide
   │
   ├──▶ Idempotency check via stripe_event_id
   │
   ├──▶ switch(event.type) :
   │     payment_intent.succeeded → Booking.confirm + Invoice.mark_paid
   │     payment_intent.payment_failed → notification client
   │     charge.refunded → Booking.cancel + Invoice.refund
   │
   └──▶ Record in stripe_events table
```

## 4. Décisions architecturales (ADR)

Chaque décision majeure est documentée dans `docs/architecture/adr/` :

- `ADR-001-postgres-only-no-warehouse.md`
- `ADR-002-htmx-no-react.md`
- `ADR-003-pwa-no-native.md`
- `ADR-004-stripe-checkout-hosted.md`
- `ADR-005-claude-sonnet-46-chatbot.md`
- `ADR-006-pgvector-for-rag.md`
- `ADR-007-mfa-mandatory-roles.md`
- `ADR-008-event-driven-cascade.md`
- `ADR-009-feature-flags-db-backed.md`
- `ADR-010-i18n-jinja-babel.md`

Format ADR (selon Michael Nygard) : Contexte, Décision, Conséquences,
Alternatives considérées.

## 5. Patterns appliqués

### 5.1 Repository pattern

Chaque module expose un repository asynchrone séparé de l'ORM :

```python
class BookingRepository:
    def __init__(self, db: AsyncSession): ...
    async def create(self, dto: BookingCreate) -> Booking: ...
    async def get(self, ref: str) -> Booking | None: ...
    async def list_by_client(self, client_id: int) -> Sequence[Booking]: ...
    async def update_status(self, ref: str, status: str) -> Booking: ...
```

### 5.2 Service layer

Une logique métier non triviale = un service dédié :

```python
class BookingService:
    def __init__(
        self,
        repo: BookingRepository,
        capacity: CapacityService,
        pricing: PricingService,
        stripe: StripeService,
        notif: NotificationService,
    ): ...

    async def confirm(self, ref: str, user: ClientAccount) -> Booking: ...
    async def cancel(self, ref: str, reason: str, user) -> Booking: ...
```

Services injectés via `Depends(get_booking_service)`.

### 5.3 DTO / Pydantic schemas

```
app/schemas/
├── booking.py  — BookingCreate, BookingRead, BookingItemCreate, ...
├── client.py   — ClientAccountCreate, ClientLogin, ...
├── leg.py      — LegPublic, LegStaff, ...
└── ...
```

### 5.4 Event-driven (in-process)

`app/events/` — petit bus interne :

```python
class DateShiftEvent(BaseModel):
    leg_id: int
    delta_hours: float
    impacted_bookings: list[int]

bus.publish(DateShiftEvent(...))
@bus.subscribe(DateShiftEvent)
async def on_date_shift(evt): ...
```

Permet de découpler les modules. Pour V3.1, migration possible vers
Redis Streams si besoin.

### 5.5 Idempotency

- Tous les POST mutants exposent un header optionnel
  `Idempotency-Key`.
- Stocké dans `idempotency_keys(key, scope, response_hash, created_at)`.
- Replay même clé = renvoie la réponse stockée.

## 6. Sécurité (rappel)

Cf. `docs/security/01-security-review.md`. Architecturalement :

- Middleware chain ordonné (CORS → Security → Maintenance → CSRF → ForcePwd).
- 2 contextes auth (staff vs client) sur 2 cookies séparés.
- Permissions au router + service.
- Audit en append-only avec hash chaîné.

## 7. Observabilité

### 7.1 Logging

- Format JSON structuré (loguru).
- Niveaux : DEBUG (dev), INFO (prod), WARNING, ERROR.
- Champs standards : `ts`, `level`, `request_id`, `user_id`, `module`,
  `event`, `payload`.
- Pas de PII dans les logs (masquage `auto_mask_pii`).

### 7.2 Métriques

Prometheus exporters :

- `http_requests_total` (par route, status)
- `http_request_duration_seconds` (histogram)
- `bookings_created_total` (par segment client)
- `chatbot_messages_total` (par modèle)
- `chatbot_cost_usd_total`
- `db_connections_active`

Dashboards Grafana : `dashboards/grafana/*.json`.

### 7.3 Tracing

OpenTelemetry SDK Python.

- Span auto pour FastAPI handler.
- Span manuel pour appels externes (Stripe, Anthropic, Windy).
- Sampling : 100 % en dev, 10 % en prod.
- Export OTLP vers Tempo (Grafana stack).

### 7.4 Alertes

| Alerte | Seuil | Notif |
|--------|-------|-------|
| Error rate > 1 % | 5 min | PagerDuty + Slack |
| p95 latence > 1 s | 5 min | Slack |
| DB connections > 80 % | 1 min | PagerDuty |
| Disk > 80 % | 5 min | Slack |
| Cert TLS < 14 j | quotidien | Email |
| Backup failed | immédiat | PagerDuty |

## 8. Déploiement

### 8.1 Pipeline CI/CD GitHub Actions

```
push branch → lint → tests → build image → push registry → deploy staging
PR merged main → tests → build image → push registry → deploy prod (manual approval)
```

### 8.2 Docker compose prod

```yaml
services:
  nginx: ...
  app:
    image: ghcr.io/newtowt/mynewtowt:${VERSION}
    deploy:
      replicas: 2
      resources: { limits: { memory: 1G, cpus: '1' } }
  worker:
    image: ghcr.io/newtowt/mynewtowt:${VERSION}
    command: celery -A app.worker worker
  db:
    image: postgres:16-alpine
    volumes: [pgdata:/var/lib/postgresql/data]
  metabase: ...
  grafana: ...
```

### 8.3 Zero-downtime deploy

- 2 replicas app derrière nginx.
- Migration Alembic backward-compatible obligatoire.
- Rolling update : 1 replica down → migrate → up → 2e replica.

### 8.4 DR (Disaster Recovery)

- RPO : 24 h (snapshots quotidiens chiffrés).
- RTO : 4 h (restore + reload images).
- Site DR : OVH Strasbourg en miroir si nécessaire (V3.1).

## 9. Capacité & scalabilité

### 9.1 Charge attendue (12 mois)

- 200 utilisateurs internes max.
- 5 000 clients enregistrés.
- 200 bookings/mois.
- 1 M+ requêtes HTTP/mois.

### 9.2 Bottlenecks identifiés

| Composant | Limite estimée | Plan upgrade |
|-----------|---------------|--------------|
| Postgres single instance | 5 000 RPS | Réplique lecture (V3.1) |
| FastAPI 2 workers | 200 RPS | Scale horizontal nginx upstream |
| Anthropic API quota | 200 EUR/mois | Augmenter cap si NPS chatbot bon |
| S3 PDF storage | illimité | OK |

## 10. Décommissionnement V2

Cf. `docs/strategy/01-deployment-plan.md` §5 pour la stratégie complète.
Architecturalement :

1. V2 reste en lecture seule pendant 90 j post-bascule.
2. Archives V2 transférées dans `archive_v2.*` schemas du nouveau PG.
3. Module passagers supprimé proprement.
4. URLs V2 redirigées 301 vers V3 équivalent.
