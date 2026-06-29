# CLAUDE.md — `mynewtowt` Project Guide

## Vue d'ensemble

`mynewtowt` est la plateforme unifiée NEWTOWT (TransOceanic Wind
Transport) — pionnier du transport maritime décarboné à la voile depuis
2011. La V3 combine en un seul outil :

- **L'ERP interne** des collaborateurs : planning, commercial, escale,
  cargo, équipage, finance, KPI, MRV, claims, captain/on board.
- **La plateforme client** publique : recherche de routes, réservation
  d'espace en cale, compte client authentifié (MFA), dashboard, factures,
  certificats CO₂.
- **Le portail expéditeur** par token (`/p/{token}`) : packing list,
  messagerie sécurisée, documents, suivi.

## Stack technique

| Couche | Choix |
|---|---|
| Backend | FastAPI 0.115 / Python 3.12 / Uvicorn |
| DB | PostgreSQL 16 + asyncpg via SQLAlchemy 2 async (`Mapped[]`) |
| Migrations | Alembic |
| Front | HTMX 2 + Alpine.js (light) + Jinja2 SSR + design system Kairos |
| Icons | Lucide CDN |
| Auth | Cookies signés (itsdangerous) + bcrypt + MFA WebAuthn / TOTP |
| Observabilité | OpenTelemetry + Prometheus + Sentry |
| Carto | MapLibre GL + Mapbox / MapTiler |
| Météo | Windy / OpenWeather |
| IA | Claude Sonnet 4.6 (chatbot Kairos AI) |
| PDF | WeasyPrint |
| Containers | Docker + docker-compose |

## Identité visuelle — charte « Nouvelle Étoile »

Source de vérité : `docs/design/newtowt-design-tokens.json`. Tokens
exposés à toutes les pages via `app/static/css/tokens.css`.

| Couleur | Code | Variable | Ratio |
|---|---|---|---|
| Teal NEWTOWT | `#0D5966` | `--teal` | 60 % (dominante) |
| Vert NEWTOWT | `#87BD29` | `--vert` | 20 % (succès, baseline) |
| Cuivre NEWTOWT | `#B47148` | `--cuivre` | 10 % (signal transition) |
| Sable NEWTOWT | `#EFE6D6` | `--sable` | 10 % (fond éditorial) |

**Polices** : Manrope (UI/print), DM Serif Display (accents), JetBrains
Mono (codes leg, MMSI, IMO).

## Structure du dépôt

```
mynewtowt/
├── app/
│   ├── main.py                # FastAPI entrypoint, middlewares, routers
│   ├── config.py              # pydantic-settings (.env)
│   ├── database.py            # async engine, get_db()
│   ├── auth.py                # bcrypt + itsdangerous (staff + client)
│   ├── permissions.py         # matrice rôles × modules × {C,M,S}
│   ├── csrf.py                # double-submit cookie CSRF
│   ├── templating.py          # Jinja2 env, filtres (money/date/datetime/flag), globals (t, brand)
│   ├── i18n/                  # 5 catalogues (fr, en, es, pt-br, vi)
│   ├── middlewares/
│   │   ├── security_headers.py
│   │   ├── maintenance.py     # toggle via /tmp/.maintenance
│   │   └── force_password.py  # User.must_change_password redirection
│   ├── models/                # SQLAlchemy 2 Mapped[]
│   ├── routers/               # 1 router par module (ou packagé dans modules_router)
│   ├── schemas/               # Pydantic DTO
│   ├── services/              # logique métier réutilisable
│   ├── utils/                 # file_validation, timezones, pipedrive
│   ├── templates/
│   │   ├── base.html          # squelette HTML, scripts, modal+toast containers
│   │   ├── staff/             # ERP interne (sidebar + topbar dédiés)
│   │   ├── client/            # plateforme client (sidebar + topbar dédiés)
│   │   ├── public/            # marketing landing + routes catalog
│   │   ├── portal/            # /p/{token} (token-based, no auth)
│   │   ├── pdf/               # WeasyPrint BL/PL/invoice/CO2
│   │   └── errors/            # 404/403
│   └── static/
│       ├── css/tokens.css     # design tokens W3C
│       ├── css/kairos.css     # composants + utilitaires Kairos
│       ├── js/                # toast, modal, sidebar, clock, towt-tz, csrf-htmx
│       └── img/               # logos NEWTOWT compose
├── docs/                      # vision, runbook, ADR, design handoff
├── migrations/                # Alembic
├── scripts/                   # backup, seed, import
├── tests/                     # pytest (unit + integration)
└── docs/legacy/               # specs V2 archivées (captain, ux, v2)
```

