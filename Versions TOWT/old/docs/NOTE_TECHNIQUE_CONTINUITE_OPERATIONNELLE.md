# NOTE TECHNIQUE DE CONTINUITÉ OPÉRATIONNELLE
## Plan de reconstruction intégral de l'application `my_newtowt`

> **Statut** : document de référence — Plan de Continuité d'Activité (PCA)
> **Objet** : permettre la reconstruction complète de l'application en cas de perte majeure (perte du code source, perte du serveur, perte des sauvegardes applicatives, départ de l'équipe, etc.).
> **Public visé** : équipe technique de reprise (développeurs, DBA, DevOps), équipe projet (chef de projet, product owner), direction NEWTOWT.
> **Version applicative documentée** : `my_newtowt v3.0.0` (post-restructuration).
> **Date de rédaction** : mai 2026.

Le présent document est structuré en deux grandes parties :

1. **Partie I — Cadre, contexte et nomenclature** : sert d'introduction et de référentiel commun. Il pose les bases techniques, opérationnelles et sémantiques nécessaires à toute reprise.
2. **Partie II — Description module par module** : pour chaque module, deux regards complémentaires sont fournis :
   - **(A) Vision développeur** : approche technique / opérationnelle pure (modèle de données, routes, variables, règles, comportements).
   - **(B) Vision chef de projet** : approche cahier des charges (besoin métier, périmètre, utilisateurs cibles, règles de gestion, acceptance).

L'objectif est qu'à partir de ce seul document, une équipe nouvelle puisse reconstruire l'application **module par module**, avec le même comportement métier, en utilisant la même stack ou une stack équivalente.

---

# PARTIE I — CADRE, CONTEXTE ET NOMENCLATURE

## 1. Contexte d'utilisation

### 1.1 L'entreprise — NEWTOWT (TransOceanic Wind Transport)

NEWTOWT est une compagnie française de transport maritime cargo à la voile (cargo vélique), pionnière du transport décarboné depuis 2011. Elle exploite, dans la configuration documentée par cette note (post-restructuration de 2026), une flotte de **quatre voiliers cargos** :

| Code | Nom | Capacité indicative | Notes |
|------|-----|---------------------|-------|
| 1 | Anemos | 850 palettes EPAL | navire historique |
| 2 | Artemis | 850 palettes EPAL | |
| 3 | Atlantis | 850 palettes EPAL | |
| 4 | Atlas | 850 palettes EPAL | |

L'activité passagers, exploitée par TOWT avant la restructuration, **n'existe plus en v3.0.0**. Toute reconstruction de l'application doit veiller à **ne pas réintroduire** de module passagers.

### 1.2 Activité métier couverte

L'application supporte l'ensemble du cycle d'exploitation d'un navire de cargo vélique :

1. **Planification commerciale** des voyages (legs) : qui part d'où, vers où, quand, sur quel navire.
2. **Vente et contractualisation** : commandes clients, grilles tarifaires, offres commerciales, intégration CRM.
3. **Préparation cargo** : packing lists, plan d'arrimage (stowage), Bill of Lading, Arrival Notice.
4. **Escale (port call)** : opérations portuaires, équipes de manutention (dockers), Statement of Facts.
5. **Vie à bord** : SOF, documents cargo, messagerie équipage / armateur, ETA shift, clôture de voyage.
6. **Équipage** : membres, embarquements / débarquements, calendrier annuel, compliance Schengen, billets.
7. **Finance** : OPEX, frais portuaires, revenus, marge prévisionnel / réalisé.
8. **KPI** : tonnage transporté, émissions CO₂ évitées, certificats d'évitement carbone par client.
9. **MRV** : reporting fuel/émission au format réglementaire européen (DNV CSV, Carbon Report).
10. **Claims** : sinistres cargo, équipage, coque (P&I, Hull/DIV, War Risk).

### 1.3 Cible utilisateur

L'outil est utilisé en interne par les équipes NEWTOWT (huit profils de rôles — cf. §3.4) et **partiellement par les clients via des portails publics protégés par token** :
- Portail packing list (`/p/{token}`) : remplissage et suivi par le client expéditeur.
- Lien planning partagé (`/planning/share/{token}`) : consultation d'un planning filtré par un partenaire externe.

Aucun client n'a de compte authentifié dans l'application.

### 1.4 Environnement de déploiement

L'application est déployée :
- En **production** sur un VPS OVH (Linux), accessible via `http://51.178.59.174` (et un futur domaine `https://my.newtowt.eu`), derrière un reverse proxy nginx.
- En conteneurs Docker (`docker-compose`) : un service `app` (FastAPI/Uvicorn) et un service `db` (PostgreSQL 16-alpine).
- Le conteneur applicatif s'appelle `towt-app-v2`, le conteneur base `towt-db`.
- Base de données non publiée sur l'hôte : accès via `docker exec` ou tunnel SSH.

---

## 2. Nomenclature technique

### 2.1 Stack logicielle

| Couche | Choix retenu | Version |
|---|---|---|
| Langage backend | Python | 3.12 |
| Framework HTTP | FastAPI | 0.115.6 |
| Serveur ASGI | Uvicorn | 0.34.0 (`--workers 2`) |
| ORM | SQLAlchemy `asyncio` | 2.0.36 |
| Driver DB | asyncpg | 0.30.0 |
| Base de données | PostgreSQL | 16-alpine |
| Migrations | Alembic (présent dans `requirements.txt` et `alembic.ini`) + scripts SQL ad hoc | 1.14.0 |
| Validation/config | Pydantic + pydantic-settings | 2.10 / 2.7 |
| Hash mot de passe | passlib + bcrypt | passlib 1.7.4, bcrypt 4.0.1 |
| Signature de session | itsdangerous (`URLSafeTimedSerializer`) | 2.2.0 |
| Templating | Jinja2 | 3.1.5 |
| HTTP client (Pipedrive, OSM Nominatim) | httpx | 0.28.1 |
| Excel | openpyxl | 3.1.5 |
| Word | python-docx | 1.1.2 |
| PDF | reportlab + WeasyPrint | 4.2.5 / 63.1 |
| Frontend | HTMX 2.0.4 + Lucide icons + Jinja2 templates | — |
| Fonts | Manrope (UI/PDF), DM Serif Display (accents) | Google Fonts |
| Conteneurisation | Docker + docker-compose | — |
| Reverse proxy | nginx | (hors conteneur) |

**Pas de framework JS lourd** : pas de React, Vue, Angular, Svelte. Tout est SSR par Jinja2 + interactions HTMX (`hx-post`, `hx-get`, `hx-target`, `hx-swap`, `hx-boost`).

### 2.2 Arborescence

```
mytowt/
├── app/
│   ├── main.py                  # entrée FastAPI, montage routers, middlewares
│   ├── config.py                # pydantic Settings (env)
│   ├── database.py              # engine async, session factory, Base ORM
│   ├── auth.py                  # bcrypt, itsdangerous, get_current_user
│   ├── permissions.py           # matrice rôles × modules × {C,M,S}
│   ├── csrf.py                  # CSRF middleware (cookie + header)
│   ├── security_middleware.py   # ForcePasswordChangeMiddleware
│   ├── maintenance.py           # MaintenanceMiddleware (bandeau + 503)
│   ├── templating.py            # config Jinja2 + filtres
│   ├── i18n/                    # traductions (fr, en, es, pt-br, vi)
│   ├── models/                  # 25+ modèles SQLAlchemy
│   ├── routers/                 # 1 router FastAPI par module
│   ├── templates/               # Jinja2, base.html + sous-dossier par module
│   ├── static/css/app.css       # design system NEWTOWT (single CSS file)
│   ├── static/img/              # logos NEWTOWT + favicon
│   ├── services/                # services partagés (rate_limit)
│   └── utils/                   # file_validation, navigation, notifications,
│                                # pipedrive, portal_security, safe_files,
│                                # timezones, activity
├── Design/                      # charte NEWTOWT (logos, design tokens W3C)
├── docs/                        # docs versionnées (cette note, audits, v2/)
├── migrations/                  # Alembic + scripts SQL idempotents
├── scripts/                     # backup, import, purge, seed
├── nginx/                       # config reverse proxy
├── docker-compose.yml           # 2 services : app, db
├── Dockerfile                   # image Python 3.12-slim
├── .env.example                 # template de configuration
├── requirements.txt             # gel des dépendances Python
├── alembic.ini                  # config Alembic
├── backup.sh / restore.sh / deploy.sh / update.sh  # outillage VPS
└── CLAUDE.md, README.md         # documentation projet
```

### 2.3 Variables d'environnement (cf. `.env.example`)

| Variable | Obligatoire | Description |
|---|---|---|
| `APP_NAME` | non (défaut `my_newtowt`) | Nom applicatif affiché. |
| `APP_VERSION` | non (défaut `3.0.0`) | Version applicative. |
| `APP_ENV` | non (défaut `production`) | `production` ou `development`. |
| `DEBUG` | non (défaut `false`) | Active `echo` SQLAlchemy et logs verbeux. |
| `DATABASE_URL` | **oui** | URL async `postgresql+asyncpg://user:pass@host/db`. Doit pointer sur Postgres 16. **Refus de démarrer si mot de passe par défaut `towt_secure_2025`**. |
| `SECRET_KEY` | **oui** | Clé HMAC signature cookie. **>=32 caractères**, non listée dans `weak_secrets`. Le démarrage est refusé sinon. |
| `ALGORITHM` | non (défaut `HS256`) | Algorithme JWT/itsdangerous. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | non (défaut `480`) | Durée de vie cookie de session (8h). |
| `TRACKING_API_TOKEN` | recommandé | Token attendu sur l'en-tête `X-API-Token` pour `/api/tracking/upload`. **Si vide → endpoint renvoie 503**. |
| `SITE_URL` | non (défaut `https://my.newtowt.eu`) | URL absolue pour générer les liens portail envoyés aux clients. |
| `PIPEDRIVE_API_TOKEN` | non | Token Pipedrive (intégration CRM). |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | **oui** (compose) | Identifiants du service Postgres dans `docker-compose.yml`. |
| `BACKUP_RETENTION_DAYS` | non | Rétention des dumps. |
| `DOMAIN` / `CERTBOT_EMAIL` | non | Pour le déploiement TLS. |

### 2.4 Middlewares actifs (`app/main.py`, dans l'ordre d'exécution)

1. **CORSMiddleware** — origines autorisées : `https://my.towt.eu`, `http://51.178.59.174`. Méthodes : GET, POST, PUT, DELETE, PATCH, OPTIONS. En-têtes HTMX (`HX-*`) autorisés.
2. **SecurityHeadersMiddleware** — applique sur toute réponse :
   - `Content-Security-Policy` avec `default-src 'self'`, scripts limités à `unpkg.com`, fonts à `fonts.gstatic.com`, tiles à `*.tile.openstreetmap.org`, AJAX à `nominatim.openstreetmap.org`.
   - `X-Content-Type-Options: nosniff`
   - `X-Frame-Options: SAMEORIGIN`
   - `Referrer-Policy: strict-origin-when-cross-origin`
   - `Strict-Transport-Security: max-age=31536000; includeSubDomains`
   - `Permissions-Policy: camera=(), microphone=(), geolocation=()`
3. **MaintenanceMiddleware** — bandeau + 503 quand le mode maintenance est activé via `/admin/maintenance/enable`.
4. **CSRFMiddleware** — double-submit cookie : pose `towt_csrf` côté client, attend `x-csrf-token` sur toute requête mutante. HTMX injecte le token automatiquement (script dans `base.html`).
5. **ForcePasswordChangeMiddleware** — si `User.must_change_password = True`, redirige toute requête vers `/admin/my-account/change-password`.

### 2.5 Exception handlers

- `AuthRequired` (custom) → redirige vers `/login` (303).
- HTTP 403 → page `403.html` (HTML) ou `{"detail":"Forbidden"}` (JSON, si `Accept: application/json`).
- HTTP 404 → page `404.html` (HTML) ou JSON sinon. Fallback `PlainTextResponse("Page non trouvée")`.
- `/.well-known/security.txt` exposé en plain-text avec contact `security@towt.eu`.

### 2.6 Conventions de codage et garde-fous

Règles **strictes** issues de `CLAUDE.md` (à respecter dans toute reconstruction) :

- **Ne jamais appeler `await db.commit()` dans une route** : le `get_db()` dependency gère commit/rollback automatique (`yield → commit`, `except → rollback`). Utiliser `await db.flush()` pour matérialiser un INSERT/UPDATE et récupérer l'ID.
- **Détection HTMX** : `request.headers.get("HX-Request")` → renvoyer le header `HX-Redirect` ; sinon `RedirectResponse(status_code=303)`.
- **Anti-injection SQL admin** : toutes les références dynamiques à des tables passent par une whitelist `ALLOWED_TABLES` + `.bindparams()`.
- **Permissions** : toute route doit déclarer `Depends(require_permission("module", "C"|"M"|"S"))` ; les routes publiques (`/p/{token}`, `/planning/share/{token}`, `/api/tracking`) sont **explicitement** hors auth.
- **Modules à ne jamais introduire** : pas de module passagers (`passengers`), pas de framework JS lourd.
- **Polices** : `Manrope` (UI/PDF) + `DM Serif Display` (accent) uniquement. Interdit : `Segoe UI`, `Inter`, `Poppins`.
- **Static permissions** : appel `_fix_static_permissions()` au démarrage pour éviter les 403 sur `/static/`.

### 2.7 Vue d'ensemble de la base de données

