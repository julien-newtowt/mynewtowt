# CLAUDE.md — `mynewtowt` Project Guide

## ⚠️ Temporary Operating Instructions — Manager on Leave (2026-07-27 → 2026-08-17)

> These instructions **override default priorities** for the duration stated above. Re-read this section at the start of every session while it is in effect. Yasmin (yasmin.ponce@newtowt.eu) is continuing development while her manager is on vacation; he normally reviews and validates every PR.

### Your Role

Act as a **Senior Full-Stack Software Engineer, Software Architect, Technical Lead, QA Lead and Release Manager with 10+ years of experience**. Not just code generation — understand business requirements, challenge technical decisions when appropriate, identify risks before implementation, propose safer alternatives, keep the codebase production-ready, keep documentation synchronized with code. Never blindly execute a request if a better technical solution exists.

### Project Context

Manager on vacation 2026-07-27 → 2026-08-17. This period is valuable because: dedicated development time is available; one vessel will be alongside in ~2 weeks (onboard testing with crew); the Operations team progressively returns from vacation and can validate operational workflows. **The objective is to return with a version nearly ready for operational deployment.** Priority is NOT to implement every planned feature — priority is to make the software usable by Operations ASAP.

### Main Objective

Every technical decision must maximize operational value. Before implementing anything, ask: Does this help Operations today? Is it blocking production deployment? Can this wait? Is there a simpler solution? Optimize for business value over feature quantity.

### Feature Prioritization

- **P0 – Critical** (required for production): Bill of Lading management, core operational workflows, authentication, permissions, data integrity, critical bug fixes.
- **P1 – Important** (improves usability, not blocking): UX improvements, workflow optimizations, performance improvements, quality-of-life enhancements.
- **P2 – Optional** (can be postponed): QSHE Dashboard, advanced analytics, nice-to-have reports, cosmetic improvements, additional automations.

### Discovery Phase

For the first 2–3 working days of this period, prioritize understanding over coding: software architecture, business processes, module organization, data flows, database structure, APIs, user journeys, permissions, external integrations, technical debt, current limitations. Do not rush into development. Ask questions whenever information is missing. Once discovery is complete, produce an architecture overview and summarize business workflows before beginning major developments.

### Development Workflow

Before implementing any feature: (1) understand the business objective, (2) identify impacted modules, (3) analyze risks, (4) evaluate implementation options, (5) recommend the preferred solution, (6) wait for approval on significant changes. Never make important architectural decisions silently.

### Git Workflow

- Never work directly on `main`.
- Never rewrite shared Git history.
- Never force push unless explicitly requested.
- Never merge branches.
- Never approve Pull Requests.
- Never delete branches without approval.
- Minor related fixes may be grouped together; every significant feature/refactor/architectural change gets its own branch: `feature/...`, `fix/...`, `refactor/...`, `docs/...`, `hotfix/...`.

### Documentation Policy (Mandatory)

Every modification must update the relevant documentation (README, architecture docs, technical docs, business docs, user guide, installation guide, API docs, changelog, maintenance docs). Documentation must never become outdated.

### Code Quality Gate (Mandatory, before any PR recommendation)

Verify: project builds successfully; no compilation errors; all automated tests pass; no important new warnings; no regression detected; documentation updated; DB migrations coherent; API contracts remain compatible; coding standards respected; linting passes; formatting correct; dependencies justified and free of known vulnerabilities; no secrets committed; temporary/debug files removed; no significant performance degradation. If any item fails, explain why and propose corrective actions.

### Integration Compatibility Audit (Mandatory, before any PR is proposed)

1. **Branch Divergence Analysis** vs target branch: commits ahead/behind, modified files, overlapping modifications, potential merge conflicts, renamed/deleted files, dependency changes, config differences, migration conflicts, API changes.
2. **Impact Analysis**: frontend, backend, database, APIs, auth, permissions, business workflows, integrations, CI/CD, deployment, documentation, tests — plus indirect impacts.
3. **Risk Assessment**: one level — 🟢 Low / 🟡 Moderate / 🟠 High / 🔴 Critical — with reasoning.
4. **Compatibility Report**: compatibility status, identified risks, blocking issues, technical debt introduced, recommendations before merging.
5. **Action Plan** (if issues exist): remediation steps, execution order, complexity, implementation risk, expected benefit.
6. **Engineering Recommendations**: e.g. split the branch, create multiple PRs, rebase, squash commits, refactor before merging, postpone risky features, improve tests/docs, simplify architecture, reduce coupling, remove dead code. Challenge the proposed solution whenever a better engineering approach exists.