## Patterns critiques

### Base de données
- Session via `get_db()` — auto-commit on success / rollback on exception.
- Utiliser `await db.flush()` pour matérialiser INSERT/UPDATE ; **jamais
  `await db.commit()`** dans une route (géré par la dependency).
- Schéma init via `Base.metadata.create_all` au boot (dev) ; production
  utilise Alembic exclusivement.

### Routes
- Mutations : `validate → modify → await db.flush() → RedirectResponse(303)`.
- Détection HTMX : `request.headers.get("hx-request")` → renvoyer header
  `HX-Redirect`.

### Permissions
- 8 rôles : `administrateur`, `operation`, `armement`, `technique`,
  `data_analyst`, `marins`, `commercial`, `manager_maritime`.
- 16 modules : planning, commercial, escale, cargo, finance, kpi, captain,
  crew, claims, mrv, rh, booking, tickets, analytics, chat, admin.
- Niveaux C / M / S = Consult / Modify / Suppress.
- Décorateur `Depends(require_permission("module", "C"|"M"|"S"))` sur
  toute route.

### Sécurité
- **CSRF** : `CSRFMiddleware` (double-submit cookie `towt_csrf`).
  HTMX injecte automatiquement le header via `csrf-htmx.js`.
- **CSP stricte** (cf. `security_headers.py`) — pas d'inline scripts ;
  ressources externes whitelistées (unpkg, fonts.gstatic, maptiler…).
- **Force-password-change** : `ForcePasswordChangeMiddleware` redirige
  toute requête HTML vers `/admin/my-account/change-password` quand
  `User.must_change_password = True`.
- **Audit trail** : `services.activity.record()` appelé sur tous les
  write actions. Table `activity_logs` append-only, viewer dans
  `/admin/activity-logs`.
- **Portail token** : `/p/{token}` sécurisé par UUID hex 24 car (90 j).
  Accès audité dans `portal_access_logs` (token jamais en clair —
  SHA-256 uniquement).
- **Tracking API** : `/api/tracking/upload` (X-API-Token) — public-mais-
  protégé pour Power Automate. Retourne 503 si `TRACKING_API_TOKEN`
  n'est pas configuré.

### Templates
- Tous étendent `base.html` puis un layout par audience (`staff/_layout`,
  `client/_layout`, `portal/_layout`, `public/_layout`).
- Composants riches dans `kairos.css` : `.card`, `.btn`, `.pill`, `.badge`,
  `.alert`, `.kpi-card` / `.stat-card`, `.vessel-tabs`, `.year-selector`,
  `.leg-chip`, `.leg-summary`, `.vessel-status-badge`, `.bordee-grid`,
  `.dash-notif-card`, `.progress-bar`, `.toast`, `.modal-card`,
  `.sidebar-clock`, `.sidebar-userbadge`, `.port-badge`.
- Filtre Jinja `|flag` : code pays ISO 2 → emoji drapeau.
- Filtre Jinja `|money` : Decimal → "1 234,56 EUR" avec séparateur.
- Helper Jinja `t(key, lang)` : i18n inline.

### Forms
- HTML standard `<form method="POST">`, action vers route relative.
- `forms.js` désactive le bouton submit 5 s après clic (anti-double-submit).
- `towt-tz.js` gère la conversion timezone pour `.tz-input-wrap` avec
  `.tz-select`.

## Domaines fonctionnels

