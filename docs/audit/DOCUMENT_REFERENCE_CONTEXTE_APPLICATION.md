# Document de référence — Contexte & comportements de l'application `mynewtowt`

> **Objet :** fournir, en un seul document, l'intégralité du **contexte** et des
> **comportements** de la plateforme NEWTOWT V3 (`mynewtowt`), de façon à pouvoir
> (a) **assurer la continuité du développement** sans relire tout le code, et
> (b) **alimenter d'autres outils d'analyse** (IA, audit, revue) avec un contexte
> complet, fidèle et autoportant.
> **Périmètre :** code de la branche `claude/email-branches-audit-5uw0b2` au
> 2026‑06‑29. **Nature :** documentation factuelle dérivée du code (routers,
> modèles, services, middlewares, config, templates, i18n, tests).
> **À lire en complément :** `CLAUDE.md` (guide projet) et
> `docs/audit/AUDIT_V2_V3_RAPPORT_ECARTS_ET_PLAN.md` (écarts V2→V3).

---

## 0. Comment utiliser ce document

- **Pour un développeur qui reprend le projet :** lire §1 → §4 (architecture,
  patterns, sécurité) puis §6 (le module concerné).
- **Pour un outil d'analyse externe (IA/audit) :** ce document est conçu pour
  être ingéré tel quel comme *contexte système*. Il liste exhaustivement les
  modules, modèles, services, rôles, variables d'environnement, contrats
  externes et invariants de comportement.
