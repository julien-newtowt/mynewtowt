# Journal de production — `mynewtowt` V3

> Étape 10 — Documentation intégrale du travail de production réalisé.
> Ce document récapitule chaque livrable, son emplacement, son lien
> avec les 10 étapes initiales de la commande, et la stratégie
> d'évolution.

## 0. Contexte

Production initiée le **18 mai 2026** sur la branche
`claude/newtowt-erp-development-0kOfg`.

Repository : `juliengonde-5g/mynewtowt`.

Objectif : refondre l'application TOWT V2 en `mynewtowt` V3, plateforme
unique combinant ERP interne et portail client avec **plateforme de
réservation d'espace en cale** (nouveauté différenciante).

## 1. Cartographie des 10 étapes ↔ livrables

| # | Étape de la commande | Livrable principal | Emplacement |
|---|---------------------|--------------------|-------------|
| 1 | Prise de connaissance | Vision produit + audit V2 | `docs/strategy/00-vision.md` |
| 2 | `/design-handoff` | Ajustement design system | `docs/design/01-design-handoff.md` + tokens CSS |
| 3 | `/writing-plans` + `/systematic-debugging` | Plan déploiement + playbook | `docs/strategy/01-deployment-plan.md` + `docs/operations/debugging-playbook.md` |
| 4 | `/verification-before-completion` | Stratégie zéro défaut | `docs/strategy/04-verification-before-completion.md` + CI |
| 5 | `/vendor-review` + plateforme cale | Booking platform | `docs/booking/01-cale-booking-platform.md` + code app |
| 6 | `/variance-analysis` + `/build-dashboard` | Data strategy | `docs/analytics/01-data-strategy.md` |
| 7 | `/security-review` | Politique sécurité renforcée | `docs/security/01-security-review.md` |
| 8 | `/process-optimization` | Optimisations continues | `docs/strategy/08-process-optimization.md` |
| 9 | `/architecture` + personas | Architecture évolutive | `docs/architecture/01-architecture.md` + `docs/personas/01-personas.md` |
| 10 | Documentation intégrale | Présent document | `docs/PRODUCTION-NOTEBOOK.md` |

## 2. Arborescence livrée

```
mynewtowt/
├── README.md                           # vue d'ensemble du dépôt
├── pyproject.toml                      # config Python/lint/tests
├── requirements.txt                    # deps runtime
├── requirements-dev.txt                # deps dev
├── Dockerfile                          # image conteneur
├── docker-compose.yml                  # stack locale
├── alembic.ini                         # config migrations
├── .env.example                        # gabarit env
├── .gitignore
├── .github/
│   ├── workflows/ci.yml                # pipeline GitHub Actions
│   └── PULL_REQUEST_TEMPLATE.md        # template PR
├── app/
│   ├── __init__.py
│   ├── main.py                         # entrée FastAPI
│   ├── config.py                       # settings avec garde-fous prod
│   ├── database.py                     # async SQLAlchemy + get_db()
│   ├── auth.py                         # staff + client auth
│   ├── permissions.py                  # RBAC matrice étendue
│   ├── csrf.py                         # double-submit CSRF
│   ├── templating.py                   # Jinja2 + filtres
│   ├── middlewares/
│   │   ├── __init__.py
│   │   └── security_headers.py         # CSP + HSTS + autres
│   ├── models/                         # 12 modèles SQLAlchemy
│   │   ├── __init__.py
│   │   ├── activity_log.py
│   │   ├── booking.py                  # Booking + BookingItem (cœur V3)
│   │   ├── client_account.py
│   │   ├── client_invoice.py
│   │   ├── co2_certificate.py
│   │   ├── feature_flag.py
│   │   ├── leg.py
│   │   ├── port.py
│   │   ├── rate_limit.py
│   │   ├── user.py
│   │   └── vessel.py
│   ├── schemas/                        # Pydantic DTO
│   │   ├── __init__.py
│   │   ├── booking.py
│   │   └── leg.py
│   ├── services/                       # logique métier réutilisable
│   │   ├── __init__.py
│   │   ├── activity.py
│   │   ├── booking.py                  # workflow + transitions
│   │   ├── capacity.py                 # check + lock + pessimistic
│   │   ├── co2.py                      # calcul CO₂ EU MRV
│   │   ├── feature_flags.py            # rollout progressif
│   │   └── pricing.py                  # tarification dynamique
│   ├── routers/                        # endpoints HTTP
│   │   ├── __init__.py
│   │   ├── api_v1_router.py            # API publique B2B
│   │   ├── booking_router.py           # wizard client 3 étapes
│   │   ├── client_auth_router.py
│   │   ├── client_dashboard_router.py
│   │   ├── public_router.py            # landing + routes + about
│   │   ├── staff_auth_router.py
│   │   ├── staff_booking_router.py     # backoffice booking
│   │   └── staff_dashboard_router.py
│   ├── templates/
│   │   ├── base.html
│   │   ├── public/                     # landing + about + routes
│   │   ├── client/                     # dashboard + booking wizard
│   │   ├── staff/                      # ERP interne
│   │   └── errors/                     # 403 + 404
│   └── static/css/
│       ├── tokens.css                  # design tokens Kairos
│       └── kairos.css                  # composants
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 20260518_0001_baseline.py   # schéma V3
├── scripts/
│   ├── __init__.py
│   └── seed_demo.py                    # 4 navires, 6 ports, 6 legs, 3 users
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_auth.py
│   │   ├── test_booking_workflow.py
│   │   ├── test_co2.py
│   │   ├── test_permissions.py
│   │   └── test_pricing.py
│   ├── integration/                    # à compléter au fil des PR
│   └── e2e/                            # à compléter au fil des PR
└── docs/
    ├── PRODUCTION-NOTEBOOK.md          # ← vous êtes ici
    ├── strategy/
    │   ├── 00-vision.md
    │   ├── 01-deployment-plan.md
    │   ├── 04-verification-before-completion.md
    │   └── 08-process-optimization.md
    ├── design/
    │   └── 01-design-handoff.md
    ├── architecture/
    │   └── 01-architecture.md
    ├── security/
    │   └── 01-security-review.md
    ├── booking/
    │   └── 01-cale-booking-platform.md
    ├── analytics/
    │   └── 01-data-strategy.md
    ├── personas/
    │   └── 01-personas.md
    └── operations/
        ├── 01-runbook.md
        ├── debugging-playbook.md
        └── defect-board.md
```