| Module | Route racine | État |
|---|---|---|
| Planning | `/planning` | ✅ Gantt + table + share token |
| Commercial | `/commercial` | ✅ clients, grids, offers, orders |
| Cargo (packing list + portail) | `/cargo` + `/p/{token}` | ✅ batches + **audit consultable** + edit/suppr + lock + messagerie ; **BL reconnecté au batch** (n° `TUAW_…`), Arrival Notice, import/export Excel, portail multilingue |
| Escale (port call) | `/escale` | ✅ operations + dockers + lock |
| Onboard / Captain | `/captain` | ✅ SOF + ETA shifts + messagerie + docs |
| Crew | `/crew` | ✅ bordées + compliance Schengen + calendar |
| Stowage | `/stowage` | ✅ 18 zones + algo glouton |
| Claims | `/claims` | ✅ workflow 6 statuts + timeline |
| MRV | `/mrv` | ✅ events fuel + exports DNV CSV + Carbon Report |
| Navigation | `/performance/navigation` | ✅ multi-legs/multi-navires : carte (1 couleur/leg) points GPS + trait + route théorique, tableau comparatif (réelle/théorique/écart/durée/restant), météo le long du trajet + blocs « conditions actuelles » par navire (rose des vents, anémomètre/Beaufort, pression, visibilité, T°…) |
| Finance | `/finance` | ✅ prévisionnel/réel 5 postes + écarts + export CSV + NOx/SOx évités + section Exploitation + détail assurance + CRUD OPEX |
| KPI | `/kpi` | ✅ vue KPI consolidée + Carbon Report par leg (intensités t·nm) ; **certificats CO₂ = label Anemos** (par booking + RSE annuel) |
| Booking (client) | `/booking/...` | ✅ wizard 3 étapes |
| Tickets escale | `/tickets` | ✅ kanban + SLA P1/P2/P3 |
| Cashbox | `/cashbox` | ✅ EUR/USD/VND |
| RH (SIRH) | `/rh` | ✅ congés marins + SIRH sédentaires : dossier/CRUD/import, contrats & avenants + alertes, congés/absences + self-service `/rh/moi`, EVP + verrouillage période, export Silae CSV + journal des lots, coffre-fort bulletins + entretiens + reporting RH (cf. `docs/strategy/CAHIER_DES_CHARGES_SIRH.md`) |
| Tracking flotte | `/tracking` | ✅ positions live + historique trajets (filtre navire × leg × période + trait reliant les points) |
| Tracking API | `/api/tracking/upload` | ✅ Power Automate compatible |
| Météo historisée | `/api/weather/refresh` | ✅ snapshot Windy du dernier point GPS / navire (cron 30 min, `WEATHER_API_TOKEN`) → `vessel_weather` |
| Chat Kairos AI | `/chat` | ✅ Claude Sonnet 4.6 |
| Veille d'actualité | `/veille` + `/api/veille/refresh` | ✅ flux NewsData.io (staff), refresh cron Power Automate + **couche IA** (score de pertinence affiné + digest quotidien, dégradation gracieuse sans clé → scoring heuristique) |
| Admin | `/admin/...` | ✅ users + opex + insurance + maintenance + activity-logs |

## Glossaire maritime