- **Source de vérité hiérarchique :** en cas de divergence, le **code** prime,
  puis ce document, puis `CLAUDE.md`. (Le `CLAUDE.md` comporte des inexactitudes
  historiques signalées dans l'audit V2→V3 §2.13.)

---

## 1. Vue d'ensemble produit

NEWTOWT (TransOceanic Wind Transport) est un armateur du **transport maritime
décarboné à la voile** (depuis 2011). `mynewtowt` est la **plateforme unifiée
V3** qui fusionne en un seul déploiement trois audiences :

| Audience | Préfixe d'URL | Authentification | Rôle |
|---|---|---|---|
| **Staff / ERP interne** | `/planning`, `/escale`, `/cargo`, `/admin`… | Cookie signé staff + MFA TOTP | Exploitation maritime complète |
| **Client (plateforme)** | `/me`, `/booking`, `/login` (client) | Cookie signé client + MFA | Réservation de cale, factures, certificats |
| **Public (vitrine + devis)** | `/`, `/routes`, `/devis`, `/contact` | Anonyme | Marketing, SEO, catalogue de routes, leads |
| **Portail expéditeur** | `/p/{token}` | Token UUID (90 j, jamais en clair) | Packing list, messagerie, documents, suivi |

La V3 est une **plateforme** (et non plus seulement un ERP) : elle a **ajouté**
client/public/booking/vitrine/veille/SIRH/chat/cashbox au cœur ERP V2. Voir
l'audit V2→V3 pour la cartographie des **régressions** du cœur staff et leur
reprise (campagne « Reprise P0/P1 »).

---

## 2. Stack technique & exécution

| Couche | Choix | Détails opérationnels |
|---|---|---|
| Backend | FastAPI 0.115 / Python 3.12 / Uvicorn | Entrypoint `app/main.py` (factory `create_app`) |
| ORM | SQLAlchemy 2 **async** (`Mapped[]`) + asyncpg | Decimal partout pour le monétaire/poids |
| DB | PostgreSQL 16 | `Base.metadata.create_all` au boot **dev** ; **prod = Alembic seul** |
| Migrations | Alembic | 86 révisions, dernière `20260624_0080` |
| Front | HTMX 2 + Alpine.js (léger) + Jinja2 SSR | Design system **Kairos** (CSS maison) |
| Icons | Lucide (CDN unpkg) | whitelisté CSP |
| Auth | Cookies signés `itsdangerous` + bcrypt + **MFA TOTP** | Staff 8 h, client 30 j |
| Carto | MapLibre GL + Mapbox/MapTiler | token résolu via `settings.map_token` |
| Météo | Windy / OpenWeather | snapshot historisé `vessel_weather` (cron 30 min) |
| IA | Claude (chatbot Kairos / Newtowt Agent) | `anthropic_api_key` |
| PDF | WeasyPrint | BL, PL, invoice, SOF, Crew List, Carbon Report, brochure |
| Observabilité | OpenTelemetry + Prometheus + Sentry | `prometheus_metrics` on par défaut |
| Conteneurs | Docker + docker-compose | `domain`/`certbot_email` pour le reverse-proxy |

**Démarrage (`create_app`) — ordre des middlewares (du plus externe au plus
interne) :** CORS → `SecurityHeadersMiddleware` → `MaintenanceMiddleware` →
`CSRFMiddleware` → `ForcePasswordChangeMiddleware` → `ForceMfaForAdminMiddleware`.
Au boot : `init_db()` (create_all en dev), `enforce_production_safety()`.

---

## 3. Structure du dépôt (carte mentale)

```
app/
├── main.py            # factory create_app : middlewares + ~40 routers + handlers 403/404
├── config.py          # pydantic-settings (toutes les variables d'env, cf. §5)
├── database.py        # async engine, get_db() (auto-commit/rollback), init_db()
├── auth.py            # cookies signés staff + client, bcrypt, get_current_staff/client
├── permissions.py     # matrice RBAC 9 rôles × 17 modules × {C,M,S} + overrides DB
├── csrf.py            # double-submit cookie towt_csrf
├── templating.py      # env Jinja2, filtres (money/date/flag), globals (t, brand)
├── i18n/              # fr / en / es / pt_br / vi  (catalogues Python)
├── middlewares/       # security_headers, maintenance, force_password, force_mfa
├── models/            # ~50 modèles SQLAlchemy 2 Mapped[]
├── routers/           # ~40 routers (1 par module ou packagés)
├── schemas/           # DTO Pydantic (booking, leg, voyage_*)
├── services/          # ~90 services métier réutilisables (cœur de la logique)
├── utils/             # csv_safe, file_validation, marad, pipedrive, timezones
├── templates/         # base.html + staff/ client/ public/ portal/ pdf/ emails/ errors/
└── static/            # css/tokens.css, css/kairos.css, js/ (externe, CSP-strict), img/
docs/                  # vision, audit V2→V3, backlog de reprise, runbooks, ADR, design
migrations/versions/   # 86 révisions Alembic
scripts/               # seed_demo, backup, import
tests/                 # unit / integration / regression (test_v2_parity)
```

---

## 4. Patterns & invariants de comportement (à respecter impérativement)

Ces règles sont des **contrats de comportement** : tout nouveau code doit les
suivre, tout outil d'analyse doit les supposer vraies.

### 4.1 Base de données
- Session injectée par `get_db()` → **auto-commit en succès / rollback sur
  exception**. Une route **ne fait jamais `await db.commit()`** : elle fait
  `await db.flush()` pour matérialiser un INSERT/UPDATE.
- Schéma : `create_all` au boot **en dev uniquement**. **Production = Alembic
  exclusivement.** Toute modif de schéma ⇒ migration additive (colonnes
  nullable / nouvelles tables) — l'historique des reprises est 100 % additif.
- Monétaire, poids, intensités : **`Decimal`** (jamais `float`).

### 4.2 Routes & mutations
- Mutation = **`validate → modify → await db.flush() → RedirectResponse(303)`**.
- Détection HTMX : `request.headers.get("hx-request")` ⇒ renvoyer un header
  **`HX-Redirect`** au lieu d'une redirection classique.
- Toasts/modales pilotés serveur via en-têtes **`HX-Trigger`**.

### 4.3 Permissions (RBAC) — cf. §7
- Chaque endpoint protégé porte
  **`Depends(require_permission(module, "C"|"M"|"S"))`**.
- Niveaux **C/M/S** = Consult / Modify / Suppress. Les **suppressions** exigent
  le niveau **`S`**.
- `require_permission` fait, en plus du check : pré-chargement du **compteur de
  notifications** + 5 dernières (badge cloche topbar) et de l'état du
  **Newtowt Agent** (toggle widget). Tout échoue *fail-soft* (compteur=0).

### 4.4 Sécurité (transverse) — cf. §8
- **CSRF** : `CSRFMiddleware` double-submit cookie `towt_csrf` ; HTMX injecte le
  header via `csrf-htmx.js`.
- **CSP stricte** : **aucun `<script>` / `onclick` inline**. JS 100 % externe.
  Confirmations destructives via `data-confirm` porté par le `<form>` (forms.js).
- **Audit trail** : `services.activity.record()` sur **toute écriture staff**.
  Table `activity_logs` append-only, visualiseur `/admin/activity-logs`.
- **Uploads** : `safe_files` (validation extension + taille + magic number +
  noms aléatoires + anti-traversal) ; pré-filtre `Content-Length` → **413**
  avant lecture, puis revérification de la taille réelle (anti zip-bomb).
- **Exports tableur** : `csv_safe.sanitize_cell` neutralise l'injection de
  formule sur **CSV et XLSX**.
- **SQL dynamique** : jamais de f-string sur identifiant de table/colonne →
  whitelist + `bindparams()`.

### 4.5 Templates
- Tous étendent `base.html` puis un layout par audience : `staff/_layout`,
  `client/_layout`, `public/_layout`, `portal/_layout`.
- Composants riches dans `kairos.css` (`.card`, `.btn`, `.pill`, `.badge`,
  `.kpi-card`, `.leg-chip`, `.vessel-status-badge`, `.toast`, `.modal-card`…).
- Filtres Jinja : `|money` (Decimal → « 1 234,56 EUR »), `|flag` (ISO2 → emoji),
  `|date`/`|datetime` ; helper `t(key, lang)` pour l'i18n inline ; `brand`.

### 4.6 i18n
- **5 catalogues** : `fr`, `en`, `es`, `pt_br`, `vi` (modules Python sous
  `app/i18n/`). Toute clé ajoutée doit l'être **dans les 5**. Le catalogue
  vietnamien, effondré en V3 (15 clés), a été **restauré à parité** (UX-02).

### 4.7 Formulaires & timezone
- `<form method="POST">` standard ; `forms.js` désactive le submit 5 s
  (anti-double-submit).
- Saisie d'heures portuaires : partial réutilisable **`tz_datetime`**
  (UTC / Paris / Port local + aperçu UTC), câblé via `towt-tz.js` sur
  `.tz-input-wrap`/`.tz-select` (escale, SOF — UX-01).

---

## 5. Configuration & variables d'environnement (`app/config.py`)

`Settings` (pydantic-settings, `.env`). **Refus de démarrer en production** si
secret faible ou mot de passe DB par défaut (`enforce_production_safety`).

| Variable | Défaut | Rôle / comportement |
|---|---|---|
| `APP_ENV` | `development` | `production` active les refus de démarrage |
| `SECRET_KEY` | — (requis) | ≥ 32 car., refusé si dans `WEAK_SECRETS` |
| `DATABASE_URL` | — (requis) | doit utiliser `postgresql+asyncpg://` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 480 | session **staff 8 h** |
| `CLIENT_SESSION_DAYS` | 30 | session **client persistante** |
| `UPLOAD_DIR` | `var/uploads` | stockage PJ (volume monté en prod) |
| `REQUIRE_MFA_FOR_ADMIN` | `True` | force la mise en place MFA admin (middleware) |
| `SITE_URL` | `localhost:8000` | origin attendu pour les attestations WebAuthn/MFA |
| `PIPEDRIVE_API_TOKEN` / `PIPEDRIVE_PIPELINE_NAME` | None / « Deals from web » | sync CRM (leads, deals) |
| `ANTHROPIC_API_KEY` | None | chatbot Kairos / Newtowt Agent |
| `WINDY_API_KEY` | None | météo |
| `MAPBOX_TOKEN` / `MAPTILER_TOKEN` | None | carto (résolus par `map_token`) |
| `TRACKING_API_TOKEN` | None | `POST /api/tracking/upload` (Power Automate) — 503 si absent |
| `PUBLIC_API_KEY` | None | `X-API-Key` de `/api/v1/*` — **503 si absent (fermé par défaut)** |
| `TICKETS_SLA_API_TOKEN` | None | cron `POST /api/tickets/escalate-sla` |
| `WEATHER_API_TOKEN` | None | cron `POST /api/weather/refresh` (snapshot Windy 30 min) |
| `MARAD_*` | base url + token + header | sync **lecture seule** crew Marad (no-op sans token) |
| `NEWSDATA_API_KEY` / `VEILLE_API_TOKEN` | None | veille NewsData.io + cron `POST /api/veille/refresh` |
| `SMTP_*`, `SMTP_FROM_*`, `COMMERCIAL_INBOX_EMAIL` | None | email transactionnel + routage des leads |
| `SENTRY_DSN`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `PROMETHEUS_METRICS` | — / — / True | observabilité |
| `BACKUP_*` (retention, s3_bucket, gpg_recipient) | 7 / None / None | sauvegardes |
| `DOMAIN`, `CERTBOT_EMAIL` | my.newtowt.eu / ops@newtowt.eu | reverse-proxy/TLS |

> **Note V3.1 :** **Stripe retiré** — NEWTOWT facture **par virement** ; aucun
> paiement n'est traité par l'app, l'équipe commerciale confirme les bookings.

**Tokens d'API cron / intégration (récapitulatif de comportement)** : tous les
endpoints cron (`/api/tracking/upload`, `/api/weather/refresh`,
`/api/veille/refresh`, `/api/marad/refresh`, `/api/tickets/escalate-sla`)
renvoient **503 quand leur token n'est pas configuré** — *secure-by-default*.

---

## 6. Modules fonctionnels (comportements attendus)

> État au sens « comportement implémenté sur cette branche ». L'historique de
> reprise (P0/P1) est dans `docs/audit/RAPPORT_DEPLOIEMENT_REPRISE_P0.md` et
> `…_P1.md`.

### 6.1 Planning — `/planning`
Gantt + table + carte ; scénarios **what-if** (drag-drop, comparaison au réel) ;
validation d'intégrité (chevauchement, continuité, vitesse) ; **cascade de
dates** (escale/dockers/notif clients) ; **partage par token** avec
destinataire/langue/sélection leg-à-leg (PLN-04), période/expiration/compteur ;
**brochure commerciale PDF** FR/EN (PLN-01) ; export **CSV** du planning réel
(PLN-03). Modèles : `Leg`, `PlanningScenario`, `PlanningShare`.

### 6.2 Commercial / Pricing — `/commercial`
Clients (création **+ édition/désactivation** COM-03, adresses BL structurées) ;
**grilles tarifaires** (surcharge IMDG, minimum facturation, options
per_palette/tonne/booking, verrouillage de grille active) ; **offres** ; **devis
public** (`/devis`, PDF, rate-limit, honeypot, leads) ; **commandes** riches
(format/poids/THC/frais/route POL-POD/fenêtre livraison/PJ/lien grille — COM-02,
COM-04) ; **affectation commande → leg** (COM-01, filtrée par route + alerte
hors-délai) ; **ventilation multi-legs du CA + réconciliation capacité + PL/BL
épinglées au leg d'origine** (COM-11) ; **push Pipedrive Deal** (COM-06) ;
auto-création packing list à la confirmation. Modèles : `commercial`, `quote`.

### 6.3 Cargo / Packing list / BL / Portail — `/cargo` + `/p/{token}`
**Bill of Lading reconnecté au `PackingListBatch`** (adresses structurées +
marchandise), numérotation `TUAW_{leg}_{seq}` anti-doublon ; **édition/suppression
batch + vue d'audit** (`PackingListAudit`) ; **Arrival Notice** PDF ; **import/
export Excel** (openpyxl, anti-injection formule) ; champs goods riches
(CARGO-13) ; **portail expéditeur** : dépôt de documents, écrans **Suivi voyage**
(3 phases + position satellite) / **Guide** / fiche navire, **multilingue
fr/en/es/pt-br/vi** (CARGO-12), messagerie sécurisée. Modèles : `packing_list`,
`booking`, `booking_message`.

### 6.4 Escale / Port call — `/escale`
Opérations + shifts dockers avec **édition/suppression** et **saisie manuelle
des heures réelles** (ESC-01/03) ; **pilotage statut portuaire ATA/ATD**
idempotent + recalcul OPEX + notifications EOSP/SOSP (ESC-02, **sans cascade
fantôme** — cf. arbitrage) ; `intervenant` + durées prévue/réelle (ESC-04) ;
**cadence dockers** pal/h + écart % (ESC-05) ; **couplage op ↔ équipage** +
billetterie + **PAF auto** ports français (ESC-06) ; **sélecteur de fuseau** sur
l'horodatage statut (ESC-07) ; verrouillage tracé. Modèle : `escale`.

### 6.5 Onboard / Captain + Claims — `/captain`, `/claims`, `/carnet-bord`
**SOF** (Statement of Facts) avec signatures/lock IMO (hash SHA-256),
**édition/suppression d'un SOF non signé** (ONB-01) ; **documents cargo
structurés** 12 types (NOR, LOP×6, Holds Cert, Mate's Receipt…) sérialisés en
`data_json`, signataire choisi dans l'équipage embarqué (ONB-02) ; **pièces
jointes leg** catégorisées + zone docs agent d'escale (ONB-03) ; **clôture
d'escale** + récap PDF (ONB-05) ; **messagerie de bord** enrichie (fil scope
navire, @mentions autocomplete, journal système, suppression — ONB-04) ;
**Noon Report officiel TOWT** + ROB chaîné + **PWA offline** + journal de quart ;
**Carnet de bord ANEMOS** (highlights/photos de voyage). **Claims** : workflow
6 statuts + timeline ; détail financier (franchise/indemnité/provision —
FIN-06/ONB-06), **SOF auto** à la déclaration + rattachement marin (ONB-06).
Modèles : `sof_event`, `noon_report`, `watch_log`, `leg_attachment`,
`onboard_cashbox`, `claim`, `carnet_bord`, `voyage_highlight`, `voyage_photo`.

### 6.6 Crew / Équipage — `/crew`
Fiche marin détaillée avec **édition** (CREW-01, visa US/BR, seaman book,
naissance, nationalité) ; **Crew List PAF** PDF bilingue (CREW-02, obligation
réglementaire) ; **édition/suppression d'affectation** + anti-chevauchement
(CREW-03) ; **embarquement hors leg** autorisé (CREW-04, `leg_id` nullable +
`vessel_id`) ; **billet de transport** upload/download/delete (CREW-05) ;
**alertes billet/escale** sur la fiche (CREW-07) ; **compliance Schengen** réelle
(90/180 persisté + garde-fou override tracé) ; **sync Marad** (lecture seule).
Congés marins (`CrewLeave`) coexistent avec SIRH sédentaire (`HrAbsence`).
Modèles : `crew`, `crew_ticket`.

### 6.7 Stowage / Plan d'arrimage — `/stowage`
18 zones + algo glouton ; **vue à bord** (STO-01, perm captain) ;
**réaffectation de zone** + **retrait d'item** (STO-02/03) ; **politique de
blocage capacité configurable** par feature flag (STO-05, arbitrage « avertir vs
bloquer ») ; **capacité réelle zone × format × gerbage** (coefficients — STO-07) ;
**référentiel IMDG bilingue** (select IMO labellisé — STO-08) ; référentiel
`StowageZoneSpec` éditable (admin). Modèle : `stowage`.

### 6.8 MRV (émissions UE) — `/mrv`
**Compteurs DO (4) + calcul ME/AE/ROB chaîné** ET **noon report** cohabitent
(arbitrage A1 hybride) ; **export DNV Veracity 18 colonnes** (correctif IMO) ;
**Carbon Report PDF** avec **blocage qualité** ; **édition/suppression d'event** ;
**position DMS** auto depuis GPS (MRV-07) ; **contrôle qualité multi-règles**
verrouillé par tests de non-régression (MRV-05) ; **sync auto** noon/SOF →
`MRVEvent` (idempotente) ; **Carbon Report par leg** (intensités /NM, /t, /t·nm,
CO₂ évité) ; **facteur CO₂ versionné** (`/admin/co2`, NOx/SOx ré-exposés —
ADM-06). Modèles : `mrv`, `co2_variable`.

### 6.9 Navigation / Performance — `/performance/navigation`
Multi-legs/multi-navires : carte (1 couleur/leg, points GPS + trait + route
théorique), tableau comparatif (réelle/théorique/écart/durée/restant), météo le
long du trajet + « conditions actuelles » par navire (rose des vents,
anémomètre/Beaufort, pression, visibilité, T°). Filtre anti-saut > 50 NM
(SEC restauré). Services : `voyage_track`, `weather`, `weather_history`.

### 6.10 Finance + KPI — `/finance`, `/kpi`
**Prévisionnel/réel** par poste sur `LegFinance` + marge + écarts (FIN-01,
arbitrage A2) ; **export CSV finance** 19 colonnes (FIN-02) ; **NOx/SOx évités**
(FIN-03, service `emissions`) ; **rollup finance** ; **CRUD OPEX** ; onglet
**Exploitation** (écart planning ETD→ATD, vitesse, durée) (FIN-04) ; équivalences
CO₂ (FIN-05) ; **vue KPI consolidée** (FIN-07) ; **détail exposition assurance**
(provision/indemnité/franchise — FIN-06). Modèles : `finance`, `insurance`.

### 6.11 Booking client — `/booking/...`
Wizard 3 étapes (recherche route → choix cale → confirmation) ; espace client
`/me` (dashboard, documents, factures dormantes `client_invoice`) ; **label
Anemos** (certificat CO₂ par booking + RSE annuel). Modèles : `booking`,
`client_account`, `client_invoice`, `anemos_certificate`.

### 6.12 Modules transverses / V3-only
- **Tickets escale** `/tickets` : kanban + SLA P1/P2/P3 + escalade cron.
- **Cashbox** `/cashbox` : caisse de bord EUR/USD/VND.
- **RH (SIRH)** `/rh` : dossiers, contrats & avenants + alertes, congés/absences
  + self-service `/rh/moi`, EVP + verrouillage période, **export Silae CSV**,
  coffre-fort bulletins, entretiens, reporting RH.
- **Tracking flotte** `/tracking` : positions live + historique filtrable
  (navire × leg × période) ; ingestion multi-format CSV/ZIP/XLSX (Power Automate
  rétro-compatible). Modèle : `vessel_position`.
- **Chat Kairos AI / Newtowt Agent** `/chat` : assistant Claude (toggle admin).
- **Veille d'actualité** `/veille` : flux NewsData.io (staff), refresh cron.
  Modèles : `news_item`, `news_source`.
- **Vitrine publique** `/` : flotte, impact, navigation, contact, recrutement,
  kit presse, passagers 2027, actualités/blog, socle SEO/JSON-LD, sélecteur de
  langue à drapeaux. (Présent sur `main` — cf. §10.)
- **Admin** `/admin/...` : users, opex, insurance, maintenance, activity-logs,
  permissions (éditeur de matrice), CO₂, exports/purges DB whitelistés (ADM-04),
  CRUD navires (ADM-01), moteur d'alertes dashboard (ADM-02).

---

## 7. Rôles, permissions & audiences

**9 rôles** : `administrateur`, `operation`, `armement`, `technique`,
`data_analyst`, `marins`, `commercial`, `manager_maritime`, `rh`
(+ alias legacy : admin→administrateur, manager/operator→operation,
viewer→data_analyst).

**17 modules** : planning, commercial, escale, cargo, finance, kpi, captain,
crew, claims, mrv, **rh**, booking, tickets, analytics, chat, **veille**, admin.

**Niveaux** : `C` (Consult) < `M` (Modify) < `S` (Suppress). Une cellule vaut
`""` | `C` | `CM` | `CMS`.

**Mécanique (ARC-04)** : matrice codée en dur `_MATRIX` = **défaut**, surchargée
par des **overrides en base** (`role_permissions`, écran `/admin/permissions`,
cache 60 s). Le chemin requête (`require_permission`) lit la **matrice
effective** ; toute erreur DB **retombe fail-closed sur `_MATRIX`**. Garde-fou :
`(administrateur, admin)` est toujours forcé au défaut (l'admin ne peut jamais se
verrouiller dehors). Les helpers synchrones (`has_permission`, `can_view/edit/
delete`, `has_any_access`) ne voient que `_MATRIX` et servent **l'affichage**
(sidebar filtrée, flags UI, chatbot) — **pas** le contrôle d'accès.

Points d'arbitrage RBAC actés : `data_analyst` a `finance` = CMS et `analytics`
= CMS mais **pas** d'accès admin (restriction V3 assumée) ; le rôle **`rh`** a
l'autorité d'écriture sur `rh`, consultation ailleurs, **pas** d'accès finance
par défaut (masse salariale à arbitrer).

---

## 8. Sécurité (synthèse opérationnelle)

| Surface | Comportement garanti |
|---|---|
| **Auth staff** | cookie signé `itsdangerous`, bcrypt, expire 8 h ; rate-limit sur `POST /login` (restauré SEC) |
| **Auth client** | cookie signé, 30 j, MFA TOTP, codes de récupération, device de confiance + alerte email nouvel appareil |
| **MFA admin** | forcé par `ForceMfaForAdminMiddleware` (redir `/admin/my-account/mfa`) si `REQUIRE_MFA_FOR_ADMIN` |
| **Force password** | `ForcePasswordChangeMiddleware` redirige tant que `User.must_change_password` |
| **CSRF** | double-submit cookie `towt_csrf` ; HTMX auto via `csrf-htmx.js` |
| **CSP** | stricte, pas d'inline ; ressources externes whitelistées (unpkg, fonts.gstatic, maptiler…) |
| **Portail token** | UUID hex 24 car. (90 j), **jamais loggé en clair** (SHA-256), accès audité `portal_access_logs`, rate-limit rebranché |
| **API v1** | `X-API-Key` constant-time, **fail-closed 503 sans clé** |
| **Uploads** | `safe_files` (ext+taille+magic+noms aléatoires+anti-traversal), pré-filtre Content-Length → 413, revérif post-lecture |
| **Exports** | `csv_safe` anti-injection de formule (CSV + XLSX) |
| **Audit** | `services.activity.record()` sur écritures staff (append-only) |
| **Maintenance** | `MaintenanceMiddleware` (toggle `/tmp/.maintenance`, bypass admin) |
| **SQL** | paramétré ; whitelist + `bindparams()` pour identifiants dynamiques |

RBAC = **au niveau module** (pas de scoping par objet) — modèle applicatif
documenté et assumé.

---

## 9. Intégrations externes & contrats

| Intégration | Sens | Contrat / comportement |
|---|---|---|
| **Power Automate — tracking** | entrant | `POST /api/tracking/upload` (`X-API-Token`) ; ingestion CSV/ZIP/XLSX/urlencoded tolérante ; **écriture rétro-compatible V2**. ⚠️ **lecture** (4 endpoints GET + ancienne réponse JSON) **rompue** vs V2 |
| **Power Automate — météo** | entrant (cron 30 min) | `POST /api/weather/refresh` → snapshot Windy du dernier GPS/navire → `vessel_weather` |
| **Power Automate — veille** | entrant (cron) | `POST /api/veille/refresh` → NewsData.io |
| **Power Automate — SLA tickets** | entrant (cron) | `POST /api/tickets/escalate-sla` |
| **Pipedrive** | sortant | leads/devis → Deal (pipeline résolu par nom) ; sync org en masse |
| **Marad (MaraSoft)** | entrant **lecture seule** | sync crew ; `mynewtowt` n'écrit jamais ; no-op sans token |
| **Anthropic / Claude** | sortant | chatbot Kairos / Newtowt Agent |
| **Windy / OpenWeather** | sortant | météo live + historisée |
| **MapLibre + Mapbox/MapTiler** | sortant | tuiles carto |
| **SMTP** | sortant | email transactionnel + routage leads (`commercial_inbox_email`) |
| **API publique v1** | sortant (B2B) | `/api/v1/*` read-only, `X-API-Key`, fermée sans clé |

---

## 10. État des branches vs `main` (contexte de continuité)

Au 2026‑06‑29, le dépôt distant ne porte que **`main`** et la branche de travail
**`claude/email-branches-audit-5uw0b2`**. Comparaison **par présence réelle de
code** (et non par graphe de commits) :

- **La branche de travail est ~99 % un sur-ensemble de `main`** : elle porte la
  campagne **« Reprise P0/P1 »** (parité staff V2→V3 complète, P0 = 100 %),
  **8 modules absents de `main`** (`rh`/SIRH, `navigation`, `onboard`, `devis`,
  `marad`, `pwa`, `scenario`, `carnet_bord`), **+58 migrations** (`…0023`→`…0080`)
  et le **tableau de parité** `test_v2_parity.py`.
- **`main` est très en retard** : **29 migrations** (vs 87), **0 commit
  « Reprise »**, pas de tableau de parité. Son seul surplus est un **mince delta
  vitrine** (page recrutement, passagers 2027, kit presse, actualités) resté en
  avance.

⇒ Le rattrapage = **reporter le delta marketing sur la branche, puis fusionner
la branche dans `main`**. Plan détaillé dans
`ETUDE_COMPARATIVE_BRANCHES_VS_MAIN.md` (Action A).

---

## 11. Tests & qualité

- **98 fichiers de tests** (unit / integration / regression).
- **`tests/regression/test_v2_parity.py`** = tableau de bord **vivant** de la
  parité V2↔V3 : chaque fonctionnalité V2 reprise est asservie par un test, les
  non-reprises sont `skip` motivées. Le dictionnaire `_PENDING` des gaps **P0
  est vide** (parité P0 = 100 %).
- Politique « zéro défaut » : implémenter → tester → `/code-review` →
  `/security-review` → CI verte (`lint` + `security` + `test`) → merge squash.
- Migrations toutes **additives** (sûres en prod).

---

## 12. Dette connue & points d'attention (pour outils d'analyse)

1. **`CLAUDE.md` partiellement inexact** : Cargo « ✅ audit/lock » (l'audit était
   non consultable avant reprise) ; Insurance présentée à tort comme V3-only ;
   CO₂ → label Anemos ; statuts Finance/KPI. À recouper avec ce document.
2. **Divergence `main` ↔ branche d'audit** (§10) — non encore réconciliée.
3. **Modules V3-only à consolider** : `client_invoice`/`invoicing` **dormant** ;
   congés `CrewLeave`/`HrAbsence` non unifiés ; `erp_scaffold_router` en
   collision de slugs (ne garder qu'`analytics`) ; PWA offline réel au backlog ;
   veille IA (P2) absente.
4. **Contrat API tracking** : lecture (GET) rompue vs V2 — à versionner si des
   flux PA en dépendent.
5. **Risques de migration de données** (historique V2) : `LegFinance`
   forecast/actual & `quay_cost` sans cible directe ; `LegKPI.cargo_tons` (t) →
   `tonnage_kg` (kg) = ×1000 ; ~40 colonnes `PackingListBatch` ; renommages
   tracking/crew (cf. audit §6).

---

## 13. Glossaire maritime (rappel)

`Leg` (segment port A→B) · `leg_code` `{seq}{vessel}{dep}{arr}{year}` (ex.
`1CFRBR6`) · `ETD/ETA` (estimés) · `ATD/ATA` (réels) · `Escale` (à quai) · `SOF`
(Statement of Facts) · `BL/BOL` (Bill of Lading) · `POL/POD` (Port of Loading/
Discharge) · `LOCODE` (UN, 5 car.) · `OPEX` (coût journalier) · `EOSP/SOSP`
(End/Start Of Sea Passage) · `MRV` (réglementation UE émissions) · `MDO/DO`
(Marine Diesel Oil) · `ROB` (Remaining On Board) · `Schengen` (90 j/180) ·
`PAF` (Police Aux Frontières) · `DNV Veracity` (plateforme de vérification MRV).

---

*Document de référence — dérivé du code au 2026‑06‑29. À régénérer après toute
évolution majeure de schéma, de matrice RBAC ou d'intégration externe.*