## 3. Récapitulatif des livrables fonctionnels

### 3.1 Plateforme client (NOUVEAU)

| Page | Route | Statut |
|------|-------|--------|
| Landing publique | `GET /` | ✅ implémenté |
| Recherche routes | `GET /routes` | ✅ |
| Détail leg | `GET /routes/{leg_code}` | ✅ |
| Pages institutionnelles | `GET /about*` | ✅ (5 pages) |
| Inscription client | `GET/POST /me/register` | ✅ |
| Login client | `GET/POST /me/login` | ✅ |
| Logout client | `GET /me/logout` | ✅ |
| Dashboard client | `GET /me` | ✅ avec KPIs CO₂ |
| Mes réservations | `GET /me/bookings` | ✅ |
| Détail réservation | `GET /me/bookings/{ref}` | ✅ |
| Mes factures | `GET /me/invoices` | ✅ |
| Mes certificats CO₂ | `GET /me/co2` | ✅ |
| Mon compte | `GET /me/account` | ✅ (RGPD self-service prévu) |

### 3.2 Booking wizard (NOUVEAU)

| Étape | Route | Statut |
|-------|-------|--------|
| Choix route | `GET /booking/new` | ✅ |
| Cargo | `GET /booking/new/{leg_code}` | ✅ |
| Cargo (submit) | `POST /booking/new/{leg_code}` | ✅ |
| Confirmation | `GET/POST /booking/{ref}/confirm` | ✅ |
| Done | `GET /booking/{ref}/done` | ✅ |

### 3.3 ERP staff (V3 réorganisé)

| Page | Route | Statut |
|------|-------|--------|
| Login staff | `GET/POST /login` | ✅ |
| Logout | `GET /logout` | ✅ |
| Dashboard | `GET /dashboard` | ✅ |
| Bookings backoffice | `GET /staff/bookings` | ✅ |
| Confirmer booking | `POST /staff/bookings/{ref}/confirm` | ✅ |
| Rejeter booking | `POST /staff/bookings/{ref}/reject` | ✅ |
| Planning / Cargo / Escale / Onboard / Crew / Finance / KPI / MRV / Claims / Tickets / Analytics | placeholders sidebar | 🟡 V3.1 |

### 3.4 API publique B2B (NOUVEAU)

| Endpoint | Statut |
|----------|--------|
| `GET /api/v1/health` | ✅ |
| `GET /api/v1/spec` | ✅ |
| `GET /api/v1/routes` | ✅ |
| `GET /api/v1/legs/{id}` | ✅ |
| `GET /api/v1/legs/{id}/capacity` | ✅ |
| `POST /api/v1/bookings` | 🟡 V3.1 (auth API key) |
| `POST /webhooks/stripe` | 🟡 V3.1 |

## 4. Architecture livrée

### 4.1 Modèles SQLAlchemy (12)

