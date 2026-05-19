# my_newtowt 🚢

Maritime operations management platform for [NEWTOWT](https://towt.eu) — TransOceanic Wind Transport (post-restructuration). Pioneer of decarbonised wind-powered shipping since 2011.

> *« On garde le cap. Une nouvelle traversée commence. »*

## Features

| Module | Description |
|--------|------------|
| **Planning** | Voyage legs, Gantt chart, distance/ETA calculation, CSV/PDF export |
| **Commercial** | Orders, client management, leg assignment, degressive pricing |
| **Cargo** | Packing lists, Bill of Lading, client portal, Excel import/export |
| **Escale** | Port calls, operations timeline, docker shifts, SOF |
| **On Board** | Statement of Facts, cargo documents, notifications |
| **Crew** | Members, leg assignments, annual calendar heatmap |
| **Finance** | OPEX, revenue, port costs, profitability analysis |
| **KPI** | CO₂ emissions, performance indicators |
| **MRV** | EU MRV emissions reporting |
| **Claims** | Cargo & crew incident management |
| **Admin** | Users, roles, vessels, ports, settings |

> **Passengers module** has been removed in v3.0.0 following the corporate restructuring (former passenger activity is no longer operated by NEWTOWT).

## Quick Start

```bash
# 1. Clone
git clone https://github.com/juliengonde-5G/mytowt.git
cd mytowt

# 2. Configure
cp .env.example .env
# Edit .env with your values (SECRET_KEY, DATABASE_URL, etc.)

# 3. Start
docker-compose up -d

# 4. Open
# http://localhost:8081 (dev) or http://51.178.59.174 (prod VPS OVH)
# Login: admin / towt2025
```

## Tech Stack

- **Backend**: FastAPI + SQLAlchemy async + PostgreSQL 16
- **Frontend**: Jinja2 + HTMX (no JS framework)
- **Auth**: Cookie sessions + bcrypt
- **Deploy**: Docker + docker-compose
- **Branding**: Manrope · DM Serif Display · NEWTOWT palette (teal/vert/cuivre/sable)

## Architecture

```
app/
├── models/      # SQLAlchemy models
├── routers/     # FastAPI routers (1 per module)
├── templates/   # Jinja2 templates
├── static/      # CSS design system + NEWTOWT logos
└── utils/       # Shared utilities
Design/         # NEWTOWT brand assets + design tokens (W3C draft)
docs/v2/        # Roadmap, audits, tech-debt
```

See [CLAUDE.md](CLAUDE.md) for detailed project documentation, and `Design/newtowt-design-tokens.json` for the canonical design system tokens.

## License

Private — NEWTOWT © 2026