Tables persistées (par module) :

| Module | Tables |
|---|---|
| Sécurité / Admin | `users`, `rate_limit_attempts`, `portal_access_logs`, `activity_logs`, `activity_log` (legacy) |
| Référentiels | `vessels`, `ports`, `port_configs`, `opex_parameters`, `emission_parameters`, `co2_variables`, `mrv_parameters`, `insurance_contracts` |
| Planning | `legs`, `planning_shares` |
| Commercial | `orders`, `order_assignments`, `clients`, `rate_grids`, `rate_grid_lines`, `rate_offers` |
| Cargo | `packing_lists`, `packing_list_batches`, `packing_list_audit`, `packing_list_documents`, `portal_messages` |
| Escale | `escale_operations`, `docker_shifts`, `operation_crew` (assoc) |
| Equipage | `crew_members`, `crew_assignments`, `crew_tickets` |
| On Board | `sof_events`, `onboard_notifications`, `cargo_documents`, `cargo_document_attachments`, `onboard_attachments`, `eta_shifts`, `onboard_messages`, `onboard_message_mentions` |
| MRV | `mrv_events` |
| Stowage | `stowage_plans` |
| Tracking | `vessel_positions` |
| Finance | `leg_finances` |
| KPI | `leg_kpis` |
| Claims | `claims`, `claim_documents`, `claim_timeline` |
| Notifications dashboard | `notifications` |

La création du schéma se fait au démarrage via `Base.metadata.create_all` (`init_db()`). Les évolutions de schéma sont, en pratique, **versionnées par scripts SQL ad hoc** (cf. `migrations/`) et appliquées via `docker exec towt-app-v2 python3 -c "..."` (cf. CLAUDE.md §Database migrations).

---

## 3. Nomenclature opérationnelle (glossaire maritime et applicatif)

### 3.1 Glossaire maritime