- **Identité** : `User`, `ClientAccount` (séparation staff/client).
- **Référentiels** : `Vessel`, `Port`.
- **Voyage** : `Leg` (avec champs booking : `is_bookable`, capacité, prix public).
- **Réservation** : `Booking`, `BookingItem` (cœur V3, workflow draft→delivered).
- **Documents** : `ClientInvoice`, `CO2Certificate`.
- **Plateforme** : `FeatureFlag`, `RateLimitAttempt`, `ActivityLog`.

### 4.2 Services métier (6)

- `capacity.py` — anti-double booking + verrou pessimiste.
- `booking.py` — workflow + transitions explicites.
- `pricing.py` — tarification dynamique (early-bird, last-seat, key-account).
- `co2.py` — calcul EU MRV avec facteurs publics.
- `feature_flags.py` — rollout progressif par rôle/segment/hash.
- `activity.py` — audit append-only.

### 4.3 Sécurité

- 2 cookies distincts staff/client (HMAC + itsdangerous).
- bcrypt pour les mots de passe.
- CSRF double-submit.
- CSP restrictive (uniquement Stripe + Mapbox + OSM + fonts).
- HSTS + autres headers durcis.
- RBAC matrice 8 rôles × 16 modules × 3 niveaux (C/M/S).
- Refus de démarrer en prod avec secrets faibles.

### 4.4 Tests (unit)

- `test_auth.py` (5 tests) — hashing, sessions, expiration.
- `test_permissions.py` (15+ tests) — matrice RBAC complète.
- `test_pricing.py` (6 tests) — tarification dynamique.
- `test_co2.py` (3 tests) — calcul CO₂ EU MRV.
- `test_booking_workflow.py` (15+ tests) — transitions de statut.

### 4.5 CI/CD

- Pipeline `.github/workflows/ci.yml` : lint + types + tests + sécurité + build.
- Couverture cible 80 %.
- Image Docker scannée (à activer trivy en V3.1).
- Pré-commit : ruff + black (à ajouter localement par chaque dev).

## 5. Décisions structurantes prises

### 5.1 Séparation staff / client

Décision : **cookies, modèles, layouts et services séparés** entre
collaborateurs et clients. Implication : pas de mélange de permissions,
templates dédiés, expérience adaptée.

Alternative écartée : un seul `User` polymorphe → trop d'effets de bord
sur les permissions et la SI client. Cf. `docs/architecture/adr/` (à
remplir au fil des décisions).

### 5.2 HTMX + Jinja2 (pas de SPA)

Décision : SSR Jinja2 + HTMX 2 pour l'interactivité. Pas de React/Vue.

Implications :

- Performance native (LCP < 1,5 s sans gros bundle JS).
- Accessibilité de base assurée.
- Stack maintenue par les mêmes profils backend (Python + HTML).

### 5.3 Postgres unique OLTP+OLAP

Décision : un seul Postgres avec schémas `public` (OLTP) et
`analytics` (OLAP), pas de Snowflake/BigQuery en V3.

Pourquoi : volume sub-1 M lignes/mois, équipe restreinte, time-to-value
priorisé sur scalabilité prématurée.

### 5.4 Chatbot Kairos AI sur Claude Sonnet 4.6

Décision : Anthropic Claude Sonnet 4.6 + prompt caching + RAG pgvector.

Pourquoi : qualité, prompt caching économique, tool use sécurisable
(permissions revalidées côté serveur, jamais déléguées au LLM).

### 5.5 MFA mandatory pour rôles sensibles

Décision : TOTP obligatoire pour `administrateur`, `manager_maritime`
et `commercial` à partir de S+10 (rollout progressif via feature flag
`mfa_required_admin`). WebAuthn optionnel.

### 5.6 Feature flags DB-backed

Décision : table `feature_flags` au lieu d'env vars → modification sans
redémarrage, audit, audience ciblable.

## 6. Conformité

| Référentiel | Implémentation |
|-------------|----------------|
| RGPD | Politique privacy + export self-service planifié + DPO contact |
| EU MRV | Service `co2.py` aligné facteurs + reporting annuel DNV CSV planifié |
| SOLAS / ISM / ISPS | Check-lists numérisées planifiées V3.1 (Onboard refonte) |
| PCI-DSS SAQ-A | Stripe Checkout hosted, aucune carte sur nos serveurs |
| eIDAS | Signature CGV via `signed_terms_version` + horodatage |

## 7. Performance & qualité

| Critère | Cible | Comment on mesure |
|---------|-------|-------------------|
| Lighthouse desktop | ≥ 90 | Lighthouse CI par PR |
| Lighthouse mobile | ≥ 85 | Lighthouse CI par PR |
| LCP | < 1,5 s sur 4G | RUM Sentry |
| INP | < 100 ms | RUM Sentry |
| Test coverage | ≥ 80 % | pytest-cov dans CI |
| 0 secret en clair | obligatoire | gitleaks dans CI |
| 0 vuln HIGH | obligatoire | trivy dans CI |
| MTBF | > 30 j | post-mortem analysis |