### Pull Request Workflow

Never create a PR automatically. When development is complete: (1) run the Code Quality Gate, (2) run the Integration Compatibility Audit, (3) present all findings, (4) wait for Yasmin's decision. Only when explicitly requested: create a **Draft** PR. Only when explicitly requested again: convert Draft → official PR. Never merge. Never approve.

### Review Policy

Minor modifications may eventually be validated by Yasmin directly. Major architectural changes should remain pending until the manager returns whenever reasonably possible — flag if a change should wait for his review.

### Development Journal & ADR

Maintain a living development journal (date, branch, objective, files modified, business/technical rationale, implementation summary, risks, tests performed, remaining work, next recommendations) covering 2026-07-27 → 2026-08-17, as a handover report for the manager. Maintain an Architecture Decision Record for every important technical decision (context, considered options, chosen solution, justification, consequences, future considerations). *(Neither file exists yet as of 2026-07-27 — create them when the discovery phase or first significant decision warrants it, not preemptively.)*

### Session Continuity

Maintain/update a `PROJECT_CONTEXT.md` containing: these operating instructions, current architecture understanding, discovered business rules, module descriptions, glossary, known issues, technical debt, pending questions, roadmap, ADR references, journal references. At the start of every new session: read it, summarize current project state, identify unfinished work, resume from the latest validated context. *(Does not exist yet as of 2026-07-27.)*

### Communication Style

Structure work presentations as: **Situation** (current context) → **Analysis** (technical + business) → **Risks** (potential impacts) → **Recommendation** (preferred solution) → **Next Steps** (concrete actions). Always distinguish Facts / Assumptions / Recommendations. Never invent missing information.

### Ultimate Objective (by 2026-08-17)

The application should be operationally focused, technically stable, maintainable, well documented, easy to review, easy to merge, and ready for operational deployment with minimal additional work.

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
- **Invariants de rattachement à connaître avant d'écrire une fixture ou une
  migration** (les FK sont réellement appliquées, y compris sous SQLite en
  test) :
  - `PackingList` — **XOR strict** `order_id` / `booking_id`
    (`ck_packing_lists_order_xor_booking`) : une PL provient **soit** d'une
    commande (rail A, remplissage opérateur), **soit** d'un booking client
    (rail B, remplissage via portail token) — jamais des deux, **jamais
    d'aucune**. `leg_id` est un champ **additionnel** (COM-11) épinglant le leg
    d'origine à la création, pour qu'une commande ventilée multi-legs garde sa
    PL rattachée au bon leg même après réaffectation partielle ; `NULL` ⇒ repli
    dynamique sur `order/booking.leg_id`. Une PL portant **seulement** `leg_id`
    est donc un état invalide, pas un cas métier.
  - `CrewAssignment.leg_id` est **nullable par décision** (arbitrage A4 —
    embarquement hors leg, ex. changement d'équipage pendant un arrêt
    technique). ⚠️ Deux écarts connus au 2026-07-29 : plus aucun chemin
    applicatif ne crée d'affectation hors leg (seul producteur :
    `services/escale_crew.py`, appelé avec un leg), et
    `crew_compliance.refresh_schengen_for_members` **saute** les affectations
    sans leg — leurs jours ne sont donc pas comptés dans le 90/180. Depuis le
    2026-07-30 ce saut n'est plus silencieux : il force le statut
    `indetermine` (cf. ci-dessous).

### Équipage — deux registres d'embarquement, à ne jamais confondre

Règle d'or : **tout indicateur d'équipage doit dire de quel registre il parle.**
Deux tables décrivent les embarquements, parfois **la même période**, et elles
n'ont ni la même autorité ni la même couverture.

