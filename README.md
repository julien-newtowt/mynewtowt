# NEWTOWT ERP — `mynewtowt`

Plateforme unifiée pour la compagnie NEWTOWT (transport maritime cargo
à la voile). Version courante : **3.11.0**. Combine en une seule application :

- L'**ERP interne** utilisé par les collaborateurs (planification +
  scénarios what-if, escale, cargo, équipage, finance, KPI, MRV, claims,
  RH, captain/on board, carnet de bord ANEMOS).
- Le **portail client** auto-administré offrant une **plateforme de
  réservation d'espace en cale**, le suivi documentaire, les rapports
  d'émissions CO₂ (label Anemos), le suivi de claims et la consultation
  des navigations.
- La **vitrine publique marketing** (`/`) : landing + catalogue de routes,
  verticales B2B2C **café** / **cacao**, page **preuves** opposables +
  **vérification de certificats**, **carnet de construction** (blog + RSS),
  **kit presse**, tunnel **devis/leads**, contact, traçabilité consommateur
  `/voyage/{ref}`, taux de service, artefacts SEO (`robots.txt`, `llms.txt`,
  `sitemap.xml`, hreflang).
- Le **portail expéditeur** par token (`/p/{token}`) : packing list,
  messagerie sécurisée, documents, suivi (sans authentification).
- Une **veille d'actualité** interne (`/veille`) agrégeant l'actualité du
  transport maritime, du transport à la voile, du Brésil et de la
  réglementation internationale, alimentée par l'agrégateur NewsData.io
  (couche IA de scoring + digest quotidien).

> ⚠️ Aucun paiement n'est traité par l'application (Stripe retiré en V3.1) :
> NEWTOWT facture par **virement bancaire** uniquement, l'équipe commerciale
> confirmant les bookings sous 4 h.

## Vision produit

Un seul outil, deux audiences :

| Audience | Usages |
|----------|--------|
| Collaborateurs NEWTOWT (9 rôles) | Pilotage opérationnel & décisionnel de la flotte |
| Clients / prospects | Réservation, suivi, documentation, reporting CO₂ |
| Grand public / presse / consommateurs | Vitrine, verticales B2B2C, preuves & certificats, traçabilité `/voyage/{ref}` |

## Démarrage rapide

```bash
docker compose up -d
docker compose exec app alembic upgrade head
open http://localhost:8000
```

Compte admin de démarrage : `admin` / mot de passe défini dans
`.env` via `INITIAL_ADMIN_PASSWORD`.

## Structure du dépôt

```
mynewtowt/
├── app/                  # application FastAPI
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── auth.py
│   ├── permissions.py
│   ├── csrf.py
│   ├── routers/          # 1 router par module
│   ├── models/           # SQLAlchemy async
│   ├── schemas/          # Pydantic DTO
│   ├── services/         # logique métier réutilisable
│   ├── templates/        # Jinja2 SSR (sidebar Kairos)
│   ├── static/           # design system Kairos
│   ├── middlewares/      # CSRF, sécurité, maintenance
│   ├── i18n/             # fr / en / es / pt-br / vi
│   └── utils/            # helpers
├── docs/
│   ├── strategy/         # roadmap, vision, SIRH, claims, continuité
│   ├── design/           # design handoff + tokens Kairos
│   ├── architecture/     # ADRs, flux
│   ├── security/         # security review + politiques
│   ├── operations/       # runbooks (oncall, veille, tracking/météo, Marad)
│   ├── integrations/     # Marad crew read-only, connecteurs externes
│   ├── personas/         # parcours utilisateur
│   ├── analytics/        # data strategy + dashboards
│   ├── booking/          # plateforme réservation cale
│   ├── i18n/             # audit traductions
│   ├── audit/            # audits repo / 360 + backlog par module
│   └── legacy/           # specs V2 archivées
├── migrations/           # Alembic
├── scripts/              # backup, seed, import
└── tests/                # unit / integration / e2e
```

## Documentation principale

- [`docs/strategy/00-vision.md`](docs/strategy/00-vision.md)
- [`docs/strategy/01-deployment-plan.md`](docs/strategy/01-deployment-plan.md)
- [`docs/design/01-design-handoff.md`](docs/design/01-design-handoff.md)
- [`docs/architecture/01-architecture.md`](docs/architecture/01-architecture.md)
- [`docs/booking/01-cale-booking-platform.md`](docs/booking/01-cale-booking-platform.md)
- [`docs/analytics/01-data-strategy.md`](docs/analytics/01-data-strategy.md)
- [`docs/security/01-security-review.md`](docs/security/01-security-review.md)
- [`docs/personas/01-personas.md`](docs/personas/01-personas.md)
- [`docs/vitrine-construction-plan.md`](docs/vitrine-construction-plan.md) — plan de construction de la vitrine publique
- [`docs/integrations/marad-crew-readonly.md`](docs/integrations/marad-crew-readonly.md) — intégration Marad (crew, lecture seule)
- [`docs/operations/01-runbook.md`](docs/operations/01-runbook.md)
- [`docs/operations/02-veille-runbook.md`](docs/operations/02-veille-runbook.md) — activation & exploitation de la veille d'actualité
- [`docs/operations/03-tracking-meteo-runbook.md`](docs/operations/03-tracking-meteo-runbook.md) — crons tracking + météo historisée
- [`docs/operations/04-marad-crew-sync-runbook.md`](docs/operations/04-marad-crew-sync-runbook.md) — cron de sync crew Marad

## Stack technique

| Couche | Choix |
|--------|-------|
| Backend | FastAPI 0.115 / Python 3.12 / Uvicorn |
| Base de données | PostgreSQL 16 + pgvector |
| ORM | SQLAlchemy 2 async + asyncpg |
| Migrations | Alembic |
| Front | HTMX 2 + Alpine.js + Jinja2 SSR + design system Kairos |
| Auth / MFA | itsdangerous + bcrypt + WebAuthn / TOTP + codes de récupération |
| Observabilité | OpenTelemetry + Prometheus + Sentry |
| Cartographie | MapLibre GL + MapTiler / Mapbox |
| Météo | Windy → repli Open-Meteo |
| IA | Claude Sonnet 4.6 — Newtowt Agent (prompt caching + tools ; RAG pgvector = backlog V3.1) + couche IA veille |
| PDF / DOCX | WeasyPrint + python-docx |
| Crew (lecture) | Marad / MaraSoft (sync read-only) |
| Reverse proxy / TLS | Caddy (Let's Encrypt auto) |
| Conteneurisation | Docker + docker-compose |

## Conventions

- **Commits** : conventional commits (`feat:`, `fix:`, `chore:`...).
- **Branches** : `feature/<module>-<court-desc>`, `fix/<court-desc>`.
- **PR** : template `.github/PULL_REQUEST_TEMPLATE.md`, review obligatoire.
- **Tests** : couverture > 80 % sur les services critiques.
- **Sécurité** : `/security-review` à chaque PR avant merge sur `main`.

## Licence

Propriété de NEWTOWT — usage interne et clients identifiés uniquement.
