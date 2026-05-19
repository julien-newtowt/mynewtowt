# NEWTOWT ERP — `mynewtowt`

Plateforme ERP intégrée pour la compagnie NEWTOWT (transport maritime cargo
à la voile). Combine en une seule application :

- L'**ERP interne** utilisé par les collaborateurs (planification, escale,
  cargo, équipage, finance, KPI, MRV, claims, RH).
- Le **portail client** auto-administré offrant pour la première fois une
  **plateforme de réservation d'espace en cale**, le suivi documentaire,
  les rapports d'émissions CO₂, le suivi de claims et la consultation
  des navigations.

## Vision produit

Un seul outil, deux audiences :

| Audience | Usages |
|----------|--------|
| Collaborateurs NEWTOWT (8 profils) | Pilotage opérationnel & décisionnel de la flotte |
| Clients / prospects | Réservation, suivi, documentation, reporting CO₂, paiement |

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
│   ├── strategy/         # roadmap, vision, livraison
│   ├── design/           # design handoff + tokens Kairos
│   ├── architecture/     # ADRs, flux, personas
│   ├── security/         # security review + politiques
│   ├── deployment/       # staging → prod, debugging
│   ├── personas/         # parcours utilisateur
│   ├── analytics/        # data strategy + dashboards
│   ├── booking/          # plateforme réservation cale
│   ├── api/              # OpenAPI + webhooks
│   └── operations/       # runbook, oncall
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
- [`docs/operations/01-runbook.md`](docs/operations/01-runbook.md)

## Stack technique

| Couche | Choix |
|--------|-------|
| Backend | FastAPI 0.115 / Python 3.12 / Uvicorn |
| Base de données | PostgreSQL 16 + pgvector |
| ORM | SQLAlchemy 2 async + asyncpg |
| Migrations | Alembic |
| Front | HTMX 2 + Alpine.js + Jinja2 SSR + design system Kairos |
| Auth | itsdangerous + bcrypt + WebAuthn / TOTP |
| Observabilité | OpenTelemetry + Prometheus + Sentry |
| Cartographie | MapLibre + Mapbox tiles |
| Météo | Windy / OpenWeather |
| IA | Claude Sonnet 4.6 (chatbot Kairos AI + RAG pgvector) |
| Conteneurisation | Docker + docker-compose |

## Conventions

- **Commits** : conventional commits (`feat:`, `fix:`, `chore:`...).
- **Branches** : `feature/<module>-<court-desc>`, `fix/<court-desc>`.
- **PR** : template `.github/PULL_REQUEST_TEMPLATE.md`, review obligatoire.
- **Tests** : couverture > 80 % sur les services critiques.
- **Sécurité** : `/security-review` à chaque PR avant merge sur `main`.

## Licence

Propriété de NEWTOWT — usage interne et clients identifiés uniquement.
