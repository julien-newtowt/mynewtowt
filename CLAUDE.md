# CLAUDE.md — `mynewtowt` Project Guide

## Vue d'ensemble

`mynewtowt` est la plateforme unifiée NEWTOWT (TransOceanic Wind
Transport) — pionnier du transport maritime décarboné à la voile depuis
2011. Version courante : **3.11.0**. La V3 combine en un seul outil :

- **L'ERP interne** des collaborateurs : planning (+ scénarios what-if),
  commercial, escale, cargo, équipage, finance, KPI, MRV, claims,
  captain/on board, carnet de bord ANEMOS.
- **La plateforme client** authentifiée : recherche de routes, réservation
  d'espace en cale, compte client (MFA), dashboard, factures,
  certificats CO₂ (label Anemos).
- **La vitrine publique marketing** (P3–P12) : landing, catalogue de
  routes, verticales B2B2C **café** / **cacao**, page **preuves** opposables
  + **vérification de certificats**, **carnet de construction** (blog + RSS),
  **kit presse**, tunnel **devis/leads**, contact, traçabilité consommateur
  **`/voyage/{ref}`**, taux de service, artefacts SEO (`robots.txt`,
  `llms.txt`, `sitemap.xml`, hreflang).
- **Le portail expéditeur** par token (`/p/{token}`) : packing list,
  messagerie sécurisée, documents, suivi.