| Terme | Définition |
|---|---|
| **Leg** | Segment de voyage d'un port A à un port B. Unité de base de la planification. |
| **leg_code** | Identifiant unique au format `{SHIP}{LETTER}{DEP_COUNTRY}{ARR_COUNTRY}{YEAR_LAST_DIGIT}`, ex. `1CFRBR6` = Anemos, 3ᵉ escale (C), France→Brésil, 2026. |
| **ETA / ETD** | Estimated Time of Arrival / Departure. |
| **ATA / ATD** | Actual Time of Arrival / Departure. |
| **ETD_REF / ETA_REF** | Référence : ETD/ETA initiaux au moment du booking (immuables, sert au calcul du décalage). |
| **Escale** | Période pendant laquelle le navire est à quai (entre ATA et ATD). |
| **SOF** | Statement of Facts — chronologie des événements portuaires. |
| **NOR / NOR_RT** | Notice of Readiness / Re-Tendered. |
| **BL / BOL** | Bill of Lading — document de transport et titre de propriété de la cargaison. |
| **POL / POD** | Port of Loading / Port of Discharge. |
| **LOCODE** | Code UN de port (5 caractères), ex. `FRFEC` = Fécamp, `BRSSO` = São Sebastião. |
| **OPEX** | Operating Expenditure (coût journalier d'exploitation du navire). |
| **Docker shift** | Vacation de manutentionnaires (stevedores). |
| **EOSP / SOSP** | End / Start Of Sea Passage. |
| **MRV** | Monitoring, Reporting, Verification — réglementation européenne sur les émissions maritimes. |
| **MDO** | Marine Diesel Oil. |
| **ROB** | Remaining On Board (quantité de fuel à bord). |
| **DWT** | Deadweight Tonnage. |
| **Élongation (coeff.)** | Coefficient multiplicatif pour passer de la distance orthodromique théorique à la distance réelle parcourue (vent, courants). Défaut 1.25. |
| **Palette EPAL** | Europalette 120×80 cm, coefficient d'occupation 1.0. |
| **Palette USPAL / PORTPAL** | Palette US / portuaire 120×100, coefficient 1.2. |
| **IBC** | Conteneur intermédiaire pour vrac, +6 cm, coeff 1.3. |
| **Big Bag** | Sac vrac palettisé (+3 cm), coeff 1.25. |
| **Barrique 120 / 140** | Barriques 120×120 (coeff 1.5) / 140×140 (coeff 2.0). |
| **Cale / pont / bloc** | Hiérarchie d'arrimage (cf. §Stowage). |
| **Basket (panier)** | Unité de manutention standard : 380×150 cm, 2.2 m, CMU 5.1 t, tare 2.2 t. |
| **Schengen status** | Statut Schengen d'un marin étranger (compliant / warning / non_compliant). |

### 3.2 Glossaire applicatif

| Terme | Définition |
|---|---|
| **Rôle** | Profil utilisateur déterminant ses droits. 8 rôles : administrateur, opération, armement, technique, data_analyst, marins, commercial, manager_maritime. |
| **Module** | Domaine fonctionnel applicatif : 10 modules permissionnés. |
| **C / M / S** | Niveaux de permission : Consult / Modify / Suppress. |
| **HTMX** | Bibliothèque permettant les requêtes AJAX déclaratives via attributs HTML (`hx-post`, `hx-get`…). |
| **Portail (cargo)** | Espace public protégé par token, où un client renseigne la packing list de sa commande. |
| **Partage planning** | Lien public protégé par token donnant accès à une vue filtrée du planning. |
| **Notification dashboard** | Message éphémère affiché sur le tableau de bord (nouvelle commande, message client, EOSP/SOSP, claim, ETA shift). |
| **Activity log** | Journal d'audit des actions utilisateur (login, create, update, delete, login_fail). |
| **Closure workflow** | Workflow de clôture d'un voyage : `open → review → approved → locked`. |
| **Token portail (`packing_lists.token`)** | UUID hex tronqué à 24 caractères, validité 90 jours. |
| **Token planning share** | Idem, sans expiration explicite (désactivable via `is_active`). |

### 3.3 Conventions d'identifiants

- **leg_code** : voir 3.1.
- **Référence commande** : `ORD-{YEAR}-{SEQ4}` (recommandé) ou format historique.
- **Référence packing list** : id auto-incrémenté, exposé via token uuid hex 24 car.
- **Référence claim** : `CLM-{YEAR}-{SEQ4}` ou équivalent.
- **Référence grille tarifaire** : `RG-{YEAR}-{SEQ4}` (ex. `RG-2026-0001`).
- **Référence offre tarifaire** : `RO-{YEAR}-{SEQ4}`.
- **BL/BOL** : format prévu `TUAW_{voyage_id}_{bl_no}` avec **3 OBL** (Original Bill of Lading).

### 3.4 Matrice des rôles et permissions

Source de vérité : `app/permissions.py` — `_MATRIX`.

| Rôle | planning | commercial | escale | finance | kpi | captain | crew | cargo | claims | mrv |
|---|---|---|---|---|---|---|---|---|---|---|
| **administrateur** | CMS | CMS | CMS | CMS | CMS | CMS | CMS | CMS | CMS | CMS |
| **operation** | CM | CM | CMS | — | C | CM | CM | CMS | CMS | CM |
| **armement** | C | — | C | — | C | C | CMS | — | — | C |
| **technique** | C | C | CMS | — | C | CM | C | C | C | CM |
| **data_analyst** | C | C | C | CMS | C | C | C | C | C | CM |
| **marins** | C | — | C | — | C | C | C | C | — | C |
| **commercial** | C | CMS | C | — | C | C | — | CM | — | — |
| **manager_maritime** | CM | CM | CM | — | C | CMS | CM | CM | CM | CM |

Mapping de compatibilité (`_LEGACY_MAP`) : `admin → administrateur`, `manager → operation`, `operator → operation`, `viewer → data_analyst`, `gestionnaire_passagers → commercial`.

Règle générale appliquée par `require_permission(module, level)` :
- GET (lecture) → niveau `C`
- POST/PUT/PATCH (mutation) → niveau `M`
- DELETE → niveau `S`

Le bouclier est posé au niveau du **router** dans `main.py` :
```python
app.include_router(planning_router, dependencies=[Depends(require_permission("planning", "C"))])
```
…et **renforcé** dans les routes individuelles pour les actions M/S.

### 3.5 Identité visuelle (charte « Nouvelle Étoile »)

Source de vérité : `Design/newtowt-design-tokens.json` (W3C draft Design Tokens).

| Couleur | Code hex | CSS variable canonique | Alias rétro-compat |
|---|---|---|---|
| Teal NEWTOWT | `#0D5966` | `--newtowt-teal` | `--towt-blue` |
| Vert NEWTOWT | `#87BD29` | `--newtowt-vert` | `--towt-green` |
| Cuivre NEWTOWT | `#B47148` | `--newtowt-cuivre` | — |
| Sable NEWTOWT | `#EFE6D6` | `--newtowt-sable` | — |
| Bleu marine | (cf. tokens) | `--newtowt-bleu-marine` | — |

Ratio chromatique cible : **60% teal · 20% vert · 10% cuivre · 10% neutres**.

Polices : **Manrope** (UI/PDF/print) + **DM Serif Display** (accents/exergues). Chargement Google Fonts dans `base.html`.

Logos : `app/static/img/logo_newtowt_white.png` (sombre/sidebar), `logo_newtowt_web.png` (clair). Favicon SVG.

Classes utilitaires `app.css` à utiliser de préférence aux styles inline : `.card`, `.card-title`, `.alert`, `.alert-success`, `.alert-error`, `.field-label`, `.field-value`, `.btn-outline`, `.leg-code`, `.account-grid`.

---

# PARTIE II — DESCRIPTION MODULE PAR MODULE

> **Convention de lecture** : pour chaque module, la section **(A)** décrit le module *en tant que développeur* (modèle, routes, règles techniques) ; la section **(B)** le décrit *en tant que chef de projet rédigeant le cahier des charges* (besoin, périmètre, règles de gestion, acceptance, risques). Les deux sections sont **complémentaires** : on doit pouvoir reconstruire chaque module en lisant l'une OU l'autre, mais lire les deux assure la cohérence.

---

## Module 0 — Socle technique : sécurité, sessions, permissions, CSRF

### 0.A — Vision développeur

**Pourquoi** : aucun module fonctionnel n'existe sans authentification (sauf portails clients). Le socle pose :
- L'authentification par session signée.
- L'autorisation par matrice rôle × module × {C, M, S}.
- La protection CSRF.
- Le rate limiting persistant.
- L'audit de toutes les actions sensibles.

**Modules de code et fichiers** :
- `app/auth.py` — bcrypt + itsdangerous + dependency `get_current_user`.
- `app/permissions.py` — matrice, helpers `can_view/can_edit/can_delete/has_any_access`, dependency `require_permission`.
- `app/csrf.py` — CSRFMiddleware.
- `app/security_middleware.py` — ForcePasswordChangeMiddleware.
- `app/services/rate_limit.py` + `app/models/rate_limit.py` — rate limit persistant.
- `app/utils/portal_security.py` — sécurité des tokens portail.

**Modèles de données** :
- `users (id, username UNIQUE, email UNIQUE, hashed_password, full_name, role, language, is_active, must_change_password, created_at, updated_at)`
- `rate_limit_attempts (id, scope, identifier, attempted_at, INDEX(scope, identifier, attempted_at))`
- `portal_access_logs (id, portal_type, token_hash sha256, ip_address, user_agent, path, packing_list_id, accessed_at)`
- `activity_logs (id, user_id, user_name, user_role, action, module, entity_type, entity_id, entity_label, detail, ip_address, created_at)`

**Constantes et règles** :
- Cookie de session : `COOKIE_NAME = "towt_session"`, durée 480 minutes (8 h), signé HS256 via `URLSafeTimedSerializer` et `SECRET_KEY`.
- Hash mots de passe : `bcrypt` via `passlib.CryptContext(schemes=["bcrypt"], deprecated="auto")`.
- Refus de démarrer en production si :
  - `SECRET_KEY` dans `{"towt_secret_key_change_in_production_2025", "change_me", "changeme", "secret"}` ou < 32 caractères.
  - `DATABASE_URL` contient `towt_secure_2025`.
- Scopes de rate limit :
  - `login` : compteur sur IP, déclenche un verrou progressif après plusieurs échecs (cf. `auth_router`).
  - `portal_token` : protège les portails clients d'un bruteforce de token.
- Tokens portail :
  - Stockés en clair côté `packing_lists.token` (UUID hex 24 car., validité 90 jours via `default_token_expiry()`).
  - **Jamais stockés en clair dans les logs d'accès** : on persiste `sha256(token)` dans `portal_access_logs.token_hash`.
- Force password change : si `must_change_password = True`, toute requête est redirigée vers `/admin/my-account/change-password` (sauf cette page et `/logout`).

**Endpoints** :
- `GET /login` — formulaire de login.
- `POST /login` — valide credentials + crée la session (cookie `towt_session`), pose `towt_csrf`, rate-limité.
- `GET /logout` — efface le cookie, supprime la session.
- `GET /admin/my-account` — profil personnel.
- `POST /admin/my-account/password` — changer mot de passe (vérifie l'ancien).
- `GET|POST /admin/my-account/change-password` — flow forcé.
- `POST /admin/my-account/language` — choix de langue UI (fr/en/es/pt-br/vi).

### 0.B — Vision chef de projet

**Besoin métier** : tracer qui fait quoi, contrôler les droits selon le métier de l'utilisateur, sécuriser les accès clients sans gérer de comptes.

**Périmètre** :
1. Connexion par couple identifiant / mot de passe — pas de SSO en v3.0.0.
2. Profils utilisateurs typés (cf. matrice §3.4).
3. Possibilité d'imposer un changement de mot de passe au premier login (création de compte par l'admin).
4. Portails clients sans compte, protégés par token unique à durée de vie limitée.
5. Audit des actions sensibles : login (ok/échec), création, modification, suppression.

**Règles de gestion** :
- Tout utilisateur a un et un seul rôle.
- Un compte désactivé (`is_active = false`) ne peut pas se connecter.
- Un mot de passe est stocké uniquement haché (bcrypt). Aucun stockage en clair, jamais affiché.
- La session expire après 8 h d'inactivité ; les sessions sont signées HMAC.
- Une URL portail expirée (`token_expires_at < now`) n'ouvre plus l'accès.

**Critères d'acceptance** :
- Tester le démarrage avec `SECRET_KEY = "secret"` → l'application **doit refuser** de démarrer.
- Tester 5 échecs de login successifs depuis la même IP → mécanisme de rate limit déclenché.
- Tester l'accès `/cargo` avec un rôle `marins` → 403.
- Tester l'accès à un portail packing list avec un token expiré → message d'erreur, pas de fuite de données.

**Risques** :
- Compromission de `SECRET_KEY` → invalidité de toutes les sessions, rotation obligatoire.
- Compromission d'un token portail → expiration à 90 jours limite l'exposition.
- Phishing : tracer les accès portail (table `portal_access_logs`) permet l'enquête.

---

## Module 1 — Authentification (router `auth_router`)

### 1.A — Vision développeur

**Fichier** : `app/routers/auth_router.py`.

**Routes** :
- `GET /login` — affiche `auth/login.html` (champ user/pass, lang switch).
- `POST /login` — formulaire `username`, `password` :
  1. Lookup `User` actif.
  2. Vérification bcrypt.
  3. Si OK : `create_session_token(user.id)` → cookie `towt_session` (httpOnly, secure, samesite=Lax, max-age = 28 800 s).
  4. Sinon : enregistrement `RateLimitAttempt(scope="login", identifier=ip)` + `ActivityLog(action="login_fail")`.
  5. Si limite atteinte : message d'erreur + délai.
- `GET /logout` — efface le cookie, log `action=logout`, redirige vers `/login`.

**Comportement** :
- Persistance des tentatives en table `rate_limit_attempts` (rotation lazy, pas de cron dédié).
- I18N : la page `auth/login.html` honore `lang` (defaulte `fr`), via `app/i18n`.

### 1.B — Vision chef de projet

**Besoin** : authentifier les collaborateurs internes avec un mécanisme robuste à un environnement non-cloud.

**Spécifications** :
- Champ identifiant : `username` (50 car max, unique).
- Champ mot de passe : libre, encodé en bcrypt avant stockage.
- Message d'erreur générique en cas d'échec (« identifiant ou mot de passe incorrect ») pour ne pas indiquer si le compte existe.
- Bouton « Se connecter », formulaire HTML standard (pas de JS lourd).
- Sélecteur de langue accessible avant login.

**Acceptance** :
- Login admin/towt2025 sur instance fraîche → accès dashboard.
- Login désactivé → refus.
- 5 échecs successifs → blocage temporaire + message.

---

## Module 2 — Administration (router `admin_router`)

### 2.A — Vision développeur

**Fichier** : `app/routers/admin_router.py` (1 796 lignes), `app/templates/admin/`.

**Sous-domaines couverts** :
1. **Utilisateurs** (CRUD + import CSV) : `/admin/users`, `/admin/users/create`, `/admin/users/{uid}/edit`, `/admin/users/{uid}` (DELETE), `/admin/users/import/template`, `/admin/users/import`.
2. **Navires** (CRUD) : `/admin/vessels/create`, `/admin/vessels/{vid}/edit`.
3. **Paramètres OPEX** (`opex_parameters`) : `/admin/opex/update`.
4. **Clôture de leg** (lock/unlock) : `/admin/legs/lock`, `/admin/legs/{lid}/unlock`.
5. **Exports** : `/admin/export/global`, `/admin/export/selective`, `/admin/export/files`.
6. **Purges DB** : `/admin/database/purge-selective`, `/admin/database/reset`, `/admin/database/stats`, `/admin/database/cleanup-*`.
7. **Paramètres** : `/admin/settings/language`, `/admin/settings/insurance/add|edit`, `/admin/settings/pipedrive/update`, `/admin/settings/pipedrive/test`.
8. **Mon compte** : `/admin/my-account`, `/admin/my-account/password`, `/admin/my-account/change-password`, `/admin/my-account/language`.
9. **Audit** : `/admin/activity-logs`.
10. **Variables CO₂ / MRV / Émissions** : `/admin/co2/update`, `/admin/mrv/update`, `/admin/emissions/update`.
11. **Import planning** (CSV) : `/admin/import/planning`.
12. **Mode maintenance** : `/admin/maintenance/enable`, `/admin/maintenance/disable`.

**Garde-fou** : router monté avec `Depends(require_admin)` (≈ rôles `administrateur` + `data_analyst`).

**Sécurité SQL injection** : toutes les opérations dynamiques utilisent `ALLOWED_TABLES` (whitelist) et `text(...).bindparams(...)`.

**Modèles écrits** : `users`, `vessels`, `opex_parameters`, `co2_variables`, `mrv_parameters`, `emission_parameters`, `insurance_contracts`, `legs` (champs `closure_*`), `activity_logs`.

**Constantes** :
- Liste des rôles (`ROLES`) et modules (`MODULES`) — cf. `permissions.py`.
- Variables CO₂ par défaut (`CO2_DEFAULTS`) : `towt_co2_ef = 1.5 gCO2/t.km`, `conventional_co2_ef = 13.7 gCO2/t.km`, `sailing_cargo_capacity = 1100 mt`, `nm_to_km = 1.852`.
- Variables MRV par défaut (`MRV_DEFAULTS`) : `avg_mdo_density = 0.845 t/m³`, `mdo_admissible_deviation = 2.0 mt`, `co2_emission_factor = 3.206 t CO₂/t fuel`.

### 2.B — Vision chef de projet

**Besoin métier** :
- Centraliser la gouvernance utilisateurs et flotte.
- Permettre l'administration sans intervention DBA (purges ciblées, exports complets).
- Configurer les paramètres globaux (OPEX, CO₂, MRV, assurances, intégration CRM).
- Verrouiller administrativement la clôture d'un voyage.
- Activer un mode maintenance affichant un bandeau à tous les utilisateurs.

**Spécifications fonctionnelles** :
- Formulaire utilisateur : `username`, `email`, `full_name`, `role` (liste à choix), `language` (fr/en/es/pt-br/vi), mot de passe (force password change activable), `is_active`.
- Import CSV utilisateurs avec template téléchargeable.
- Pour chaque navire : `code` (1..4), `name`, `imo_number`, `flag`, `dwt`, `capacity_palettes`, `default_speed`, `default_elongation`, `opex_daily_sea`, `max_palettes`.
- Paramètres OPEX : table `opex_parameters` (`parameter_name UNIQUE`, `parameter_value`, `unit`, `category`, `description`).
- Lock leg : passe `closure_status = locked`, renseigne `closure_approved_*`.
- Purge sélective : liste blanche de tables, possibilité de purge par module.
- Export global : ZIP de tous les CSV/Excel + dossier `attachments`.
- Settings Pipedrive : token + bouton « tester » (appel `httpx`).

**Règles de gestion** :
- Un administrateur ne peut pas supprimer son propre compte.
- Si `must_change_password = true`, l'utilisateur est forcé sur la page de changement à chaque requête tant qu'il n'a pas validé.
- Le mode maintenance pose un fichier-flag (cf. `app/maintenance.py`) → toute requête (sauf admin et login) reçoit 503.

**Acceptance** :
- Création d'un utilisateur, login, déconnexion, modification, désactivation.
- Maintenance ON → utilisateurs voient bandeau ; routes API renvoient 503.
- Export global → ZIP contient toutes les entités + fichiers attachés.

**Risques** : la purge sélective doit être réservée à des rôles précis (administrateur uniquement, pas data_analyst). À auditer.

---

## Module 3 — Dashboard et notifications

### 3.A — Vision développeur

**Fichiers** : `app/routers/dashboard_router.py`, `app/templates/dashboard.html`.

**Routes** :
- `GET /` — page d'accueil :
  - Liste des notifications non archivées (avec icône et label).
  - Cartes synthèse par module en fonction des accès (`has_any_access(user, module)`).
  - Vue rapide : prochains départs, escales en cours, claims ouverts, packing lists en cours, etc.
- `POST /notifications/cargo/{pl_id}/dismiss` — masque une notification cargo donnée.
- `POST /notifications/{nid}/toggle-read` — marque lu/non-lu.
- `POST /notifications/{nid}/archive` — archive.
- `POST /notifications/archive-read` — archive en lot tout ce qui est lu.
- `GET /captain` — alias dashboard armateur côté capitaine.

**Modèle** : `notifications (id, type, title, detail, link, is_read, is_archived, created_at, leg_id, order_id, packing_list_id)`.

**Types de notification** (`NOTIFICATION_TYPES`) avec icônes :
- `new_order` 📦
- `new_cargo_message` 💬
- `eosp` ⚓
- `sosp` ⛵
- `new_claim` ⚠️
- `eta_shift` 🕐

**Déclencheurs** :
- Création d'une commande commerciale → `new_order`.
- Message client portail cargo → `new_cargo_message`.
- Pose d'un événement SOF de type EOSP/SOSP par le capitaine → `eosp`/`sosp`.
- Ouverture d'un claim → `new_claim`.
- Décalage ETA/ETD via la modale ETA shift → `eta_shift`.

### 3.B — Vision chef de projet

**Besoin** : vue d'ensemble opérationnelle quotidienne, alertes contextuelles.

**Spécifications** :
- Bandeau de notifications en haut du dashboard.
- Notification cliquable → redirige vers l'écran lié (leg, packing list, claim, etc.).
- Possibilité d'archiver, marquer lu/non-lu.
- Le dashboard est filtré par le menu sidebar (un utilisateur ne voit que les modules autorisés).

**Acceptance** :
- En tant qu'opération, créer une commande → notification visible au prochain rafraîchissement.
- En tant que marin, le dashboard n'affiche pas les liens commerciaux/finance.

---

## Module 4 — Planning (router `planning_router`)

### 4.A — Vision développeur

**Fichier** : `app/routers/planning_router.py`, `app/templates/planning/*.html`.

**Modèles** :
- `vessels` (cf. §2 admin).
- `ports (id, locode UNIQUE 5 char, name, latitude, longitude, country_code 2 char, zone_code, is_shortcut)`.
- `legs` — voir détails ci-dessous.
- `planning_shares (id, token UNIQUE, year, vessel_code, origin_locode, destination_locode, legs_ids, lang, label, recipient_*, is_active, created_at, created_by)`.

**Schéma `legs`** (extrait `app/models/leg.py`) :
- Identité : `id`, `leg_code UNIQUE`, `vessel_id FK vessels`, `year`, `sequence`.
- Ports : `departure_port_locode FK ports.locode`, `arrival_port_locode FK ports.locode`.
- Calendrier référence : `etd_ref`, `eta_ref`.
- Prévisionnel : `etd`, `eta`.
- Réalisé : `atd`, `ata`.
- Navigation : `distance_nm`, `speed_knots` (déf. 8 nœuds), `elongation_coeff` (déf. 1.25), `computed_distance = distance_nm * elongation_coeff`, `estimated_duration_hours = computed_distance / speed_knots`.
- Statut : `status` ∈ {planned, in_progress, completed, cancelled}.
- Escale : `port_stay_days` (déf. 3).
- Clôture : `closure_status` ∈ {open, review, approved, locked}, `closure_reviewed_by/_at`, `closure_approved_by/_at`, `closure_notes`, `closure_pdf_path`.
- Timestamps : `created_at`, `updated_at`, `notes`.

**Génération du `leg_code`** :
```
leg_code = f"{vessel_code}{letter}{dep_country.upper()}{arr_country.upper()}{year_suffix}"
# letter = A..Z dérivée de sequence (1→A, 2→B…)
# year_suffix = dernier chiffre de l'année (2026→"6")
```

**Calcul navigation** (méthode `compute_navigation()`) :
- `computed_distance = round(distance_nm * elongation_coeff, 1)`
- `estimated_duration_hours = round(computed_distance / speed_knots, 1)` si `speed_knots > 0`

**Endpoints** :
- `GET /planning` — index : liste filtrée des legs (par année, navire, route, statut).
- `GET /planning/ports` — vue par port (entrées / sorties).
- `GET|POST /planning/legs/create` — création (formulaire HTML standard).
- `GET|POST /planning/legs/{leg_id}/edit` — édition.
- `DELETE /planning/legs/{leg_id}` — suppression.
- `GET /planning/export/csv` — export CSV.
- `GET /planning/api/gantt` — JSON pour rendu Gantt.
- `GET /planning/api/map` — JSON pour la carte (positions ports + tracé).
- `GET /planning/pdf/commercial` — PDF planning commercial (WeasyPrint).
- `POST /planning/pdf/commercial/share` — crée un partage et renvoie l'URL `SITE_URL/planning/share/{token}`.
- `GET /planning/pdf/commercial/shares` — historique des partages.
- `POST /planning/pdf/commercial/shares/{share_id}/toggle` — désactiver un partage.

**Route publique externe** : `GET /planning/share/{token}` (router `planning_ext_router`, sans auth).

**Permissions** : router monté avec `require_permission("planning", "C")` ; écritures vérifient `M`/`S` dans la route.

### 4.B — Vision chef de projet

**Besoin métier** : construire et tenir à jour le planning de la flotte sur l'année. Permettre la diffusion vers les commerciaux et partenaires.

**Périmètre** :
1. CRUD legs avec génération automatique du `leg_code`.
2. Vues : tableau, calendrier annuel, Gantt, carte.
3. Filtres : année, navire, port de départ, port d'arrivée, statut.
4. Export CSV.
5. Génération PDF commercial (planning calendrier) et partage par lien tracé.
6. Détection des conflits d'escale (deux navires au même port en même temps) — `port_conflicts.html`.

**Règles de gestion** :
- `leg_code` immutable une fois généré (sauf cas exceptionnel).
- `etd_ref/eta_ref` sont posés à la création et **ne bougent plus** (référence figée). Tout décalage est tracé via `ETAShift` (cf. module On Board).
- `speed_knots` et `elongation_coeff` ont des valeurs par défaut héritées du `Vessel`.
- Une suppression de leg en cascade ses dépendances (`order_assignments`, `operations`, `docker_shifts`, `finance`, `kpi`).

**Acceptance** :
- Créer un leg Fécamp→São Sebastião sur Anemos 2026 → code généré `1AFRBR6` (si A est le premier).
- Modifier la vitesse → recalcul automatique de la durée.
- Générer un PDF commercial, partager le lien, désactiver le partage → token publique 404.

**Risques** :
- Confusion entre etd_ref et etd (l'utilisateur peut écraser le prévisionnel par accident). UI doit clairement distinguer les 3 colonnes (ref / prévisionnel / réalisé).

---

## Module 5 — Commercial : commandes et clients (router `commercial_router`)

### 5.A — Vision développeur

**Fichier** : `app/routers/commercial_router.py`.

**Modèles** :
- `orders` (cf. `app/models/order.py`) :
  - `id`, `reference UNIQUE`, `client_name`, `client_contact`.
  - Cargo : `quantity_palettes`, `palette_format` (EPAL/USPAL/PORTPAL/IBC/BIGBAG/BARRIQUE120/BARRIQUE140), `weight_per_palette` (déf 0.8 t), `unit_price`, `thc_included` (bool), `description`.
  - Frais : `booking_fee`, `documentation_fee`.
  - Période souhaitée : `delivery_date_start`, `delivery_date_end`.
  - Préférences ports : `departure_locode`, `arrival_locode`.
  - Statut : `status` ∈ {non_affecte, reserve, confirme, annule}.
  - Totaux calculés : `total_price = qty*unit_price + booking_fee + doc_fee`, `total_weight = qty * weight_per_palette`.
  - Pièce jointe : `attachment_filename`, `attachment_path`.
  - CRM : `pipedrive_deal_id`.
  - **Affectation à un leg** : `leg_id FK legs` (source de vérité) — `assignments` (`order_assignments`) ne sert que d'audit historique pour splits multi-legs.
  - Liaison grille : `rate_grid_id`, `rate_grid_line_id`.
- `order_assignments (id, order_id, leg_id, assigned_at, confirmed, notes)` — table d'audit.

**Constantes** : `PALETTE_FORMATS` (avec `value`, `label`, `coeff`), `PALETTE_COEFF`.

**Endpoints** :
- `GET /commercial`, `/commercial/` — index commandes.
- `GET|POST /commercial/orders/create` — formulaire création.
- `GET|POST /commercial/orders/{oid}/edit` — édition.
- `DELETE /commercial/orders/{oid}` — suppression.
- `GET|POST /commercial/orders/{oid}/assign` — affectation à un leg (renseigne `leg_id`, crée éventuellement un `OrderAssignment`).
- `POST /commercial/orders/{oid}/upload` — joindre un fichier (`utils/file_validation.py`).
- `GET /commercial/orders/{oid}/attachment` — télécharger la pièce jointe.
- `DELETE /commercial/orders/{oid}/attachment` — supprimer la pièce jointe.

**Règles métier programmées** :
- `equivalent_epal = qty * palette_coeff` (slots EPAL équivalents) — sert au check de capacité du leg (≤ 850).
- `compute_total()` met à jour `total_price` et `total_weight` lors de save.
- Création/MAJ → écrit une `Notification(type="new_order")` si applicable.

### 5.B — Vision chef de projet

**Besoin** : gérer le pipeline de commandes (création, qualification, affectation à un voyage, suivi pièce jointe).

**Spécifications** :
- Formulaire commande : client, contact, palettes, format de palette, poids/palette, prix unitaire, frais booking/documentation, période souhaitée, ports préférés, description, pièce jointe.
- Statuts visibles : « non_affecte », « reserve », « confirme », « annule ».
- Bouton « Affecter à un leg » : liste des legs compatibles (route OK, fenêtre de date, capacité résiduelle suffisante).
- Lien vers Pipedrive si `pipedrive_deal_id` renseigné.

**Règles de gestion** :
- `total_price` recalculé automatiquement.
- `equivalent_epal` doit toujours être ≤ capacité résiduelle du leg cible avant affectation.
- Un client peut être créé « inline » lors de la commande (via module Pricing/Clients).

**Acceptance** :
- Créer une commande de 200 BARRIQUE140 → poids = 200 × 0.8 = 160 t (poids par défaut), equivalent_epal = 200 × 2.0 = 400.
- Affecter à un leg → leg.kpi.cargo_tons cumulatif mis à jour (cf. KPI module).

**Risques** :
- Confusion qty palettes vs equivalent EPAL → toujours afficher les deux dans l'UI.
- Suppression d'une commande affectée doit avertir et nettoyer la stowage liée.

---

## Module 6 — Pricing : clients, grilles tarifaires, offres (router `pricing_router`)

### 6.A — Vision développeur

**Fichier** : `app/routers/pricing_router.py`. Prefix : `/commercial/pricing`.

**Modèles** :
- `clients (id, name, client_type ∈ {freight_forwarder, shipper}, contact_*, address, country, notes, is_active, pipedrive_org_id, created_at, updated_at)`.
- `rate_grids (id, reference UNIQUE = RG-YYYY-NNNN, client_id, vessel_id, valid_from, valid_to, adjustment_index (déf 1.0), bl_fee, booking_fee, brackets_json, volume_commitment, status ∈ {draft, active, expired, superseded}, notes, created_at/by)`.
- `rate_grid_lines (id, rate_grid_id, pol_locode, pod_locode, leg_id, distance_nm, nav_days, opex_daily, base_rate, rates_json, rate_lt10/10to50/51to100/gt100 legacy, is_manual)`.
- `rate_offers (id, reference UNIQUE = RO-YYYY-NNNN, client_id, rate_grid_id, validity_date, status ∈ {draft,sent,accepted,rejected,expired}, document_filename, document_path, notes, sent_at, pipedrive_deal_id, created_at/by)`.

**Brackets par défaut** :
- **Shipper** (degressif) : `<50 (×1.10), 100 (×1.00), 200 (×0.80), 300 (×0.80), 400 (×0.80), 500 (×0.70), full=850 (×0.60)`.
- **Freight forwarder** : flat unique (×1.00).

**Formule tarifaire** (`RateGridLine.compute_rates(opex_daily, adjustment_index, brackets)`) :
```
nav_days   = distance_nm / (8 knots × 24h)
base_rate  = opex_daily × nav_days / 850
rate[k]    = base_rate × bracket[k].coeff × adjustment_index
```
La grille stocke aussi le résultat dans des colonnes legacy (`rate_lt10`, `rate_10to50`, `rate_51to100`, `rate_gt100`) pour rétro-compatibilité.

**Lookup par quantité** (`get_rate_for_quantity(qty)`) — bornes : <50, ≤100, ≤200, ≤300, ≤400, ≤500, sinon `full`. Si `flat` dans rates, renvoie `flat`.

**Endpoints (extraits)** :
- Clients : `/clients`, `/clients/create`, `/clients/{cid}/edit`, `/clients/create-inline`.
- Pipedrive : `/pipedrive/search`, `/pipedrive/org/{org_id}` (utils/pipedrive.py).
- API : `/api/legs-for-period`, `/api/compute-rates`, `/api/default-brackets`, `/api/rate-lookup`.
- Grilles : `/grids`, `/grids/create`, `/grids/{gid}`, `/grids/{gid}/edit`, `/grids/{gid}/delete`, `/grids/{gid}/activate`, `/grids/{gid}/recalculate`.
- Offres : `/grids/{gid}/offer/create`, `/offers`, `/offers/{oid}/download`, `/offers/{oid}/status`, `/offers/{oid}/create-order`.

### 6.B — Vision chef de projet

**Besoin métier** :
- Tarifer les routes commerciales selon le client (FF/Shipper), la période et le volume.
- Produire des offres commerciales documentées (PDF) liées à une grille.
- Convertir une offre acceptée en commande.

**Spécifications** :
- Création d'une grille tarifaire pour un client + période + navire (optionnel pour le lookup OPEX).
- Lignes route POL→POD avec distance orthodromique (saisie manuelle ou récupérée du leg).
- Calcul automatique des tarifs par bracket, possibilité de surcharge manuelle (`is_manual = true`).
- Statut grille : draft → active (au plus une active par client/période) → expired / superseded.
- Offre tarifaire : génération d'un document (Word/PDF), envoi (statut `sent`), suivi `accepted/rejected/expired`.
- Bouton « créer commande à partir de l'offre » → recopie les paramètres, pré-rempli formulaire commande.

**Règles de gestion** :
- Une grille `active` ne peut pas être modifiée sans la repasser en `draft` ou en créer une nouvelle (`superseded`).
- `volume_commitment` (shipper) : engagement minimum de palettes par commande, validé à la création.
- Si `pipedrive_org_id` rempli → liaison bidirectionnelle avec CRM via httpx.

**Acceptance** :
- Créer un client Shipper, grille FRFEC→BRSSO Anemos, distance 4 500 NM, OPEX 12 000 €/j → `nav_days ≈ 23.44`, `base_rate ≈ 12000 × 23.44 / 850 ≈ 330 €`.
- Vérifier rate 200 palettes = `base_rate × 0.80 × adjustment_index`.

**Risques** :
- Désynchronisation entre grille active et commandes en cours : un changement de grille active ne doit pas modifier rétroactivement les commandes déjà confirmées.

---

## Module 7 — Cargo : packing lists, BL, portail client (router `cargo_router`)

### 7.A — Vision développeur

**Fichiers** : `app/routers/cargo_router.py` (1 751 lignes), `app/templates/cargo/`.

Le module se compose de **deux routers** :
- `router` (prefix `/cargo`) — interne, authentifié, sous `require_permission("cargo", "C")`.
- `ext_router` (prefix `/p`) — **public**, accès par token portail.

**Modèles** :
- `packing_lists (id, order_id FK, token UNIQUE, token_expires_at, status ∈ {draft, submitted, locked}, created_at, updated_at, locked_at, locked_by)`.
- `packing_list_batches` — un lot par produit / type de palette ; champs détaillés ci-dessous.
- `packing_list_audit (id, packing_list_id, batch_id, field_name, old_value, new_value, changed_by, changed_at)` — audit complet de chaque modification (champ par champ).
- `packing_list_documents (id, packing_list_id, filename, file_path, file_size, uploaded_by, notes, created_at)`.
- `portal_messages (id, packing_list_id, sender_type ∈ {client, company}, sender_name, message, is_read, created_at)`.

**Schéma `packing_list_batches`** (extrait) :
- **Champs TOWT (préremplis)** : `voyage_id` (= leg_code), `vessel`, `loading_date`, `pol_code/name`, `pod_code/name`, `booking_confirmation` (= order reference), `freight_rate`, `bill_of_lading_id`, `wh_references_sku`, `additional_references`, `ams_hbl_id`, `isf_date`, `stackable`, `hold`, `un_number`.
- **Champs client (jaunes)** : `customer_name`, `freight_forwarder`, `code_transitaire`, `shipper_{name,address,postal,city,country}`, `po_number`, `customer_batch_id`, `notify_{name,address,postal,city,country}`, `consignee_{name,address,postal,city,country}`, `pallet_type`, `type_of_goods`, `description_of_goods`, `bio_products` (Yes/No), `cases_quantity`, `units_per_case`, `imo_product_class`, `pallet_quantity`, `length_cm`, `width_cm`, `height_cm`, `weight_kg`, `cargo_value_usd`.
- **Calculés** : `surface_m2 = L×W/10 000`, `volume_m3 = L×W×H/1 000 000`, `density = (weight_kg/1000)/surface_m2`.

**Calcul de complétude** (`PackingList.completion_pct`) — 22 champs requis × nb batches, %age rempli.

**Token portail** :
- Généré par `generate_token() = uuid.uuid4().hex[:24]`.
- Expire 90 jours après création (`default_token_expiry()`).

**Endpoints internes (extraits)** :
- `GET /cargo`, `/cargo/` — index.
- `POST /cargo/create` — crée une PL pour une commande donnée.
- `GET /cargo/{pl_id}` — détail.
- `POST /cargo/{pl_id}/lock`, `unlock`.
- `DELETE /cargo/{pl_id}`.
- `GET /cargo/{pl_id}/excel` — export Excel.
- `GET /cargo/{pl_id}/bol/{batch_id}` — génération BL Word (template `BILL_OF_LADING_TEMPLATE.docx`).
- `GET /cargo/{pl_id}/bol/view` — preview HTML du BL.
- `GET /cargo/{pl_id}/arrival-notice` — Arrival Notice PDF.
- `POST /cargo/{pl_id}/add-batch`, `/{pl_id}/edit`, `/{pl_id}/import-excel`.
- `GET /cargo/{pl_id}/history` — audit log de la PL.
- `GET /cargo/voyage/{leg_id}/excel` — export consolidé d'un leg.
- `POST /cargo/{pl_id}/messages/send`, `/messages/read`.

**Endpoints portail client (extraits, sans auth)** :
- `GET /p/{token}` — accueil portail.
- `GET /p/{token}/{privacy|packing|vessel|voyage|stowage|guide|documents|messages}`.
- `POST /p/{token}/documents/upload`, `GET /documents/{doc_id}/download`, `POST /documents/{doc_id}/delete`.
- `POST /p/{token}/messages/send`.
- `POST /p/{token}/batch/add`, `POST /save`, `DELETE /batch/{batch_id}`.
- `GET /p/{token}/template` — template Excel à télécharger.
- `POST /p/{token}/import` — import Excel rempli.

**Sécurité portail** :
- Token validé en début de chaque requête, expiration vérifiée.
- Toute action logguée dans `portal_access_logs` (`token_hash = sha256(token)`).
- Rate limit par token (scope `portal_token`).

### 7.B — Vision chef de projet

**Besoin métier** :
- Collaborer avec le client expéditeur (Shipper ou Freight Forwarder) pour produire une packing list complète avant chargement.
- Générer automatiquement Bill of Lading et Arrival Notice à partir des données saisies.
- Tracer chaque modification pour audit qualité.

**Périmètre fonctionnel** :
1. Création d'une PL liée à une commande (lien `Order.id`).
2. Génération d'un token portail unique (`uuid hex 24`, validité 90 j).
3. Envoi du lien `https://my.newtowt.eu/p/{token}` au client par e-mail (hors application).
4. Portail client : pages packing, voyage, navire, stowage, guide, documents, messagerie.
5. Côté interne : verrouillage / déverrouillage, export Excel, génération BL/Arrival Notice, historique d'audit.
6. Messagerie : conversation client ↔ compagnie, persistée en table `portal_messages` + notifications dashboard.

**Règles de gestion** :
- Une PL existe pour une et une seule commande (`order_id`).
- Plusieurs batches autorisés par PL (un par lot/type/IMO différent).
- Le statut `locked` empêche toute modification client et compagnie.
- Tout changement → ligne dans `packing_list_audit` (`field_name`, `old → new`, `changed_by`).
- Dimensions obligatoires : `length_cm`, `width_cm`, `height_cm`, `weight_kg`.
- Format BL prévu : `TUAW_{voyage_id}_{bl_no}`, 3 OBL.

**Acceptance** :
- Créer une PL pour ORD-2026-0001, envoyer le lien, le client renseigne 1 batch → completion_pct calculé.
- Verrouiller la PL → portail en lecture seule.
- Générer BL → fichier Word téléchargeable.

**Risques** :
- Token portail fuité → tracer l'IP via `portal_access_logs`, désactivation par expiration ou suppression PL.
- Import Excel mal formaté → validation stricte (cf. `utils/file_validation.py`).

---

## Module 8 — Escale (port call) (router `escale_router`)

### 8.A — Vision développeur

**Fichier** : `app/routers/escale_router.py`, `app/templates/escale/`.

**Modèles** :
- `escale_operations (id, leg_id, operation_type, action, planned_start/end, planned_duration_hours, actual_start/end, actual_duration_hours, intervenant, description, cost_forecast, cost_actual, created_at, updated_at)`.
- `docker_shifts (id, leg_id, hold, planned_start/end, actual_start/end, planned_palettes, actual_palettes, notes, cost_forecast, cost_actual)`. Propriétés : `planned_rate`, `actual_rate`, `rate_delta_pct`.
- `operation_crew` (M2M `escale_operations` ↔ `crew_members` pour embarquements/débarquements).

**Endpoints** :
- `GET /escale` — index par leg/escale.
- `POST /escale/legs/{lid}/port-status` — statut portuaire (libre, occupé, conflit).
- `POST /escale/legs/{lid}/lock`, `/unlock` — verrouillage administratif.
- `GET /escale/legs/{lid}/pdf` — export PDF de la timeline d'escale.
- `GET|POST /escale/operations/create`, `/operations/{op_id}/edit`, DELETE.
- `GET|POST /escale/dockers/create`, `/dockers/{ds_id}/edit`, DELETE.
- Billets (transport) : `POST /escale/tickets/create`, `GET /escale/tickets/{tid}/download`, DELETE.

### 8.B — Vision chef de projet

**Besoin métier** : organiser la vie portuaire — opérations (pilotage, remorquage, embarquements), équipes de manutention, coûts associés.

**Périmètre** :
1. Pour chaque leg : créer/éditer/supprimer des opérations (planned vs actual).
2. Affecter des membres d'équipage à une opération d'embarquement / débarquement.
3. Planifier les vacations dockers par cale (`hold`), suivre la cadence (palettes / heure) et l'écart.
4. Calculer le coût d'escale (prévision / réalisé) → consolidé dans Finance.
5. Imprimer la timeline pour le port agent.

**Règles de gestion** :
- L'écart de cadence (`rate_delta_pct`) doit être visible et alerter au-delà de ±20 %.
- Le statut portuaire détermine la visibilité dans la vue conflits (`port_conflicts.html`).
- Un docker shift ne peut pas dépasser la fenêtre escale (ATA→ATD).

**Acceptance** :
- Saisir une opération « Pilotage entrant », durée planifiée 2 h, réelle 2.5 h → durée réelle visible et coût mis à jour.
- Coût d'escale total = Σ opérations + Σ docker shifts + coût journalier à quai × jours.

---

## Module 9 — On Board / Captain (router `onboard_router`)

### 9.A — Vision développeur

**Fichier** : `app/routers/onboard_router.py` (2 201 lignes — le plus volumineux).

**Modèles couverts** :
- `sof_events` — événements Statement of Facts.
- `onboard_notifications` — alertes à l'équipage.
- `cargo_documents (id, leg_id, doc_type, title, data_json, created_*, updated_*)` + `cargo_document_attachments`.
- `onboard_attachments` — fichiers liés au leg (photos, rapports…).
- `eta_shifts` — historique des décalages ETA/ETD avec justification obligatoire.
- `onboard_messages`, `onboard_message_mentions` — messagerie interne (équipage ↔ shore) + mentions `@user`.

**Constantes** :
- `SOF_EVENT_TYPES` : 26 codes (EOSP, SOSP, FREE_PRATIQUE, NOR_RETENDERED, PILOT_*, TUG_*, FIRST_LINE, ALL_FAST, COMMENCE_LOADING, COMPLETED_LOADING, LOADING_SUSPENDED/RESUMED, CLAIM_DECLARED/UPDATED…).
- `CARGO_DOC_TYPES` : SOF, NOR, NOR_RT, HOLDS_CERT, KEY_MEETING, PRE_MEETING, LOP_FP/DELAYS/DOCUMENT/QTY/DEADFREIGHT/OTHER, MATES_RECEIPT.
- `ATTACHMENT_CATEGORIES` : photo, document, report, certificate, port_agent, bl_signed, letter_protest, other.
- `ETA_SHIFT_REASONS` : weather, mechanical, port_congestion, cargo_ops, crew, routing, speed_adjustment, port_stay_change, other.
- `MYTOWT_BOT_USERNAME = "mytowt_bot"` — utilisateur fictif pour messages système.

**Endpoints clés** :
- `GET /onboard` — dashboard armateur / capitaine (par navire / leg actif).
- SOF : `POST /onboard/sof/add`, `/sof/{event_id}/edit`, DELETE.
- Set ATA/ATD : `POST /onboard/set-ata`, `/set-atd` — pose les champs `legs.ata`/`atd`, crée éventuellement notification EOSP/SOSP.
- ETA shift : `POST /onboard/eta-shift` — calcule `shift_hours` (positive=retard, négative=avance), exige justification, recalcule legs aval (`legs_affected`).
- Notifications : `POST /onboard/notifications/{notif_id}/dismiss`, `/dismiss-all`.
- Pièces jointes : `POST /onboard/attachments/upload`, `GET /attachments/{att_id}/download`, DELETE.
- Documents cargo : `POST /onboard/doc/{doc_id}/attachments/upload`, `GET /onboard/doc/attachments/{att_id}/download`.
- Exports : `GET /onboard/sof/{leg_id}/excel`, `/sof/{leg_id}/pdf`, `/doc/{doc_id}/export/word`, `/doc/{doc_id}/export/pdf`.
- Édition document : `GET /onboard/doc/{leg_id}/{doc_type}`, `POST /onboard/doc/{leg_id}/{doc_type}/save`.
- Clôture : `POST /onboard/closure/{leg_id}/submit-review`, `/approve`, `/reopen`, `/lock`, `GET /onboard/closure/{leg_id}/pdf`.
- Messagerie : `POST /onboard/messages/post`, `/messages/{message_id}/delete`, `GET /onboard/messages/users` (JSON, pour @mentions).

**Mapping SOF → MRV** : `EOSP → departure`, `SOSP → arrival`, `ANCHORED → begin_anchoring`, `ANCHOR_AWEIGH → end_anchoring`. Un événement SOF de ce type peut générer automatiquement un `MrvEvent` (cf. module MRV).

**Workflow de clôture de leg** :
```
open → review (submit-review)
     → approved (approve)
     → locked (lock — verrou administratif, cf. admin_router)
     ← reopen (rétablit open)
```

### 9.B — Vision chef de projet

**Besoin métier** : permettre au capitaine et à l'équipage de tenir l'historique opérationnel du voyage à bord, de générer les documents portuaires officiels et de communiquer avec l'armateur en quasi-temps réel.

**Périmètre fonctionnel** :
1. Statement of Facts horodaté (date, heure, fuseau), chaque ligne typée.
2. Documents cargo personnalisables (NOR, LOPs, Mate's Receipt…) avec attachements.
3. Notifications poussées dans le dashboard armateur sur événements clés.
4. Journal des décalages ETA/ETD avec justification systématique (motif obligatoire dans `ETA_SHIFT_REASONS`).
5. Workflow de clôture à 4 états.
6. Messagerie type chat avec @mentions et bot système `mytowt_bot`.

**Règles de gestion** :
- Saisie d'un EOSP → MRV Departure créé automatiquement (si pas déjà présent).
- Saisie d'un SOSP → MRV Arrival créé automatiquement.
- ETA shift → notification dashboard `eta_shift` + recalcul cascade des legs suivants si applicable (`legs_affected`).
- Clôture verrouillée (`locked`) → seul un administrateur peut déverrouiller (admin_router).

**Acceptance** :
- Saisir EOSP sur leg en cours → notification ⚓ dans dashboard, MrvEvent créé.
- Modifier ETA de +12 h, motif « weather » → ligne dans `eta_shifts`, notification 🕐.
- Soumettre à revue, approuver, verrouiller → cycle complet.

---

## Module 10 — Crew / Équipage (router `crew_router`)

### 10.A — Vision développeur

**Fichier** : `app/routers/crew_router.py`.

**Modèles** :
- `crew_members (id, first_name, last_name, role ∈ {capitaine, second, chef_mecanicien, cook, lieutenant, bosco, marin, eleve_officier}, phone, email, is_active, is_foreign, nationality, passport_number, passport_expiry, visa_type, visa_expiry, schengen_status, notes)`.
- `crew_assignments (id, member_id, vessel_id, embark_date, disembark_date, embark_leg_id, disembark_leg_id, status, notes)`.
- `crew_tickets (id, member_id, leg_id, ticket_type ∈ {embarquement, debarquement}, transport_mode ∈ {train, avion, bus, voiture, covoiturage, autre}, ticket_date, ticket_reference, filename, file_path, file_size, notes, created_by, created_at)`.

**Constantes** :
- `CREW_ROLES` (8 rôles).
- `REQUIRED_ROLES = [capitaine, second, chef_mecanicien, cook, lieutenant, bosco]` — rôles à présence obligatoire à bord.
- `TRANSPORT_MODES` (6 modes).

**Propriétés calculées** :
- `passport_days_remaining`, `visa_days_remaining`.
- `compliance_status` : `expired` si jour négatif, `warning` si < 30 j, sinon `ok`.

**Endpoints** :
- `GET /crew`, `/crew/` — index avec filtres rôle / navire / statut.
- `GET|POST /crew/members/create`, `/members/{mid}/edit`, DELETE.
- `GET|POST /crew/members/{mid}/assign` — créer une période d'embarquement.
- `GET|POST /crew/assignments/{aid}/edit`, DELETE.
- `GET /crew/api/vessel/{vessel_id}` — équipage actif sur un navire (HTML fragment pour HTMX).
- `GET /crew/members/{mid}/calendar` — calendrier annuel heatmap.
- `GET /crew/compliance` — vue compliance passeport/visa.
- `GET /crew/border-police/{vessel_id}` — liste pour la police aux frontières.
- Billets : `POST /crew/tickets/create`, `GET /crew/tickets/{tid}/download`, DELETE.

### 10.B — Vision chef de projet

**Besoin métier** : gérer le pool équipage (44 marins importés via `scripts/import_crew.py`), planifier les embarquements / débarquements, suivre la conformité administrative (passeport/visa Schengen) et produire la liste destinée à la police aux frontières (PAF).

**Périmètre** :
1. Fiche marin avec rôle, contact, identité (passeport, visa).
2. Affectation = période [embark_date, disembark_date] sur un navire, optionnellement attachée à des legs.
3. Vue calendrier annuel par marin (heatmap d'occupation).
4. Vue compliance : tous les marins en alerte (passeport/visa < 30 j ou expiré).
5. Liste PAF : un export par navire (membres actuellement embarqués, infos identité).
6. Billets de transport pour embarquements / débarquements (PDF/IMG joints).

**Règles de gestion** :
- Un marin avec `is_foreign = true` doit avoir nationalité + passeport.
- `compliance_status = expired` doit empêcher l'affectation à un voyage international.
- `REQUIRED_ROLES` : un navire ne peut pas appareiller sans capitaine, second, chef mécanicien, cook, lieutenant, bosco actifs.

**Acceptance** :
- Importer 44 marins depuis CSV.
- Affecter capitaine X sur Anemos du 01/02/2026 au 01/04/2026 → calendrier rempli.
- Marquer passeport expirant dans 20 j → marin remonte dans `compliance` avec statut `warning`.

---

## Module 11 — Finance (router `finance_router`)

### 11.A — Vision développeur

**Fichier** : `app/routers/finance_router.py`.

**Modèles** :
- `port_configs (id, port_locode UNIQUE, accessible, port_cost_total, cost_per_palette, daily_quay_cost, notes, updated_at)`.
- `opex_parameters` — saisis via admin.
- `leg_finances` — un enregistrement par leg :
  - `revenue_forecast/_actual`, `sea_cost_forecast/_actual`, `port_cost_forecast/_actual`, `quay_cost_forecast/_actual`, `ops_cost_forecast/_actual`, `result_forecast/_actual`, `margin_rate_forecast/_actual`, `claims_cost`, `notes`.
- `insurance_contracts` (cf. admin) — Hull/DIV/War Risk, P&I.

**Formule de calcul** (`LegFinance.compute()`) :
```
total_f = sea_cost_forecast + port_cost_forecast + quay_cost_forecast + ops_cost_forecast
total_a = sea_cost_actual + port_cost_actual + quay_cost_actual + ops_cost_actual + claims_cost
result_forecast = revenue_forecast - total_f
result_actual   = revenue_actual   - total_a
margin_rate_forecast = result_forecast / revenue_forecast * 100
margin_rate_actual   = result_actual   / revenue_actual   * 100
```

**Endpoints** :
- `GET /finance` — vue agrégée flotte/leg.
- `GET|POST /finance/legs/{leg_id}/edit` — saisie financière par leg.
- `GET /finance/ports`, `/finance/ports/search` — config portuaire.
- `GET|POST /finance/ports/{locode}/edit` — édition coûts portuaires.
- `GET /finance/export/csv` — export CSV.

### 11.B — Vision chef de projet

**Besoin métier** : suivi de profitabilité prévisionnel vs réalisé par leg, configuration des coûts portuaires, intégration des coûts opérationnels et des claims.

**Spécifications** :
- Pour chaque leg : revenus prévisionnel/réalisé (issus des commandes), coûts mer (OPEX × durée navigation), coûts port (issus de `port_configs` + opérations d'escale), coût d'escale (`daily_quay_cost × port_stay_days`), coûts opérations cargo, coûts claims.
- Recalcul automatique du résultat et du taux de marge.
- Export CSV exhaustif (flotte × année).

**Règles de gestion** :
- Coût claims = somme des `company_charge` des claims affectés au leg.
- Seuls `administrateur` et `data_analyst` peuvent modifier (`M`/`S`).
- Les autres rôles n'ont pas accès au module (matrice).

---

## Module 12 — KPI (router `kpi_router`)

### 12.A — Vision développeur

**Fichier** : `app/routers/kpi_router.py`.

**Modèle** : `leg_kpis (id, leg_id UNIQUE, cargo_tons, created_at, updated_at)`.

**Variables CO₂** (table `co2_variables`, valeurs courantes via `is_current=true`) :
- `towt_co2_ef = 1.5 gCO2/t.km` (facteur émission TOWT).
- `conventional_co2_ef = 13.7 gCO2/t.km` (référence conventionnelle).
- `sailing_cargo_capacity = 1100 mt`.
- `nm_to_km = 1.852`.

**Calcul CO₂ évité par leg** :
```
distance_km   = computed_distance (NM) × 1.852
co2_towt      = cargo_tons × distance_km × towt_co2_ef  (en g)
co2_conv      = cargo_tons × distance_km × conventional_co2_ef
co2_avoided   = co2_conv - co2_towt
```

**Endpoints** :
- `GET /kpi` — synthèse flotte (tonnage, CO₂ évité, par client).
- `POST /kpi/legs/{leg_id}/cargo` — saisie manuelle du tonnage.
- `GET /kpi/export/csv`.
- `POST /kpi/sync-cargo` — synchronisation automatique depuis les commandes affectées.
- `GET /kpi/certificate` — certificat carbone (PDF) par client / période.
- `GET /kpi/certificate/clients` — liste clients éligibles.

### 12.B — Vision chef de projet

**Besoin métier** : produire les KPI environnementaux et commerciaux. Délivrer aux clients un certificat d'évitement carbone valorisable RSE/CSRD.

**Spécifications** :
- Tonnage cargo synchronisable depuis les commandes (somme `total_weight` des commandes affectées au leg).
- Certificat carbone : PDF nominatif, par client, sur une période donnée, valorisant les tCO₂ évitées proportionnellement aux commandes du client.
- Historique des variables CO₂ (effective_date, is_current) pour traçabilité réglementaire.

---

## Module 13 — MRV : Monitoring, Reporting, Verification (router `mrv_router`)

### 13.A — Vision développeur

**Fichier** : `app/routers/mrv_router.py`.

**Modèles** :
- `mrv_parameters (id, parameter_name UNIQUE, parameter_value, unit, description)` avec défauts `MRV_DEFAULTS`.
- `mrv_events` — événement MRV par leg :
  - Identification : `event_type ∈ {departure, arrival, at_sea, begin_anchoring, end_anchoring}`, `timestamp_utc`, `leg_id`, optionnellement `sof_event_id`.
  - Compteurs DO : `port_me_do_counter`, `stbd_me_do_counter`, `fwd_gen_do_counter`, `aft_gen_do_counter`.
  - Déclaration : `rob_mt`, `cargo_mrv_mt`.
  - Bunkering (au départ) : `bunkering_qty_mt`, `bunkering_date`.
  - Position : `latitude_deg/min/ns`, `longitude_deg/min/ew`.
  - `distance_nm`.
  - Calculés : `me_consumption_mdo`, `ae_consumption_mdo`, `total_consumption_mdo`, `rob_calculated`.
  - Qualité : `quality_status ∈ {pending, ok, warning, error}`, `quality_notes`.

**Mapping SOF→MRV** (`SOF_TO_MRV_MAP`) : `EOSP→departure`, `SOSP→arrival`, `ANCHORED→begin_anchoring`, `ANCHOR_AWEIGH→end_anchoring`.

**Endpoints** :
- `GET /mrv`, `/mrv/` — index.
- `GET /mrv/leg/{leg_id}` — détail des événements d'un leg.
- `POST /mrv/events/add` — ajout manuel.
- `POST /mrv/events/from-sof` — création depuis SOF existant.
- `POST /mrv/events/{event_id}/edit`, DELETE.
- `POST /mrv/params/save` — édition des paramètres globaux MRV.
- `GET /mrv/export/dnv-csv` — export au format DNV (auditeur).
- `GET /mrv/export/carbon-report` — rapport carbone consolidé.
- `POST /mrv/leg/{leg_id}/recalculate` — recalcule consommations et ROB pour le leg.

**Calcul de consommation** (entre deux événements consécutifs) :
```
me_consumption_mdo = (Δport_me_do + Δstbd_me_do) × mdo_density
ae_consumption_mdo = (Δfwd_gen_do + Δaft_gen_do) × mdo_density
total              = me + ae
rob_calculated     = rob_précédent + bunkering - total
```
Si |rob_calculated - rob_mt déclaré| > `mdo_admissible_deviation` (2 mt) → `quality_status = warning`.

### 13.B — Vision chef de projet

**Besoin métier** : satisfaire l'obligation réglementaire MRV (UE) — déclarer pour chaque voyage la consommation fuel et les émissions CO₂. Auditable par DNV ou équivalent.

**Périmètre** :
1. Configurer les paramètres globaux (densité, déviation admissible, facteur CO₂).
2. Pour chaque leg : enchaîner les événements (departure / at_sea / arrival / anchoring).
3. Recalcul automatique des consommations entre événements.
4. Exports CSV/PDF normalisés.

**Règles de gestion** :
- Chaque MrvEvent doit avoir un timestamp UTC.
- Les compteurs DO sont cumulatifs depuis la mise en exploitation du navire — un Δ négatif est invalide (`quality_status = error`).
- Bunkering uniquement sur événements `departure`.
- Tout écart > 2 mt entre ROB déclaré et calculé → warning + commentaire libre.

**Acceptance** :
- Saisir Departure → At Sea → At Sea → Arrival pour un leg → consommations cohérentes.
- Export DNV CSV → fichier conforme à la spec auditeur.

---

## Module 14 — Claims (router `claim_router`)

### 14.A — Vision développeur

**Fichier** : `app/routers/claim_router.py`.

**Modèles** :
- `claims (id, reference UNIQUE, claim_type ∈ {cargo, crew, hull}, status ∈ {open, declared, instruction, accepted, refused, closed}, vessel_id, leg_id, order_assignment_id (cargo), crew_member_id (crew), context ∈ {loading, navigation, unloading, quay}, incident_date, incident_location, description, cargo_zone, guarantee_type ∈ {pi, hull_div, war_risk}, responsibility ∈ {company, third_party, pending, none}, provision_amount, franchise_amount, indemnity_amount, company_charge, currency=EUR, sof_event_id, declared_by, closed_at, notes)`.
- `claim_documents (id, claim_id, doc_type ∈ {photo, survey, correspondence, invoice, other}, title, filename, file_path, notes, uploaded_by, created_at)`.
- `claim_timeline (id, claim_id, action_type ∈ TIMELINE_ACTION_TYPES, title, description, old_value, new_value, filename, file_path, actor, action_date, created_at)`.

**Endpoints** :
- `GET /claims`, `/claims/` — index.
- `GET|POST /claims/create`.
- `GET /claims/{claim_id}` — détail (timeline + documents).
- `POST /claims/{claim_id}/status` — changement statut (génère ligne timeline).
- `POST /claims/{claim_id}/finance` — saisie financière.
- `POST /claims/{claim_id}/timeline` — ajout entrée libre.
- `POST /claims/{claim_id}/document` — upload pièce.
- `GET /claims/{claim_id}/document/{doc_id}/download`, DELETE.
- `GET /claims/{claim_id}/timeline/{tl_id}/download` — téléchargement d'un attachement timeline.
- `POST /claims/{claim_id}/guarantee` — affectation à une garantie d'assurance.
- `GET /claims/{claim_id}/declaration/pdf` — lettre de déclaration à l'assureur.

**Règle « auto-zone »** : pour un claim cargo, `cargo_zone` est auto-rempli depuis le `stowage_plan` du batch concerné (cf. Stowage).

### 14.B — Vision chef de projet

**Besoin métier** : gérer les sinistres tout au long de leur cycle de vie (ouverture → déclaration assureur → instruction → indemnisation/refus → clôture), tracer chaque échange et chaque document.

**Périmètre** :
1. Trois familles de claims : cargo (P&I), crew (P&I), hull (Hull/DIV/War Risk).
2. Pour chaque claim : description, contexte (chargement / navigation / déchargement / à quai), incident_date/location.
3. Documents probants typés (photo, survey, correspondance, facture).
4. Timeline avec horodatage et auteur.
5. Provision, franchise, indemnité, reste à charge compagnie → impacte Finance (`leg_finances.claims_cost`).
6. PDF de déclaration formelle à l'assureur.

**Règles de gestion** :
- `responsibility = company` → impact financier sur le leg ; sinon non.
- `closed_at` rempli automatiquement au passage en `closed`.
- Lien optionnel à un SOF event (preuve horodatée).
- Pour les claims cargo, la `cargo_zone` permet de retracer la position dans la cale au moment du sinistre.

**Acceptance** :
- Ouvrir claim cargo, lier à OrderAssignment, joindre une photo, déclarer à l'assureur → timeline complète + PDF déclaration.
- Saisir provision 5 000 €, franchise 1 000 €, indemnité 3 000 € → `company_charge = 5 000 - 3 000 = 2 000 €` (à valider selon règle métier).

---

## Module 15 — Stowage Plan (router `stowage_router`)

### 15.A — Vision développeur

**Fichier** : `app/routers/stowage_router.py`. Permissions enforcées **par endpoint** (pas globalement sur le router).

**Modèle** : `stowage_plans (id, leg_id, batch_id, zone_code, pallet_quantity, pallet_format, weight_total_kg, is_dangerous (0/1), imo_class, is_oversized (0/1), stackable (0/1), assigned_by, assigned_at, updated_at)`.

**Structure d'un navire** (identique sur les 4) :
- 2 cales (AR/AV) × 3 ponts (INF/MIL/SUP) × 3 blocs (AR/MIL/AV) = **18 zones**.
- Code zone : `{DECK}_{HOLD}_{BLOCK}` (ex. `INF_AR_MIL`).
- Surfaces et résistances : cf. `ZONE_DEFINITIONS` dans `app/models/stowage.py` (m² et t/m²).

**Ordre de chargement** (`LOADING_ORDER`) : AR→AV, bas→haut, de `INF_AR_AR` (1) à `SUP_AV_AV` (18).

**Zones réservées** (`DANGEROUS_ZONES = ["SUP_AV_AR", "SUP_AV_MIL", "SUP_AV_AV"]`) :
- Marchandises dangereuses (IMO class renseignée).
- Cargaisons hors gabarit (basket > 380×150×220 cm).

**Basket / panier** :
- Dimensions : 380×150 cm × 2.2 m hauteur.
- CMU 5.1 t, tare 2.2 t.

**Capacités par zone par format** (`ZONE_CAPACITIES`) : map zone → format pallet → (count simple, count gerbé). Cf. `app/models/stowage.py` pour les 18×7 valeurs exactes (à partir de `easy_chargement_navire_complet.xlsx`).

**Helpers** :
- `get_zone_capacity(zone, format, stackable)` — count maximal selon format et gerbage.
- `get_zone_max_weight(zone)` = surface × résistance (en tonnes).
- `is_oversized(L, W, H)` — vrai si > basket.
- `is_dangerous(imo_class)` — vrai si non vide.
- `must_go_sup_av(batch)` — vrai si dangerous OU oversized.
- `suggest_zone(batch, occupied, format)` — algorithme glouton qui parcourt `LOADING_ORDER` (en excluant ou priorisant `DANGEROUS_ZONES`) et choisit la première zone où la quantité ET le poids résiduel suffisent.

**Endpoints** :
- `GET /stowage/leg/{leg_id}` — plan d'arrimage du leg (vue éditable).
- `POST /stowage/leg/{leg_id}/assign/{batch_id}` — affecte un batch à une zone.
- `POST /stowage/leg/{leg_id}/auto-assign` — applique l'algorithme glouton à tous les batches non placés.
- `POST /stowage/leg/{leg_id}/move/{plan_id}` — déplace une assignation.
- `POST /stowage/leg/{leg_id}/unassign/{plan_id}` — retire une assignation.
- `GET /stowage/leg/{leg_id}/print` — impression PDF du plan.
- `GET /stowage/onboard/{leg_id}` — vue lecture seule pour le bord.
- `GET /stowage/api/leg/{leg_id}/zones` — JSON occupations par zone (pour SVG).
- `GET /stowage/api/leg/{leg_id}/batch/{batch_id}/position` — position d'un batch (pour claims).

**Classes IMO** : 27 entrées (`IMO_CLASSES`), classes 1.1 à 9.

### 15.B — Vision chef de projet

**Besoin métier** : planifier physiquement le chargement du navire en respectant les contraintes de sécurité (matières dangereuses, hors-gabarit), de stabilité (poids par cale) et d'efficacité opérationnelle (ordre de chargement / déchargement).

**Spécifications** :
1. Vue plan navire (18 zones) avec occupation actuelle (palettes / poids).
2. Bouton « Auto-assignation » : applique l'algorithme glouton sur tous les batches du leg.
3. Drag & drop ou formulaires pour assigner/déplacer manuellement.
4. Indicateurs : taux d'occupation par cale, poids total, CG approximatif.
5. Affichage des batches contraints (dangerous, oversized) en surbrillance.
6. Impression PDF pour passation à bord.
7. Vue lecture seule à bord (`/stowage/onboard/{leg_id}`).

**Règles de gestion** :
- Une zone ne peut pas dépasser sa capacité palettes (selon format et gerbage).
- Une zone ne peut pas dépasser son poids max = surface × résistance.
- Les batches dangereux ou hors-gabarit doivent aller en `SUP_AV_*`.
- Si aucune zone admissible → `suggest_zone()` retourne `None` et l'UI doit alerter.
- Toute modification est tracée (`assigned_by`, `updated_at`).

**Acceptance** :
- Auto-assigner 500 palettes EPAL sur Anemos → remplissage AR-AV, bas-haut, sans toucher SUP_AV.
- Ajouter un batch IMO class 3 → forcé en SUP_AV.
- Tenter un batch oversized → forcé en SUP_AV.

---

## Module 16 — Tracking (positions navire) (router `tracking_router`)

### 16.A — Vision développeur

**Fichier** : `app/routers/tracking_router.py`. **Pas de require_permission** car alimenté par Power Automate (Microsoft) — protégé uniquement par `TRACKING_API_TOKEN` via le dependency `require_tracking_token` (en-tête `X-API-Token`).

**Modèle** : `vessel_positions (id, vessel_id, leg_id (nullable), latitude, longitude, sog, cog, recorded_at, source, import_batch, created_at)` avec `UniqueConstraint(vessel_id, recorded_at)` pour éviter les doublons.

**Endpoints** :
- `POST /api/tracking/upload` (token requis) — accepte un CSV (`Date, Lat, Lon, SOG, COG, Source` ou équivalent), insère les points en évitant les doublons. Si `TRACKING_API_TOKEN` vide → 503.
- `GET /api/tracking/positions/{vessel_id}` — points sur une période (query params `from`, `to`).
- `GET /api/tracking/latest` — dernière position connue de chaque navire.
- `GET /api/tracking/leg/{leg_id}/track` — points associés au leg (pour la carte de planning et la vue MRV).
- `GET /api/tracking/navigation-kpis` — KPI navigation (distance réelle, vitesse moyenne par leg).

### 16.B — Vision chef de projet

**Besoin métier** : suivre la position en quasi temps réel des navires (sources Starlink, AIS, satcom) pour informer commerciaux, clients et calcul MRV.

**Spécifications** :
1. Recevoir un flux CSV automatisé via Power Automate (push).
2. Stocker chaque point unique (vessel + timestamp).
3. Rattacher automatiquement au `leg_id` actif au moment du point (si ATA pas encore atteint).
4. Fournir des endpoints REST pour les vues internes (planning map, MRV).

**Règles de gestion** :
- Le token API doit être rotaté tous les trimestres.
- Une absence de token (`TRACKING_API_TOKEN=""`) ferme l'endpoint en 503.
- Les points orphelins (pas de leg) sont autorisés mais marqués `leg_id = NULL`.

---

## Module 17 — API publiques

### 17.A — Vision développeur

**Routers publics (sans auth)** :
1. `api_ports` (prefix `/api/ports`) :
   - `GET /api/ports/search?q=...` — autocomplete UN/LOCODE (via Nominatim OSM si besoin).
   - `GET /api/ports/shortcuts` — ports favoris (`SHORTCUT_PORTS = ["FRFEC", "BRSSO"]`).
   - `GET /api/ports/next-clocks` — fuseaux horaires des prochains ports d'arrivée (pour l'horloge sidebar).
2. `planning_ext_router` (prefix `/planning/share`) :
   - `GET /planning/share/{token}` — affichage planning filtré sans auth ; tracé dans `portal_access_logs`.
3. `cargo_ext_router` (prefix `/p`) — cf. §7.A.
4. `tracking_router` (prefix `/api/tracking`) — cf. §16.

**Sécurité** :
- Tous logguent via `portal_access_logs` (sauf `tracking_router` qui dépend du token).
- Rate limit applicable.

### 17.B — Vision chef de projet

**Besoin** : autocomplétion ports, partage de plannings, portails cargo, ingestion tracking. Aucun de ces endpoints ne doit exposer de données utilisateur.

**Acceptance** :
- `GET /api/ports/search?q=fec` → renvoie FRFEC en tête.
- `GET /planning/share/<token-invalide>` → 404 et log d'accès.

---

## Module 18 — Internationalisation (I18N)

### 18.A — Vision développeur

**Fichier** : `app/i18n/__init__.py` (et fichiers de langue à compléter).

**Langues supportées** : `fr` (par défaut), `en`, `es`, `pt-br`, `vi`.

**Préférence utilisateur** : `users.language`. Posée à la création et modifiable via `/admin/my-account/language`.

**Filtre Jinja2** : `|flag` convertit un code pays (2 lettres) en emoji drapeau (cf. `templating.py`).

**Helper template** : fonction `t(key, lang)` exposée à Jinja, retourne la traduction.

### 18.B — Vision chef de projet

**Besoin métier** : la flotte cargo opère sur des routes internationales avec des marins étrangers ; l'UI doit pouvoir basculer en cinq langues, particulièrement le portugais brésilien (axe France-Brésil) et le vietnamien (marins).

**Spécifications** :
- Toutes les chaînes UI doivent passer par `t('clé', lang)`.
- Sélecteur de langue accessible depuis la page de login ET « Mon compte ».
- Drapeaux affichés via filtre `|flag`.

---

## Module 19 — Design system et template engine

### 19.A — Vision développeur

**Fichier de tokens canonique** : `Design/newtowt-design-tokens.json` (W3C Design Tokens Format).

**CSS** : un seul fichier `app/static/css/app.css` (NEWTOWT charte « Nouvelle Étoile »).

**Variables canoniques** (extrait) :
```
--newtowt-teal:  #0D5966   /* primaire 60% */
--newtowt-vert:  #87BD29   /* secondaire 20% */
--newtowt-cuivre:#B47148   /* accent 10% */
--newtowt-sable: #EFE6D6   /* neutre 10% */
--newtowt-bleu-marine: …
```

**Alias rétro-compat** : `--towt-blue → --newtowt-teal`, `--towt-green → --newtowt-vert`, etc.

**Classes utilitaires** : `.card`, `.card-title`, `.alert`, `.alert-success`, `.alert-error`, `.field-label`, `.field-value`, `.btn-outline`, `.leg-code`, `.account-grid`.

**Fonts** : `Manrope` (UI/PDF, weights 300..800) + `DM Serif Display` (accent serif). Chargées depuis Google Fonts dans `base.html`. Fallback `system-ui`.

**HTMX** :
- Injection automatique du token CSRF dans tous les `htmx:configRequest`.
- Sidebar responsive (collapse/expand par breakpoint).
- Toast container (`#toast-container`).
- Anti double-submit sur tous les forms (désactivation `submit` 5 s).
- Horloge sidebar (UTC + Paris + prochains ports d'arrivée, fetch toutes les 30 s via `/api/ports/next-clocks`).
- Helpers de conversion de fuseau (objet JS global `TOWT_TZ`).

### 19.B — Vision chef de projet

**Besoin** : identité visuelle cohérente avec la charte NEWTOWT, expérience riche sans JS lourd.

**Règles de gestion** :
- Toute évolution graphique commence par `Design/newtowt-design-tokens.json` puis se propage à `app.css`.
- Ratios chromatiques : 60% teal · 20% vert · 10% cuivre · 10% neutres.
- Pas de polices interdites (Segoe UI, Inter, Poppins).
- Logos NEWTOWT versionnés dans `app/static/img/` + `Design/`.

---

## Module 20 — Outillage opérationnel (scripts)

### 20.A — Vision développeur

**Scripts opérationnels** :
- `scripts/backup_db.sh` — `pg_dump` planifié, rotation selon `BACKUP_RETENTION_DAYS`. Cron suggéré : `0 2 * * *`.
- `scripts/import_crew.py` — import des 44 marins initiaux.
- `scripts/import_tonnage.py` — import des données de tonnage des navires.
- `scripts/purge_access_logs.py` — purge des `portal_access_logs` > N jours (RGPD).
- `scripts/seed_demo_data.py` — données de démo (utile en pré-prod et en démo commerciale).
- À la racine : `backup.sh`, `restore.sh`, `deploy.sh`, `update.sh` — orchestration VPS.

**Procédures clés** :
- **Restart applicatif** : `docker restart towt-app-v2`.
- **Restart full stack** : `docker-compose up -d` (idempotent).
- **Migrations** : exécuter un script Python ad hoc via `docker exec towt-app-v2 python3 -c "..."` qui se branche sur l'engine async et fait du `ALTER TABLE` paramétrique.
- **Fix permissions static** : `docker exec towt-app-v2 chmod -R 755 /app/app/static/` (sinon 403 sur `/static/`).

### 20.B — Vision chef de projet

**Besoin** : automatiser la sauvegarde, l'import initial, la purge RGPD, le déploiement.

**Procédure de reprise depuis zéro** :
1. Provisionner un VPS (OVH ou équivalent, 2 vCPU, 4 Go RAM, 40 Go SSD min).
2. Installer Docker + docker-compose.
3. Cloner le code, copier `.env.example` → `.env`, renseigner toutes les variables obligatoires.
4. `docker-compose up -d` → init du schéma via `Base.metadata.create_all`.
5. Restaurer le dump Postgres le plus récent (`scripts/restore.sh`).
6. Vérifier la version applicative (`/admin/settings`).
7. Lancer un smoke test : login admin, ouvrir chaque module, vérifier intégrité.

**Risques** :
- Restauration partielle (manque de pièces jointes uploadées). À pallier par sauvegarde du volume `/app/uploads` ou équivalent en plus du dump SQL.

---

# PARTIE III — ANNEXES

## A. Modèle de données — Liste exhaustive des tables et clés

### A.1 Référentiels et sécurité

| Table | PK | Champs clés | Contraintes |
|---|---|---|---|
| `users` | id | username UNIQUE, email UNIQUE, hashed_password, full_name, role, language, is_active, must_change_password | — |
| `vessels` | id | code UNIQUE 1..4, name UNIQUE, imo_number, flag, dwt, capacity_palettes, default_speed=8.0, default_elongation=1.25, opex_daily_sea, max_palettes=850, is_active | — |
| `ports` | id | locode UNIQUE 5 char, name, latitude, longitude, country_code 2 char, zone_code, is_shortcut | — |
| `port_configs` | id | port_locode UNIQUE FK ports.locode, accessible, port_cost_total, cost_per_palette, daily_quay_cost, notes | — |
| `opex_parameters` | id | parameter_name UNIQUE, parameter_value, unit, category, description | — |
| `co2_variables` | id | variable_name, variable_value, unit, description, effective_date, is_current | — |
| `mrv_parameters` | id | parameter_name UNIQUE, parameter_value, unit, description | — |
| `emission_parameters` | id | parameter_name UNIQUE, parameter_value, unit, description | — |
| `insurance_contracts` | id | guarantee_type UNIQUE ∈ {pi, hull_div, war_risk}, insurer_name, … | — |
| `activity_logs` | id | user_id, user_name, user_role, action, module, entity_type, entity_id, entity_label, detail, ip_address, created_at | INDEX user/action/module/created |
| `portal_access_logs` | id | portal_type ∈ {cargo, planning}, token_hash sha256, ip_address, user_agent, path, packing_list_id, accessed_at | INDEX token_hash |
| `rate_limit_attempts` | id | scope, identifier, attempted_at | INDEX (scope, identifier, attempted_at) |
| `notifications` | id | type, title, detail, link, is_read, is_archived, leg_id, order_id, packing_list_id, created_at | — |

### A.2 Planning & Tracking

| Table | PK | Champs clés |
|---|---|---|
| `legs` | id | leg_code UNIQUE, vessel_id, year, sequence, departure_port_locode, arrival_port_locode, etd_ref, eta_ref, etd, eta, atd, ata, distance_nm, speed_knots, elongation_coeff, computed_distance, estimated_duration_hours, status, port_stay_days, closure_* (5 champs) |
| `planning_shares` | id | token UNIQUE, year, vessel_code, origin_locode, destination_locode, legs_ids, lang, label, recipient_*, is_active |
| `vessel_positions` | id | vessel_id, leg_id (nullable), latitude, longitude, sog, cog, recorded_at, source, import_batch / UNIQUE (vessel_id, recorded_at) |

### A.3 Commercial

| Table | PK | Champs clés |
|---|---|---|
| `orders` | id | reference UNIQUE, client_name, client_contact, quantity_palettes, palette_format, weight_per_palette, unit_price, thc_included, description, booking_fee, documentation_fee, delivery_date_*, departure/arrival_locode, status, total_price/weight, attachment_*, pipedrive_deal_id, leg_id, rate_grid_id, rate_grid_line_id |
| `order_assignments` | id | order_id, leg_id, assigned_at, confirmed, notes |
| `clients` | id | name, client_type ∈ {freight_forwarder, shipper}, contact_*, address, country, is_active, pipedrive_org_id |
| `rate_grids` | id | reference UNIQUE, client_id, vessel_id, valid_from/to, adjustment_index, bl_fee, booking_fee, brackets_json, volume_commitment, status |
| `rate_grid_lines` | id | rate_grid_id, pol_locode, pod_locode, leg_id, distance_nm, nav_days, opex_daily, base_rate, rates_json, rate_lt10/10to50/51to100/gt100 (legacy), is_manual |
| `rate_offers` | id | reference UNIQUE, client_id, rate_grid_id, validity_date, status, document_*, sent_at, pipedrive_deal_id |

### A.4 Cargo

| Table | PK | Champs clés |
|---|---|---|
| `packing_lists` | id | order_id, token UNIQUE, token_expires_at, status, locked_at, locked_by |
| `packing_list_batches` | id | packing_list_id, batch_number, (champs TOWT) + (champs client) + (calculés surface_m2/volume_m3/density) |
| `packing_list_audit` | id | packing_list_id, batch_id, field_name, old_value, new_value, changed_by, changed_at |
| `packing_list_documents` | id | packing_list_id, filename, file_path, file_size, uploaded_by, notes |
| `portal_messages` | id | packing_list_id, sender_type, sender_name, message, is_read |

### A.5 Escale

| Table | PK | Champs clés |
|---|---|---|
| `escale_operations` | id | leg_id, operation_type, action, planned/actual_start/end, planned/actual_duration_hours, intervenant, description, cost_forecast, cost_actual |
| `docker_shifts` | id | leg_id, hold, planned/actual_start/end, planned/actual_palettes, notes, cost_forecast, cost_actual |
| `operation_crew` | (op_id, crew_id) | M2M |

### A.6 On Board / Captain

| Table | PK | Champs clés |
|---|---|---|
| `sof_events` | id | leg_id, event_type, event_label, event_date, event_time, event_time_tz, remarks |
| `onboard_notifications` | id | leg_id, category ∈ {crew, escale, cargo}, title, detail, is_read |
| `cargo_documents` | id | leg_id, doc_type, title, data_json |
| `cargo_document_attachments` | id | document_id, leg_id, title, filename, file_path, file_size, mime_type, uploaded_by |
| `onboard_attachments` | id | leg_id, category ∈ ATTACHMENT_CATEGORIES, title, filename, file_path, file_size, mime_type, description, uploaded_by |
| `eta_shifts` | id | leg_id, vessel_id, field_changed (eta/etd), old_value, new_value, shift_hours, reason, justification, legs_affected, created_by |
| `onboard_messages` | id | vessel_id, leg_id, author_user_id, author_name, author_username, is_bot, is_system, body, mentions |
| `onboard_message_mentions` | id | message_id, username, user_id, is_read |

### A.7 Crew

| Table | PK | Champs clés |
|---|---|---|
| `crew_members` | id | first_name, last_name, role, phone, email, is_active, is_foreign, nationality, passport_number, passport_expiry, visa_type, visa_expiry, schengen_status, notes |
| `crew_assignments` | id | member_id, vessel_id, embark_date, disembark_date, embark_leg_id, disembark_leg_id, status, notes |
| `crew_tickets` | id | member_id, leg_id, ticket_type, transport_mode, ticket_date, ticket_reference, filename, file_path, file_size, notes, created_by |

### A.8 Finance / KPI / MRV / Claims / Stowage

| Table | PK | Champs clés |
|---|---|---|
| `leg_finances` | id | leg_id UNIQUE, revenue/sea_cost/port_cost/quay_cost/ops_cost (forecast & actual), result/margin_rate (forecast & actual), claims_cost |
| `leg_kpis` | id | leg_id UNIQUE, cargo_tons |
| `mrv_events` | id | leg_id, sof_event_id, event_type, timestamp_utc, port/stbd_me_do, fwd/aft_gen_do, rob_mt, cargo_mrv_mt, bunkering_qty/_date, lat/lon (deg/min/ns/ew), distance_nm, me/ae/total_consumption_mdo, rob_calculated, quality_status/_notes |
| `claims` | id | reference UNIQUE, claim_type, status, vessel_id, leg_id, order_assignment_id, crew_member_id, context, incident_*, description, cargo_zone, guarantee_type, responsibility, provision_amount, franchise_amount, indemnity_amount, company_charge, currency, sof_event_id, declared_by, closed_at |
| `claim_documents` | id | claim_id, doc_type, title, filename, file_path, notes, uploaded_by |
| `claim_timeline` | id | claim_id, action_type, title, description, old_value, new_value, filename, file_path, actor, action_date |
| `stowage_plans` | id | leg_id, batch_id, zone_code, pallet_quantity, pallet_format, weight_total_kg, is_dangerous, imo_class, is_oversized, stackable, assigned_by, assigned_at |

## B. Procédure de reconstruction « from scratch »

Si **tout est perdu** (code, DB, serveur), suivre l'ordre :

1. **Récupérer ce document** comme spec de référence.
2. **Restaurer la charte** : `Design/newtowt-design-tokens.json` (PNG logos NEWTOWT à demander à la direction artistique si perdus).
3. **Scaffolder le projet** :
   ```bash
   mkdir mytowt && cd mytowt
   python3 -m venv .venv && source .venv/bin/activate
   # créer requirements.txt (cf. §2.1)
   pip install -r requirements.txt
   ```
4. **Recréer l'arborescence** (§2.2).
5. **Reconstituer le socle** : `config.py`, `database.py`, `auth.py`, `permissions.py`, `csrf.py`, `security_middleware.py`, `maintenance.py`, `templating.py`, `main.py`.
6. **Recréer les modèles** dans l'ordre des dépendances :
   - `user`, `port`, `vessel` (référentiels).
   - `leg` (FK ports/vessel).
   - `order`, `order_assignment` (FK leg).
   - `operation`, `docker_shift` (FK leg + M2M crew).
   - `crew_member`, `crew_assignment`, `crew_ticket`.
   - `packing_list`, `packing_list_batch`, audit, documents, portal_messages.
   - `sof_event`, `onboard_*`, `eta_shift`, `cargo_document*`.
   - `mrv_event`, `mrv_parameter`.
   - `co2_variable`, `emission_parameter`.
   - `claim`, `claim_document`, `claim_timeline`.
   - `commercial` (Client, RateGrid, RateGridLine, RateOffer).
   - `finance` (PortConfig, OpexParameter, LegFinance, InsuranceContract).
   - `kpi` (LegKPI), `stowage` (StowagePlan + constantes).
   - `tracking` (VesselPosition), `planning_share`, `notification`, `activity_log`, `portal_access_log`, `rate_limit`.
7. **Recréer les routers** (un par module), en câblant `require_permission` selon la matrice §3.4.
8. **Recréer les templates** Jinja2 (`base.html` + sous-dossier par module), avec sidebar, horloge, toast, anti-double-submit.
9. **Reconstituer `app.css`** depuis `newtowt-design-tokens.json` (variables CSS canoniques + alias rétro-compat).
10. **Dockerfile + docker-compose.yml** (cf. §2.4).
11. **Variables d'env** (`.env` à partir de `.env.example` + valeurs réelles).
12. **`docker-compose up -d`** → `init_db()` crée le schéma.
13. **Restaurer le dump** Postgres (le plus récent disponible) via `restore.sh`.
14. **Recréer le compte admin** si nécessaire (script Python ad hoc).
15. **Smoke test** des 10 modules (login admin/each_role + un GET et un POST par module).

## C. Procédure de sauvegarde / restauration

**Sauvegarde** :
- Dump quotidien Postgres : `pg_dump -U $POSTGRES_USER -d $POSTGRES_DB > /backups/mytowt_$(date +%Y%m%d).sql`.
- Rotation : `BACKUP_RETENTION_DAYS` jours.
- Sauvegarder en plus les **fichiers uploadés** (pièces jointes commandes, documents claim, attachments onboard, portail cargo).

**Restauration** :
- `cat dump.sql | docker exec -i towt-db psql -U $POSTGRES_USER -d $POSTGRES_DB`.
- Vérifier le compte admin (`SELECT id, username FROM users WHERE role='administrateur';`).

## D. Points d'attention / risques résiduels

1. **Pas d'Alembic exhaustif** : les migrations sont versionnées en mix Alembic + scripts SQL ad hoc. À normaliser sur Alembic pur lors de la reconstruction.
2. **Polices Google Fonts** : dépendance externe (fonts.googleapis.com). En cas de coupure, prévoir un fallback local.
3. **Pipedrive** : intégration optionnelle, panne externe non bloquante (catch `httpx`).
4. **Tracking** : un seul flux entrant via Power Automate ; un changement de fournisseur (Starlink → autre satcom) implique d'ajuster le parseur CSV.
5. **Token portails** : durée de vie 90 j, à renouveler manuellement par recréation de PL si besoin.
6. **Mode maintenance** : à toujours désactiver après intervention (`/admin/maintenance/disable`).
7. **Permission `data_analyst` sur admin** : ce rôle a accès à `/admin/settings` ; auditer régulièrement.
8. **Conflit d'escale** : si deux navires sont planifiés au même port en même temps, la vue `port_conflicts.html` doit alerter. Vérifier la couverture.

## E. Contacts et conventions

- **Contact sécurité** : `security@towt.eu` (publié dans `/.well-known/security.txt`).
- **Repository** : `juliengonde-5G/mytowt`.
- **Branche de référence pour cette note** : `claude/technical-documentation-recovery-eIUPG`.
- **Documentation projet additionnelle** : `CLAUDE.md`, `README.md`, `docs/v2/tech-debt-audit.md`.
- **Charte graphique source** : `Design/newtowt-design-tokens.json`.

---

# CONCLUSION

Cette note technique constitue le **socle de continuité opérationnelle** de `my_newtowt`. Elle décrit, pour chaque module :

- **(A) la dimension développeur** : modèle de données précis (tables, colonnes, types, contraintes), routes exposées, variables et constantes, règles métier implémentées, comportements observables.
- **(B) la dimension chef de projet** : besoin métier, périmètre fonctionnel, utilisateurs cibles, règles de gestion, critères d'acceptance, risques.

En cas de perte majeure (code, équipe, infrastructure), suivre la **Procédure de reconstruction « from scratch »** (Annexe B) en s'appuyant module par module sur la Partie II. Le respect strict :

- de la **matrice de permissions** (§3.4),
- des **conventions de nommage** (leg_code, références, tokens),
- de la **charte graphique NEWTOWT** (§3.5, §19),
- des **règles d'architecture** (HTMX + Jinja2, pas de framework JS, FastAPI async, PostgreSQL),

…garantit que la reconstruction conserve l'identité applicative et l'expérience utilisateur attendues.

> *« On garde le cap. Une nouvelle traversée commence. »*
> — NEWTOWT, mai 2026