## 8. Roadmap V3.1 (S+14 → S+24)

| Sprint | Livrable |
|--------|----------|
| S+14 | Module Planning Gantt drag-and-drop + cascade |
| S+15 | Module Escale Import/Export split |
| S+16 | Module Onboard refonte 4 espaces + PWA |
| S+17 | Module Cargo (orders + packing list + BL) |
| S+18 | Module RH dédié (congés, Schengen, paie variable) |
| S+19 | Module Tickets escale (Kanban + SLA) |
| S+20 | Chatbot Kairos AI + RAG pgvector |
| S+21 | Stripe Checkout + facturation auto |
| S+22 | Dashboards Analytics (Metabase + variance) |
| S+23 | Migration data V2 → V3 (CDC + double-run) |
| S+24 | Bascule prod + formation utilisateurs |

## 9. Décisions à prendre (open questions)

| Question | Décideur | ETA |
|----------|----------|-----|
| Choix nom commercial (Kairos vs autre) | Direction | T0 + 14 j |
| Stratégie nom de domaine (`my.newtowt.eu` ou autre) | Direction | T0 + 7 j |
| Activation Stripe production | Finance + DSI | S+18 |
| Choix vérificateur EU MRV 2026 | RSE | S+12 |
| Sentry plan (gratuit team / paid) | DSI | S+8 |
| Doppler / Vault pour secrets | DSI | S+4 |

## 10. Comment continuer

### 10.1 Démarrage local

```bash
git clone <repo>
cd mynewtowt
cp .env.example .env
# éditer .env : SECRET_KEY (32+ chars) + DATABASE_URL
docker compose up -d
docker compose exec app alembic upgrade head
docker compose exec app python -m scripts.seed_demo
open http://localhost:8000
```

### 10.2 Création d'une feature

1. Créer branche `feature/<module>-<court-desc>`.
2. Lire la persona cible dans `docs/personas/01-personas.md`.
3. Suivre le golden path documenté.
4. Écrire test → écrire code → faire passer test (TDD léger).
5. PR avec template complet (cf. `.github/PULL_REQUEST_TEMPLATE.md`).
6. CI verte + 1 review + (si UI publique) screenshot.
7. Merge → auto-deploy staging.
8. 24-48 h de soak en staging.
9. Deploy prod (manual approval).

### 10.3 Création d'un nouveau module

Pattern à respecter :

```
app/
├── models/<module>.py
├── schemas/<module>.py
├── services/<module>.py
├── routers/<module>_router.py
├── templates/<module>/
└── tests/unit/test_<module>.py

migrations/versions/<date>_<module>.py
docs/<domain>/<module>.md
```

Inscrire le router dans `app/main.py`.
Vérifier que la permission `<module>` est dans `app/permissions.py`.
Ajouter une entrée sidebar dans `app/templates/staff/_layout.html`.
Ajouter un feature flag si déploiement progressif.

### 10.4 Création d'un nouveau dashboard

1. Définir le besoin métier (cf. `docs/analytics/01-data-strategy.md` §3).
2. Implémenter les agrégats côté DB (vues matérialisées ou dbt models).
3. Exposer via `/api/analytics/...`.
4. Construire la vue Jinja + composants Kairos.
5. Ajouter test E2E avec un seed dédié.

## 11. Points d'attention pour la suite

- **Données V2** : la migration sera la phase la plus risquée. Double-run
  CDC pendant 7 jours minimum avant bascule. Cf. `docs/strategy/01-deployment-plan.md` §5.
- **MFA imposé** : informer les utilisateurs 30 jours avant l'obligation
  (notification email + bandeau in-app).
- **CSP stricte** : tout nouveau service externe doit être ajouté
  explicitement dans `app/middlewares/security_headers.py`.
- **Audit trimestriel** : revue des comptes inactifs, permissions,
  API keys.

## 12. Métriques à surveiller dès le J+1 de production

| Métrique | Seuil alerte | Owner |
|----------|--------------|-------|
| Error rate global | > 1 % | On-call |
| p95 latence | > 1 s | On-call |
| /me/login fail rate | > 5 % | Sécurité |
| Booking create error | > 2 % | Commercial |
| Chatbot cost | > 80 % budget | Direction |
| Disk usage | > 85 % | Ops |
| DB connections | > 80 % pool | Ops |

## 13. Remerciements

Cette V3 reprend la base solide construite sur l'application TOWT V2
documentée dans `Versions TOWT/`. Les choix d'architecture, le glossaire
maritime, le design system Kairos et la matrice de permissions sont
hérités et étendus.

Architecte : équipe Claude Code + équipe interne NEWTOWT.
Date de livraison initiale : 18 mai 2026.
Documentation tenue à jour à chaque release.