| Terme | Définition |
|---|---|
| **Leg** | Segment de voyage port A → port B |
| **leg_code** | Format `{seq}{vessel_code}{dep_country}{arr_country}{year_digit}` (ex. `1CFRBR6`) |
| **ETD / ETA** | Estimated Time of Departure / Arrival |
| **ATD / ATA** | Actual Time of Departure / Arrival |
| **Escale** | Période où le navire est à quai |
| **SOF** | Statement of Facts (chronologie portuaire) |
| **BL / BOL** | Bill of Lading (titre de propriété cargo) |
| **POL / POD** | Port of Loading / Discharge |
| **LOCODE** | Code UN port (5 caractères, ex. `FRFEC` = Fécamp) |
| **OPEX** | Operating Expenditure (coût journalier d'exploitation) |
| **EOSP / SOSP** | End / Start Of Sea Passage |
| **MRV** | Monitoring, Reporting, Verification (réglementation UE émissions) |
| **MDO** | Marine Diesel Oil |
| **ROB** | Remaining On Board (fuel restant) |
| **Schengen** | Statut immigration marin étranger (90 jours / 180) |

## Conventions

| Commit type | Usage |
|---|---|
| `feat:` | Nouvelle fonctionnalité |
| `fix:` | Correction de bug |
| `chore:` | Refactor / nettoyage |
| `docs:` | Documentation |
| `test:` | Ajout/modif tests |

- Branches : `feature/<module>-<court-desc>`, `fix/<court-desc>`.
- PR template `.github/PULL_REQUEST_TEMPLATE.md`, review obligatoire.
- Tests `pytest -q` (env de dev : Postgres + asyncpg).
- Sécurité : `/security-review` à chaque PR avant merge sur `main`.

## Do / Don't

**DO :**
- `await db.flush()` dans les routes (pas `commit`).
- Utiliser `services.activity.record()` pour tracer les write actions.
- `require_permission()` sur chaque endpoint protégé.
- `flush+RedirectResponse(303)` après mutation.
- Préférer les classes CSS Kairos aux inline styles.

**DON'T :**
- Pas de `await db.commit()` dans les routes.
- Pas de `<script>` inline (CSP-strict — utiliser un fichier externe).
- Pas de f-string SQL pour des noms de table/colonne — whitelist + `bindparams()`.
- Pas de framework JS lourd — HTMX + Alpine.js sont la norme.
- Pas de police `Inter`, `Poppins`, `Segoe UI` — uniquement Manrope.
- Pas de module passengers — disparu en v3.0.0 (restructuration corporate).

## Décisions actées & ré-absorptions (à ne pas recompter comme régressions)

Source : `docs/audit/backlog/ARBITRAGES.md` (tranché 2026-06-22) + reprise V2→V3.

- **Cargo facturation hors plateforme (A5)** : `/me/invoices` = page explicite ;
  modèle `ClientInvoice` inactif (le service `invoicing` ne sert qu'au calcul
  des montants booking/Anemos).
- **Certificats CO₂ = label Anemos** (par booking + RSE annuel), pas un PDF
  nominatif par client.
- **Insurance n'est PAS V3-only** : module repris/enrichi (détail
  provision/indemnité/franchise au KPI).
- **Congés marins migrés crew → RH** (séparation des permissions `crew` ↔ `rh`).
- **Suppression utilisateur = désactivation** (`is_active`).
- **Facteur CO₂ versionné** (`/admin/co2`) ; NOx/SOx ré-exposés (A7, accès ciblé
  `data_analyst` + `administrateur`, sans module `admin` global).
- **MRV hybride (A1)** : noon auto + compteurs DO de contrôle.
- **Stowage (A3)** : « avertir » par défaut + blocage dur configurable par zone.
- **Crew (A4)** : embarquement hors leg autorisé (`leg_id` nullable).

## Roadmap & backlog

Voir `docs/strategy/NOTE_TECHNIQUE_CONTINUITE_OPERATIONNELLE.md` (Plan
de Continuité d'Activité) et `docs/audit/ETUDE_COMPARATIVE_BRANCHES_VS_MAIN.md`
(état branches + plan de rattrapage).

Backlog actif :
1. Certificats CO₂ : couverts par le **label Anemos** (PDF WeasyPrint par booking).
2. ✅ DOCX generators : service `docx_generator.py` — Bill of Lading
   (`/cargo/booking/{ref}/bl.docx` + `/me/bookings/{ref}/bl.docx`) + offre
   commerciale (`/offers/{id}/export.docx`) (lot 75).
3. ✅ Stowage visualisation : vue SVG top-down des navires (STO-10, lot 72).
4. ✅ Exports admin : ZIP global + CSV sélectif par table whitelistée
   (ADM-04, `admin_data.py`).
5. ✅ Purges DB ciblées : whitelist `ALLOWED_PURGE_TABLES` + DELETE paramétré
   (expression SQLAlchemy, jamais de f-string) + **purge par rétention**
   (lignes plus anciennes que N jours, colonne d'horodatage whitelistée — lot 76).
6. Mailing notifications email (HTML + texte).
7. ✅ Consolidation V3-only soldée : congés unifiés `/rh/conges` (EVO-02),
   veille IA (EVO-04), PWA offline réel IndexedDB + Background Sync (EVO-05).