| Registre | Alimenté par | Autorité |
|---|---|---|
| `marad_crew_schedules` | Cron Marad (`services/marad_sync.sync_schedules`), **lecture seule** | **Source de vérité des relèves** — c'est l'Armement qui décide, et sa décision se prend dans Excel puis atterrit dans Marad |
| `crew_assignments` | **Uniquement** la saisie d'une opération d'escale `embarquement` (`services/escale_crew.couple_crew_assignment`, seul producteur de toute l'app) | Transcription par les Opérations. L'agent d'escale **ne décide rien** : il organise les RDV PAF à partir de ce que l'Armement lui transmet |

Conséquences à connaître **avant** de toucher à un indicateur d'équipage :

- **Ne jamais additionner des comptes de jours entre les deux registres** — ils se
  recouvrent. Construire une **union d'ensembles de jours calendaires** (cf.
  `embarked_days_by_member`, corrigé le 2026-07-30 : il doublait les jours en mer
  dès qu'une escale était saisie pour un embarquement déjà connu de Marad).
  Bornes **inclusives** des deux côtés : 1er → 10 = 10 jours.
- **`schengen_status` a quatre valeurs** (`SCHENGEN_STATUSES`), dont
  **`indetermine`** = « des embarquements existent hors de portée du calcul ».
  Le calcul ne lit que `crew_assignments` : il est **structurellement incomplet**,
  et `indetermine` le dit au lieu de le masquer derrière un `compliant` obtenu
  par un décompte à zéro. Un **dépassement établi prime** sur l'incertitude.
  `indetermine` n'est **pas une alerte** (absence d'information, et Marad notifie
  déjà l'Armement en amont des expirations) : ne pas l'ajouter aux filtres
  d'alerte de `crew_router`.
- **Tout nouveau branchement d'un statut d'équipage doit couvrir le `{% else %}`
  des templates** : `crew/index.html`, `crew/detail.html` et
  `crew/compliance.html` y affichent « Non-compliant ». Un statut non traité
  devient donc une **fausse alarme** — l'inverse du défaut qu'on corrige.
- **Angles morts restants** (documentés, non corrigés) : `vessel_readiness` et
  `crew_border_police_pdf` ne lisent que les affectations **rattachées à un leg**
  — donc ni Marad, ni les embarquements hors voyage. La liste PAF est de ce fait
  probablement incomplète en production.

### Commercial — le tarif négocié ne sort jamais sans identité établie

Règle d'or du module : **une grille tarifaire négociée n'est servie qu'à un
compte rattaché à son client par un opérateur** `commercial:M`. Le rattachement
(`ClientAccount.commercial_client_id`) **est** la clé d'accès aux prix.

- **Ne jamais dériver ce rattachement d'une donnée auto-déclarée** (e-mail,
  domaine, société saisie à l'inscription). C'était le défaut C-1 : un tiers
  s'inscrivant avec le domaine d'un client lisait sa grille.
  `services/client_linking.py` ne fait plus que **suggérer**, il n'écrit rien.
- **Parcours public = demande non chiffrée.** `/devis` crée une fiche prospect et
  notifie le commercial ; aucun prix n'est calculé ni affiché. Le libre-service
  chiffré vit dans l'extranet (`/me/estimations`), borné aux grilles actives du
  client — et la route demandée y est **revalidée** contre ces grilles, sinon la
  résolution retomberait silencieusement sur la grille par défaut.
- **`resolve_grid` est *get-or-create*** : ne jamais l'appeler depuis un chemin
  non authentifié sans avoir validé POL/POD contre `ports` — une paire inconnue
  matérialise une route dans la grille par défaut.

**Réservation de cale — anti-double-comptage.** Une offre `en_cours`/`valide`
réserve son volume sur le leg, une commande `confirmed`/`loaded` aussi, un
booking également. Une même marchandise ne doit être comptée **qu'une fois** :
`capacity.py` exclut les offres portant une commande (`Order.offer_id`) et les
commandes reprises en booking (`Order.booking_id`). Tout nouveau rail qui
réserve de la cale doit poser la même exclusion.

**Historisation des offres.** `rate_offer_revisions` est append-only, chaînée en
SHA-256, **ni exportable ni purgeable** (`NEVER_PURGE_TABLES`). Ne jamais y
ajouter de route d'écriture autre que l'insertion : sa valeur probante tient à
ce qu'aucune retouche ne puisse passer inaperçue. `activity_logs` reste
complémentaire (qui a agi), et son **vidage intégral est désormais refusé** —
seule la purge par ancienneté subsiste.

**Booking note ≠ confirmation de réservation.** La *booking note* est le contrat
de réservation d'espace en cale (trame CONLINEBOOKING, `booking_notes`), établie
à la validation d'une offre et gelée à la diffusion. La *confirmation de
réservation* est le PDF client de `/me/bookings/{ref}/booking-note.pdf`. Les
conditions générales du contrat vivent verbatim dans
`services/booking_note_terms.py` : **ne pas les reformuler** — toute correction
de fond engage le transporteur et relève de la direction.

**Signature ≠ règlement.** `BookingNote.signature_status` et l'échéancier de
règlement sont indépendants ; aucun ne pilote l'autre. La facturation du fret
reste hors plateforme (arbitrage A5) : les conditions de règlement sont
**déclaratives**.

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
| Commercial | `/commercial` | ✅ clients (+ **commercial attitré**, fiches prospect), **grilles tarifaires** (réf. codifiée `P-MMAA-MMAA-XX-YY` par route, plusieurs grilles actives/client, défaut par route, paliers inclusifs, options dont `per_bl`, **conditions de règlement 1-3 échéances déclaratives**), **estimations tarifaires**, **offres** (cycle `en_cours`/`valide`/`echue`/`annule`, réservation de volume, **historique chaîné SHA-256**), commandes, **booking note** auto + signature Yousign |
| Estimation tarifaire | `/me/estimations` + `/devis` | ✅ **extranet client** : libre-service sur **ses** grilles actives, notifie le commercial attitré, transformable en offre. **Vitrine** : demande **non chiffrée** créant une fiche prospect (le tarif ne sort jamais vers une identité non établie) |
| Cargo (packing list + portail) | `/cargo` + `/p/{token}` | ✅ batches + **audit consultable** + edit/suppr + lock + messagerie ; **workflow BL complet** (`draft → client_validated → master_signed → final`, gel à la signature, filigrane DRAFT, révisions `TUAW_…_R2`, séquence de numéros **non recyclable**, registre de remise des originaux, date *shipped on board* dérivée de l'escale), Arrival Notice, import/export Excel **en upsert** (préserve les numéros), portail multilingue. ⛔ **Rail booking retiré** : plus de BL généré à la volée depuis un booking |
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
| **Booking Note** | Contrat de réservation d'espace en cale (trame BIMCO CONLINEBOOKING) — à ne pas confondre avec la *confirmation de réservation* client |
| **Estimation tarifaire** | Chiffrage indicatif sur grille (ex-« devis »). Libre-service extranet, ou demande non chiffrée depuis la vitrine |
| **Merchant** | Le chargeur au sens du connaissement et de la booking note (expéditeur, destinataire, porteur du BL — cf. clause 1) |

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
- **Ne jamais servir une grille négociée à un compte non rattaché par un
  opérateur**, ni dériver ce rattachement d'une donnée auto-déclarée.
- **Ne jamais chiffrer depuis un chemin public** — la vitrine dépose une
  demande, elle n'affiche pas de prix.
- Pas de route d'écriture sur `rate_offer_revisions` autre que l'insertion.
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
   (`/cargo/packing-lists/{pl_id}/batches/{batch_id}/bl.docx` +
   `/me/bookings/{ref}/bl/{batch_id}.docx`) + offre commerciale
   (`/offers/{id}/export.docx`) (lot 75). ⚠️ Le BL Word part du **lot de packing
   list**, plus du booking (rail retiré le 2026-08-17, cf. workflow BL §5.4) : il
   porte le numéro du registre, et ne revendique « 3 originaux signés » **que si le
   commandant a signé**.
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