> ⚠️ Facturation **fret** : NEWTOWT facture **par virement bancaire
> uniquement** (l'équipe commerciale confirme les bookings sous 4 h). Stripe
> avait été retiré en V3.1 de ce circuit.
>
> 💳 **Exception — « Vente à bord »** : Stripe est réintroduit de façon
> **ciblée** pour l'encaissement CB des collaborateurs embarqués (module
> `captain`, route `/captain/ventes`). Stripe **Checkout** (page hébergée,
> lien + QR) + **webhook** `/webhooks/stripe`. Secure-by-default : sans
> `STRIPE_SECRET_KEY`, la voie carte renvoie 503 et seule reste l'espèce.
> Aucun autre circuit de paiement n'est concerné.

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
| Météo | Windy → repli Open-Meteo |
| IA | Claude Sonnet 4.6 — **Newtowt Agent** (chatbot Kairos, prompt caching + tools ; RAG pgvector = backlog V3.1) ; couche IA veille |
| PDF | WeasyPrint |
| DOCX | `python-docx` (BL + offre commerciale) |
| Crew (lecture) | Marad / MaraSoft (sync read-only) |
| Reverse proxy / TLS | Caddy (Let's Encrypt auto) |
| Paiement | Fret : virement bancaire hors app. **Vente à bord** : Stripe Checkout + webhook (segno pour le QR), ciblé, secure-by-default |
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
│   ├── middlewares/           # security_headers, maintenance (toggle /tmp/.maintenance),
│   │                          # force_password (must_change_password), force_mfa (admin)
│   ├── models/                # SQLAlchemy 2 Mapped[]
│   ├── routers/               # 1 router par module (ERP + vitrine + API + PWA)
│   │                          # public/vitrine/voyage/devis/seo/carnet_bord/scenario/
│   │                          # marad/api_v1/pwa/notifications + modules ERP
│   ├── schemas/               # Pydantic DTO
│   ├── services/              # logique métier réutilisable (~90 services)
│   ├── utils/                 # file_validation, timezones, pipedrive
│   ├── templates/
│   │   ├── base.html          # squelette HTML, scripts, modal+toast containers
│   │   ├── staff/             # ERP interne (sidebar + topbar dédiés)
│   │   ├── client/            # plateforme client (sidebar + topbar dédiés)
│   │   ├── public/            # vitrine marketing (landing, routes, verticales,
│   │   │                      # preuves, presse, carnet, voyage, devis, contact)
│   │   ├── portal/            # /p/{token} (token-based, no auth)
│   │   ├── pdf/               # WeasyPrint BL/PL/invoice/CO2/carnet
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
- 9 rôles : `administrateur`, `operation`, `armement`, `technique`,
  `data_analyst`, `marins`, `commercial`, `manager_maritime`, `rh`.
- 17 modules : planning, commercial, escale, cargo, finance, kpi, captain,
  crew, claims, mrv, rh, booking, tickets, analytics, chat, veille, admin.
- Niveaux C / M / S = Consult / Modify / Suppress.
- Décorateur `Depends(require_permission("module", "C"|"M"|"S"))` sur
  toute route.
- **ARC-04 — overrides en base** : la matrice codée en dur `_MATRIX`
  (`permissions.py`) est la valeur PAR DÉFAUT ; des overrides par cellule
  (rôle × module) se posent en base (table `role_permissions`, écran
  `/admin/permissions`, cache 60 s). Le chemin requête consulte la matrice
  **effective** (défaut + overrides) et **fail-closed** : toute erreur DB
  retombe sur `_MATRIX`. La cellule `(administrateur, admin)` est verrouillée
  (l'admin ne peut jamais se couper de l'administration). Les helpers
  synchrones `has_permission`/`can_*` ne voient que `_MATRIX` (affichage/UI,
  pas contrôle d'accès).

### Reporting environnemental (MRV v2)
- **Grand livre unique — règle d'or** : `services/emission_ledger.py` est le
  **seul** endroit du code où une consommation est multipliée par un facteur
  d'émission (`emissions_breakdown`). Les autres services (`carbon`, `anemos`,
  `kpi_env`, `report_generation`) le consomment, ne recalculent jamais. Garde-fou :
  la sentinelle `tests/regression/test_factor_whitelist.py` échoue si un fichier
  hors `FACTOR_WHITELIST` référence un jeton de facteur (`3.206`,
  `ef_co2_kg_per_kg`…). `co2.estimate` (forfait 1,5/13,7) et `services/emissions`
  (NOx/SOx) restent les **comparateurs officiels**, pas des émissions réelles.
- **Convention d'unités** (jamais dévier) : masses en **tonnes**, volumes en **m³**,
  **compteurs carburant en litres bruts** machine, densité en **t/m³** (≡ kg/L,
  défaut 0,845), heures en `h`, distances `nm`, positions **décimales** (DMS calculé
  aux frontières OVDLA), **temps saisi local+tz → `datetime_utc` calculé** (non
  modifiable, jamais lu du payload). Colonnes suffixées `_t`/`_m3`/`_l`/`_h`/`_nm`.
- **Zéro seuil en dur** : tout seuil métier vit en base (`validation_rule_thresholds`,
  override par navire) et se résout via `validation_engine.get_threshold` — cache 60 s,
  **fail-closed** `(rule,vessel)` → `(rule,NULL)` → défaut codé. Snapshot des seuils
  consommés dans chaque `QualityCheckResult.details` (reproductibilité d'audit).
- **Cycle déclaratif** : le bord déclare des **événements** (`nav_events`) et des
  **soutages** (`bunker_operations`) ; TOUT le reste est dérivé (`inter_event_compute`,
  `emission_ledger`), jamais ressaisi. Machine à états `brouillon` (autosave, **auteur
  seul** — `DraftAuthorError`) → `finalise` (UTC autoritatif + moteur de règles scope
  `event` ; un `fail` **bloquant** refuse la finalisation) → `valide` (siège). Les
  brouillons sont **exclus** de tout calcul.
- **Feature flag `mrv_v2_capture`** (`services/feature_flags.capture_v2_enabled`) :
  **défaut ON global** (flag absent ⇒ actif), **fail-open** vers ON (une panne DB ne
  rouvre jamais le legacy), cache 20 s. Opt-out **par navire** en base via
  `audience.vessels_off` (codes/ids) pour le double-run pilote.

### Sécurité
- **CSRF** : `CSRFMiddleware` (double-submit cookie `towt_csrf`).
  HTMX injecte automatiquement le header via `csrf-htmx.js`.
- **CSP stricte** (cf. `security_headers.py`) — pas d'inline scripts ;
  ressources externes whitelistées (unpkg, fonts.gstatic, maptiler…).
- **Force-password-change** : `ForcePasswordChangeMiddleware` redirige
  toute requête HTML vers `/admin/my-account/change-password` quand
  `User.must_change_password = True`.
- **Force-MFA admin** : `ForceMfaForAdminMiddleware` redirige tout
  `administrateur` sans MFA activé vers `/admin/my-account/mfa`
  (toggle `REQUIRE_MFA_FOR_ADMIN`, à mettre `False` en dev local).
- **MFA** : WebAuthn + TOTP + **codes de récupération** à usage unique
  hachés (`mfa_recovery_codes`).
- **Détection de nouvel appareil** : `known_devices` (empreinte SHA-256
  UA + IP /24 ou /48, jamais en clair) → alerte email au login depuis un
  appareil inconnu ou à la désactivation MFA (`services.security_alerts`,
  no-op silencieux sans SMTP).
- **Rate limiting** persistant : `rate_limit_attempts` (scope + identifiant).
- **Audit trail** : `services.activity.record()` appelé sur tous les
  write actions. Table `activity_logs` append-only, viewer dans
  `/admin/activity-logs`.
- **Portail token** : `/p/{token}` sécurisé par UUID hex 24 car (90 j).
  Accès audité dans `portal_access_logs` (token jamais en clair —
  SHA-256 uniquement).
- **Tracking API** : `/api/tracking/upload` (X-API-Token) — public-mais-
  protégé pour Power Automate. Retourne 503 si `TRACKING_API_TOKEN`
  n'est pas configuré.
- **API publique v1** (`/api/v1/*`, read-only) : auth par header
  `X-API-Key` (`PUBLIC_API_KEY`) **secure-by-default** — renvoie 503 tant
  qu'aucune clé n'est provisionnée (SEC-06). `security.txt` exposé sur
  `/.well-known/security.txt`.
- **Crons externes** (Power Automate) protégés par token `X-API-Token`
  distinct, comparaison à temps constant : `WEATHER_API_TOKEN`,
  `VEILLE_API_TOKEN`, `MARAD_SYNC_TOKEN`, `MARAD_FLGO_TOKEN`,
  `TICKETS_SLA_API_TOKEN`, `QUOTE_FOLLOWUP_API_TOKEN`,
  `MRV_DRAFTS_API_TOKEN` (rappels brouillons R19, `POST /api/mrv/draft-reminders`),
  `MRV_QUALITY_API_TOKEN` (run nocturne qualité, `POST /api/mrv/quality-run`)
  (503 si non configuré ; 403/401 si token invalide).

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
| Planning — scénarios | `/planning/scenarios` | ✅ what-if isolé (jamais d'écriture sur `legs`) : brouillon ou clone de legs réels, Gantt/table/comparaison, export CSV, drag-drop |
| Commercial | `/commercial` | ✅ clients, grids, offers, orders |
| Cargo (packing list + portail) | `/cargo` + `/p/{token}` | ✅ batches + **audit consultable** + edit/suppr + lock + messagerie ; **BL reconnecté au batch** (n° `TUAW_…`), Arrival Notice, import/export Excel, portail multilingue |
| Escale (port call) | `/escale` | ✅ operations + dockers + lock |
| Onboard / Captain | `/captain` | ✅ SOF + ETA shifts + messagerie + docs + quart (watch log) + clôture escale (ONB-05) |
| Carnet de bord ANEMOS | `/carnet-bord` | ✅ éditeur staff (perm. `captain`) : highlights + photos par leg → preview HTML + PDF ; alimente la page publique `/voyage/{ref}` |
| Crew | `/crew` | ✅ bordées + compliance Schengen + calendar |
| Stowage | `/stowage` | ✅ 18 zones + algo glouton |
| Claims | `/claims` | ✅ workflow 6 statuts + timeline |
| MRV (reporting événementiel v2) | `/mrv` + `/onboard/events` | ✅ **architecture événementielle déclarative** : capture d'événements `/onboard/events` (Noon/Departure/Arrival/Begin-End Anchoring ; brouillon auteur-seul → finalisé → validé, `captain:M`) ; hub `/mrv` (`mrv:C`, actions `mrv:M`, seuils/facteurs `mrv:S`) : `voyages`, `reports` (Noon/Carbon/Stopover générés), `bunkering` (BDN), `flgo` (Marad lecture seule), `qualite` (moteur R01-R26 + IR01-IR05 + resets R10), `parametres` (seuils + dashboard params), `datasets` **OVDLA/OVDBR** (remplacent le CSV DNV 18 col.), `archive/events` (noon/MRVEvent legacy lecture seule). Grand livre unique `emission_ledger` multi-GES |
| Dashboard Performance Environnementale | `/dashboard-env` | ✅ 4 pages : **vue flotte** (`kpi:C`), **suivi opérationnel** navire→voyage→événements (`kpi:C` / `mrv:C` — ROB timeline, conso vs cible, répartition ME/AE, **profil de propulsion 4 h**, carte MapLibre), **qualité des données** (`mrv:C` — anomalies par règle/sévérité, resets R10, complétude), **administration** des paramètres (`mrv:S`) ; exports PDF/DOCX |
| Navigation | `/performance/navigation` | ✅ multi-legs/multi-navires : carte (1 couleur/leg) points GPS + trait + route théorique, tableau comparatif (réelle/théorique/écart/durée/restant), météo le long du trajet + blocs « conditions actuelles » par navire (rose des vents, anémomètre/Beaufort, pression, visibilité, T°…) |
| Finance | `/finance` | ✅ prévisionnel/réel 5 postes + écarts + export CSV + NOx/SOx évités + section Exploitation + détail assurance + CRUD OPEX |
| KPI | `/kpi` | ✅ vue KPI consolidée + Carbon Report par leg (intensités t·nm) ; **certificats CO₂ = label Anemos** (par booking + RSE annuel) |
| Booking (client) | `/booking/...` | ✅ wizard 3 étapes mobile-first **en session invité** (pas de mur d'inscription) : Route → Cargaison (IMDG + FDS si dangereux) → Récap + **autocréation du compte à la validation** (email existant → bascule connexion) ; relance **J+1** sur devis non converti (`/api/quotes/followup`) ; **instrumentation du tunnel** (`analytics_events` + funnel commercial) ; grille d'annulation COM-08 (0/25/50/100 %) |
| Tickets escale | `/tickets` | ✅ kanban + SLA P1/P2/P3 |
| Cashbox | `/cashbox` | ✅ EUR/USD/VND |
| Vente à bord | `/captain/ventes` | ✅ catalogue biens/services, inventaire par navire, ventes (espèces → caisse `vente_a_bord` ou CB → Stripe Checkout + QR), registre douanier détaxe (avitaillement/franchise) + export CSV. Webhook `/webhooks/stripe` (signature + idempotent). Perm. `captain` (marins → CM via override) |
| RH (SIRH) | `/rh` | ✅ congés marins + SIRH sédentaires : dossier/CRUD/import, contrats & avenants + alertes, congés/absences + self-service `/rh/moi`, EVP + verrouillage période, export Silae CSV + journal des lots, coffre-fort bulletins + entretiens + reporting RH (cf. `docs/strategy/CAHIER_DES_CHARGES_SIRH.md`) |
| Tracking flotte | `/tracking` | ✅ positions live + historique trajets (filtre navire × leg × période + trait reliant les points) |
| Tracking API | `/api/tracking/upload` | ✅ Power Automate compatible |
| Météo historisée | `/api/weather/refresh` | ✅ snapshot Windy du dernier point GPS / navire (cron 30 min, `WEATHER_API_TOKEN`) → `vessel_weather` |
| Chat Kairos AI (Newtowt Agent) | `/chat` | ✅ Claude Sonnet 4.6 (prompt caching + tools) ; toggle global via feature flag (`/admin`) |
| Veille d'actualité | `/veille` + `/api/veille/refresh` | ✅ flux NewsData.io (staff), refresh cron Power Automate + **couche IA** (score de pertinence affiné + digest quotidien, dégradation gracieuse sans clé → scoring heuristique) |
| Notifications | `/notifications` | ✅ flux staff (par user/rôle) + badge cloche topbar (toggle-read / archive) |
| Marad (crew) | `/api/marad/refresh` | ✅ sync **lecture seule** MaraSoft → `crew_members` (cron Power Automate, `MARAD_SYNC_TOKEN`), upsert idempotent |
| Admin | `/admin/...` | ✅ users + opex + insurance + maintenance + activity-logs + **permissions** (overrides ARC-04) + **co2** (facteur versionné) + **flotte-env** (référentiels cuves/moteurs par navire, `admin:C/M`) + **emission-factors** (facteurs multi-GES versionnés, `admin:C/M`) + feature flags |

### Vitrine publique marketing (P3–P12)

| Zone | Route racine | État |
|---|---|---|
| Landing / routes | `/` , `/routes`, `/routes/{leg_code}`, `/fleet` | ✅ storefront public + recherche de legs + suivi flotte |
| About / légal | `/about`, `/about/anemos`, `/about/legal`, `/about/privacy`, `/about/terms` | ✅ (301 legacy `/about/co2` → `/about/anemos`) |
| Verticales B2B2C | `/solutions/cafe`, `/solutions/cacao` | ✅ storytelling origine (café vert, cacao) à la voile |
| Preuves opposables | `/preuves` (+ `methodologie.pdf`, `rapport-annuel-exemple.pdf`) | ✅ méthodo + registre vérifiable (ENV-04) |
| Vérification certificat | `/verify`, `/verify/{cert_ref}` | ✅ certificats Anemos vérifiables |
| Kit presse | `/presse` (+ `logos.zip`, `dossier.pdf`) | ✅ dossier + logos |
| Carnet de construction | `/carnet`, `/carnet/{slug}`, `/carnet/rss.xml` | ✅ blog éditorial + RSS |
| Actualités | `/actualites`, `/actualites/rss.xml` | ✅ index news + RSS |
| Traçabilité consommateur | `/voyage/{ref}` (+ photos, brand-logo) | ✅ « histoire d'une cargaison » multilingue, rate-limité |
| Devis / leads | `/devis` (GET/POST), `/devis/{ref}`, `/devis/{ref}.pdf`, `/api/quotes/followup` | ✅ tunnel devis + PDF + relance J+1 (cron) |
| Contact | `/contact`, `/contact/merci` | ✅ lead → `COMMERCIAL_INBOX_EMAIL` + Pipedrive |
| Passagers (vitrine) | `/passagers` | ✅ page marketing service 2027 (pas d'ERP) |
| Recrutement / impact / flotte | `/recrutement`, `/impact`, `/flotte` | ✅ pages de marque |
| PWA « NEWTOWT Bord » | `/sw.js`, `/manifest.json` | ✅ offline IndexedDB + Background Sync |
| SEO | `/robots.txt`, `/llms.txt`, `/sitemap.xml` | ✅ artefacts crawler + IA + hreflang |
| API publique v1 | `/api/v1/*` | ✅ read-only B2B, `X-API-Key`, 503 sans clé (SEC-06) |

## Glossaire maritime

| Terme | Définition |
|---|---|
| **Leg** | Segment de voyage port A → port B |
| **leg_code** | Format `{vessel_code 1 chiffre}{rang année 1 lettre, A=1er}{dep_country}{arr_country}{year_digit}` (ex. `1CFRBR6` = navire 1, 3ᵉ voyage 2026, FR→BR). Rang = position chronologique par ETD dans l'année (renuméroté automatiquement) |
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
- **Ne jamais multiplier une consommation par un facteur d'émission hors
  `services/emission_ledger.py`** (règle d'or, sentinelle `test_factor_whitelist`).
- **Jamais de seuil métier MRV en littéral** — toujours `validation_engine.get_threshold`
  (paramétrable en base, override navire, fail-closed).
- Pas de **module ERP** passengers (disparu en v3.0.0 : pas de modèle, pas
  d'entrée dans la matrice de permissions). Mais le **service passagers 2027**
  est une **intention commerciale assumée** (P4) : page vitrine `/passagers`
  (12 couchettes/navire, champ `Vessel.capacity_pax`), sans logique ERP. Ne
  pas recréer de module ERP passagers ; ne pas dépublier la page marketing.

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
- **MRV v2 — démarrage à vide (Q1)** : aucune donnée historique importée en prod ;
  le dataset 2025 (`Sample_Dataset_Architecture_Evenementielle_2025.xlsx`) sert
  **uniquement** aux tests/staging (`scripts/import_mrv_2025.py`, jamais branché prod).
- **MRV v2 — CSV DNV retiré (Q3)** : le CSV DNV 18 colonnes est décommissionné
  (lot 14), **remplacé intégralement** par les datasets OVDLA/OVDBR ; le legacy 9 col.
  (code mort) a été purgé dès le lot 10.
- **MRV v2 — capture événementielle (Q6)** : les événements `/onboard/events`
  remplacent la saisie noon legacy ; double-run par navire (flag
  `mrv_v2_capture.audience.vessels_off`), ancien formulaire noon retiré en écriture.
- **MRV v2 — OVDLA (Q10)** : `Source_System = "MyTOWT"` ; **pas de lignes Noon**
  dans l'OVDLA (1 ligne/événement validé Departure/Arrival/Anchoring, valeurs en
  deltas entre événements).
- **MRV v2 — cargo MRV saisi (CDC v0.7, G10)** : `cargo_mrv_t` est saisi
  directement par le Master (calcul hydrostatique retiré, table
  `vessel_hydrostatics` supprimée) ; capacités officielles des cuves non
  fournies (Q11) → R23 volet capacités reste en sévérité **Info** (bascule
  Bloquant dès réception des plans).

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
6. Mailing notifications email (HTML + texte) : socle posé
   (`services.email`, alertes sécurité + relais leads) ; templates
   transactionnels riches restants.
7. ✅ Consolidation V3-only soldée : congés unifiés `/rh/conges` (EVO-02),
   veille IA (EVO-04), PWA offline réel IndexedDB + Background Sync (EVO-05).
8. ✅ **Vitrine marketing P3–P12** : conformité claims environnementaux
   (ECGT), preuves opposables + vérif certificats, verticales café/cacao,
   carnet de construction + RSS, kit presse & kit social B2B2C, taux de
   service publié, i18n stratégique (PT-BR en tête, hreflang honnête),
   tunnel devis + relance J+1, grilles multi-routes, comptes-ancres,
   rétroplanning médias.
9. ✅ **Intégration Marad (MaraSoft)** : sync crew lecture seule
   (`docs/integrations/marad-crew-readonly.md`, runbook
   `docs/operations/04-marad-crew-sync-runbook.md`).
10. ✅ **Scénarios de planning** what-if isolés + **API publique v1**
    read-only + **feature flags** (`role_permissions`, `feature_flags`).
11. ✅ **Refonte du reporting environnemental (MRV v2)** — architecture
    événementielle déclarative complète (14 lots, migrations 0096-0105) :
    référentiels navire + facteurs multi-GES versionnés, moteur de règles
    R01-R26 + IR01-IR05, capture d'événements + soutage BDN + FLGO Marad,
    rapports générés (Noon/Carbon/Stopover), grand livre unifié `emission_ledger`,
    datasets OVDLA/OVDBR, dashboard 4 pages, bascule + décommissionnement.
    Doc de référence : `docs/strategy/REGLES_GESTION_DONNEES_EMISSIONS.md` ;
    runbook : `docs/operations/05-mrv-evenementiel-runbook.md`.

Backlog MRV v2 (post-livraison, honnête) :
- **Écran admin d'audience des feature flags** : `mrv_v2_capture.audience.vessels_off`
  se pose aujourd'hui en SQL direct (pas d'UI) — constat du lot 14.
- **Hydrostatiques + capacités cuves officielles à charger** (Q11) : bascule le
  cargo MRV en calcul auto et R23 volet capacités en Bloquant.
- **Calibrage des 21 seuils provisoires** (`provisional=True`) après voyage pilote.
- **Sourcing formel CH₄/N₂O/WtT** (Q12) et **EF comparateurs dashboard** (Q15)
  avant tout usage en communication externe.
- **Distance OVDLA journalisée** : aujourd'hui haversine entre événements
  (amélioration lot 10 — distance loguée réelle à intégrer).

Backlog IA : RAG pgvector du Newtowt Agent sur `docs/`, streaming SSE
(V3.1) ; le chatbot tourne aujourd'hui en prompt caching + tools.
