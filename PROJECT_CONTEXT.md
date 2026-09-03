# PROJECT_CONTEXT.md — mynewtowt

> Document de continuité de session, maintenu au fil des périodes de travail. Voir `CLAUDE.md` pour les consignes opérationnelles complètes, la stack technique détaillée, le glossaire maritime et les patterns critiques (base de données, permissions, MRV, sécurité) — ce document ne duplique pas ce contenu, il le référence.
>
> **Au début de chaque session** : lire ce document, résumer l'état du projet, identifier le travail non terminé, reprendre depuis le dernier contexte validé.

---

## 1. Vue d'ensemble & stack

Voir `CLAUDE.md` §"Stack technique" et §"Structure du dépôt" pour le détail complet. Résumé : FastAPI 0.115 / Python 3.12 / PostgreSQL 16 + SQLAlchemy 2 async / Alembic / HTMX 2 + Alpine.js + Jinja2 SSR / Caddy (reverse proxy + TLS auto) / Docker + docker-compose. Version courante : 3.11.0.

**Repo** : `C:\Users\YASMINPONCECOBOS\dev\mynewtowt`, remote GitHub `julien-newtowt/mynewtowt`. 112 migrations Alembic depuis le 2026-05-18 (~1,7 migration/jour — dépôt très actif).

---

## 2. Architecture — constats de la phase de découverte (2026-07-27)

### 2.1 Points fiables (vérifiés dans le code)

- **Middleware chain** (`app/main.py`) : CORS → SecurityHeaders → Maintenance → CSRF → ForcePasswordChange → ForceMfaForAdmin. ~40 routers enregistrés à plat (pas de sous-packages par audience), l'audience étant distinguée par préfixe d'URL + dépendance `require_permission`, pas par arborescence de fichiers.
- **Auth** : deux contextes indépendants (staff `towt_session` / client `towt_client_session`), bcrypt + itsdangerous. TTL de session dépendant du rôle : 14 jours pour `marins`/`manager_maritime` (connectivité satellite intermittente en mer) vs 8h pour les rôles de bureau — décodage avec le TTL max possible puis re-vérification post-décodage du TTL réel par rôle (gère proprement le cas d'un utilisateur rétrogradé avec un vieux cookie).
- **RBAC** : 9 rôles × 18 modules, matrice `_MATRIX` codée en dur dans `app/permissions.py` + overrides en base (table `role_permissions`, ARC-04, cache 60s, **fail-closed** sur erreur DB). Deux API parallèles : synchrone (`has_permission`, DB-blind, UI seulement) vs asynchrone (`require_permission`, DB-aware, seule voie d'application réelle).
- **DB** : session unique par requête via `get_db()` — commit uniquement dans la dependency (jamais dans une route), `autoflush=False` (flush explicite requis avant lecture de PK auto-générées).
- **CSRF** : double-submit cookie, parsing multipart par regex (délibéré, pour ne pas consommer le flux deux fois), chemins machine (`/api/v1/`, `/webhooks/`, etc.) exemptés. Un bug de production passé (désync du flag `Secure` du cookie via détection de schéma HTTPS) est documenté en commentaire et corrigé.
- **MRV** : module le plus volumineux et le plus abouti (`mrv_router.py`, 1646 lignes). Architecture événementielle : `NavEvent` (brouillon → finalise → valide, capture bord uniquement `captain:M`) puis génération de rapports (`mrv:M`, siège) avec **double validation** : master (capitaine, `captain:M`) puis siège (`mrv:M`, restreint au rôle Carbon). Le legacy (`mrv_events`/`MRVEvent`, CRUD manuel, export CSV DNV, écran `/mrv/params`) a été **décommissionné intentionnellement** (« LOT 14 ») et n'existe plus qu'en lecture seule (`/mrv/archive/events`) — ce n'est pas un TODO oublié, c'est documenté comme tel dans le code.
- **Booking client** : wizard **3 étapes réel** (route → cargaison → récap+création de compte), **guest-first** (panier tenu par cookie signé 2h, pas de compte requis avant l'étape 3), **aucun paiement en ligne** pour le fret (virement bancaire, confirmé par le commentaire du router lui-même). Stripe n'existe que pour la « vente à bord » (module `captain`), sans rapport avec ce wizard.
- **Escale** : verrou `escale_locked_at` asymétrique — déverrouiller (`S`) demande un niveau de permission supérieur à verrouiller (`M`), choix délibéré.
- **Claims** : 6 statuts (`open, in_review, provisioned, settled, rejected, closed`) mais **transition non forcée en code** — l'API accepte n'importe quelle transition malgré l'ordre linéaire documenté dans le docstring du router. Risque mineur (outil interne) mais à garder en tête si le workflow claims est retouché.

### 2.2 Écarts documentation ↔ code (à corriger ou à garder en tête)

Ces documents sont des **artefacts de conception/vision**, pas des specs vivantes — à ne pas prendre au pied de la lettre sans vérifier le code :

| Document | Écart constaté |
|---|---|
| `docs/architecture/01-architecture.md` | Omet `ForceMfaForAdminMiddleware` dans la chaîne ; décrit un event bus (`app/events/`), un pattern Repository (`BookingRepository`) et une couche DTO complète qui **n'existent pas** dans le code ; décrit un worker Celery/dbt-runner non retrouvé dans `app/` ; décrit un flux de réservation **centré sur Stripe** qui ne correspond plus à la réalité (fret = virement, Stripe = vente à bord seulement) ; mentionne nginx en frontal alors que README/CLAUDE.md indiquent Caddy ; « 30+ modèles » alors que ~115 classes sont exportées. **Lecture globale : document figé à une phase de conception antérieure, à rafraîchir.** |
| `docs/personas/01-personas.md` | Explicitement un document de conception (« Étape 9 — /architecture »), pas l'état courant. Propose une arborescence de routers par audience (`public/`, `client/`, `staff/`) **non implémentée** (routers à plat) ; mentionne du offline IndexedDB/service worker non retrouvé ; suppose un layout PWA dédié `/onboard` alors que les écrans bord réutilisent `staff/_layout.html`. |
| `docs/booking/01-cale-booking-platform.md` | Décrit un wizard 4 étapes avec paiement Stripe — la réalité est 3 étapes, guest-first, sans paiement (voir §2.1). Ne mentionne pas du tout le rattachement au portail packing-list par token (`/p/{token}`), pourtant réel et structurant du parcours client. |
| `docs/security/01-security-review.md` | Référence encore Stripe dans la CSP (contradiction avec le retrait de Stripe du circuit fret) ; revendique WebAuthn alors que seul TOTP est implémenté (constat de l'audit du 2026-06-10, jamais corrigé depuis) ; liste des outils CI (`safety`, `trivy`, `npm audit`) qui ne correspondent pas à `ci.yml` actuel. |

**Recommandation** : rafraîchir ces 4 documents n'est pas bloquant pour la production, mais évite de futures décisions prises sur une base erronée (P1, cf. §4).

---

## 3. Workflows métier clés (tracés bout-en-bout dans le code)

- **Booking/devis** : `booking_router.py` (client) + `staff_booking_router.py` (backoffice) + `devis_router.py` (devis) + `cargo_portal_router.py` (portail token post-confirmation) → services `booking.py` → `capacity.py` → `booking_lifecycle.py` (point unique de dispatch des effets de bord : email, notification, création auto du packing-list, émission auto du certificat Anemos) → `packing_list.py` / `anemos.py` / `quoting.py`. États : `draft → submitted → confirmed → loaded → at_sea → discharged → delivered` (+ `cancelled`), transitions avant uniquement, whitelist `_ADVANCE_TARGETS`.
- **MRV** : voir §2.1. Rapports (Noon/Carbon/Stopover) rendus depuis un **snapshot JSON figé**, jamais recalculés à l'affichage.
- **Escale** : `escale_router.py` — opérations import/export, dockers, statut de quai (ATA/ATD idempotent), synchronisation auto vers `SofEvent` (chaîne MRV).
- **Claims** : `claims_router.py` — création auto d'un `SofEvent` + entrée de timeline + notification `manager_maritime` ; sous-flux provision/assureur/documents/stats. Transitions non forcées (§2.1).
- **Trombinoscope** (module `crew`, PR #148, mergée dans `main` le 2026-07-27 lors du pull) : génération PDF mensuelle auto (scheduler interne, dernier jour du mois) + manuelle, archivage (`generated_reports`), notification in-app rôle `armement`.

---

## 4. État CI/tests & dette technique priorisée

### P0 — bloquant

1. **La CI ne fait tourner que `tests/unit` (83 fichiers)** — confirmé directement dans `.github/workflows/ci.yml:64-65` (`pytest tests/unit --cov=app ...`). **`tests/integration/` (107 fichiers — plus nombreux que unit !) et `tests/regression/` (4 fichiers, dont le filet de sécurité de parité V2↔V3) ne sont jamais exécutés en CI.** Les rapports de déploiement citent "710 passed" mais ce chiffre vient d'exécutions locales/manuelles, pas de la CI — le badge CI actuel atteste de moins que ce que l'équipe croit qu'il atteste.
2. **Pas de protection de branche confirmée sur `main`** — un incident déjà survenu (merge direct sans PR sur MRV v2/vente à bord) a cassé `main` (marqueurs de conflit non résolus → `SyntaxError`, 74 erreurs ruff, 1 finding bandit High) en contournant totalement la CI. Réparé depuis (PR #143), mais rien n'empêche aujourd'hui une récidive.

### P1 — à prioriser

3. `pip-audit` et `gitleaks` sont non-bloquants en CI (`|| true` / `continue-on-error`) — une CVE réelle ou un secret fuité passerait au vert.
4. Documents obsolètes à rafraîchir : `docs/security/01-security-review.md`, `docs/architecture/01-architecture.md`, `docs/personas/01-personas.md`, `docs/booking/01-cale-booking-platform.md` (§2.2).
5. Override d'audience du pilote MRV v2 (`mrv_v2_capture.audience.vessels_off`) : pas d'écran admin, réglage en SQL direct, aucune trace dans le journal d'audit — gap de traçabilité pour un réglage réglementaire (déjà documenté comme backlog connu dans `CLAUDE.md`).
6. Baseline mypy à 142 erreurs, non-bloquante (ratchet assumé — à reconfirmer que le chiffre n'a pas grossi).
7. Claims : transitions de statut non forcées en code (§2.1) — risque mineur, à corriger si le module est retouché.
8. Long tail de tickets P1 déjà trackés (non détaillés ici, voir `docs/audit/backlog/`) : `CARGO-12`, `COM-05/06/07/08/11`, `ONB-04/06/07`, `ADM-05/06`, `FIN-06/07`, `STO-06/07/09`, `MRV-08`, `TRK-02/03/04`, `PLN-04`, + reliquats audit-360 cycle 2.

### P2 — reportable sans risque

9. `app/services/invoicing.py` module dormant non supprimé malgré la décision d'arbitrage A5 (déjà tracké `EVO-01`, docstring honnête, aucun risque fonctionnel).
10. 21 seuils MRV provisoires (`provisional=True`) en attente de calibrage post-voyage pilote (résolution naturelle attendue).
11. Migration de layout public en cours (`public/_layout.html` vs `_layout_v2.html` coexistent) — incohérent mais pas cassé.
12. Nettoyage cosmétique (`Versions TOWT/` legacy, `.DS_Store`).

### Déjà résolu — ne pas re-prioriser

Conflit pip CI, gate de couverture réaliste (25% au lieu de 80% fictif), tous les écarts P0 V2→V3 (confirmé par le dict `_PENDING` vide dans `tests/regression/test_v2_parity.py`), la plupart des constats de l'audit 360 (2026-06-12), rate-limiting login/portail, garde-fou injection CSV, garde-fou taille d'upload.

---

## 5. Questions ouvertes / à trancher

- ~~Faut-il ajouter `pytest tests/integration` + `tests/regression` à la CI (P0-1) avant tout autre développement ?~~ **Tranché 2026-07-27 : on garde le constat pour l'instant, pas d'action immédiate.**
- ~~La protection de branche GitHub sur `main` (P0-2) nécessite un accès admin GitHub.~~ **Confirmé 2026-07-27 : Yasmin n'est pas admin du repo — à escalader plus tard (au retour du manager ou via la personne admin).**
- Rafraîchir les 4 documents obsolètes (§2.2) maintenant, ou les laisser en l'état et se fier au code + à ce document ? — en attente.
- ~~Caisse de bord : les règlements CB doivent-ils créditer la caisse espèces ?~~ **Tranché 2026-08-27 — ADR-011, option B.**
- ~~Cloisonnement par navire : jusqu'où, et pour qui ?~~ **Tranché 2026-08-27 — ADR-012, le personnel maritime est borné à son navire d'affectation.**
- ~~Qui peut rembourser une vente à bord ?~~ **Tranché 2026-08-27 — ADR-013, le siège seul.**
- ~~Le registre de vente détaxée doit-il porter les exigences probantes du registre BL ?~~ **Tranché 2026-08-27 — non, ce sont deux objets distincts (ADR-013).**
- ~~Régime de taxe pour le service passagers 2027 ?~~ **Sans objet — activité passagers suspendue (2026-08-27).**
- **Reste ouvert** : qui clôture la caisse (la séparation des tâches voudrait `finance:M`, la décision sur le gel à la relève va dans l'autre sens — cf. ADR-012 §« Point resté ouvert »).
- **Reste ouvert** : extension du cloisonnement par navire aux autres modules du bord (escale, cargo, crew, mrv, qhse, tickets) — principe acté, application à faire module par module.

**Priorité (2026-08-27)** : rendre la vente à bord et la caisse réellement utilisables par le bord. Cf. §16.

---

## 6. Prochaines étapes recommandées

Par ordre de valeur opérationnelle (cf. `CLAUDE.md` — priorité à ce qui aide Operations aujourd'hui) :
1. **Rejouer un test à bord** de la vente/caisse, une fois la checklist de mise en service exécutée (§16). C'est le seul moyen de valider la remédiation du 2026-08-27.
2. Combler le trou de couverture CI (P0-1) — changement mécanique, faible risque, gain de sécurité élevé avant toute autre modification.
3. Vérifier/activer la protection de branche `main` (P0-2).
4. Reprendre le backlog P1 fonctionnel (`docs/audit/backlog/`) en fonction des retours Operations.

## 7. Lancer l'app en local — procédure réelle (le README est incomplet sur ce point)

Testé et validé le 2026-07-28. Le README dit juste `docker compose up -d` puis `http://localhost:8000`, mais c'est insuffisant/inexact tel quel :

1. **`caddy` ne fonctionne pas en local** : son `Caddyfile` demande un vrai certificat Let's Encrypt pour `CADDY_DOMAIN` (`my.newtowt.eu` par défaut) — ça ne marche pas sans nom de domaine public. Et le service `app` n'expose **aucun port** vers l'hôte dans `docker-compose.yml` (accès prévu uniquement via `caddy`). **Contournement local** : un `docker-compose.override.yml` (non versionné, créé pour cette session) ajoute `ports: ["8000:8000"]` sur `app`, et on ne lance que `db` + `app` (`docker compose up -d db app`), sans `caddy`.
2. **`.env` à créer** (absent du repo, seulement `.env.example`) — copier `.env.example` en `.env` et changer `SECRET_KEY` (doit faire ≥32 caractères et ne pas être dans la liste `WEAK_SECRETS`, sinon le démarrage est refusé). Pour tester sans le garde-fou MFA admin (sinon on est bloqué sur l'écran de config TOTP à la première connexion), ajouter `REQUIRE_MFA_FOR_ADMIN=false`.
3. **`alembic upgrade head` échoue sur une base fraîche** en `APP_ENV=development` — car `init_db()` (`app/database.py`) exécute déjà `Base.metadata.create_all()` au démarrage de l'app dans cet environnement, créant toutes les tables avant qu'Alembic ne s'exécute.

   ⚠️ **CORRECTION 2026-07-29 — la recommandation initiale (`alembic stamp head`) était mauvaise.** `stamp` marque l'historique comme appliqué **sans exécuter le DDL** : toute migration ultérieure qui `ALTER` une table ne s'exécute donc jamais, et la base dérive silencieusement du modèle. Constaté en pratique le 2026-07-29 : **7 colonnes manquantes sur 6 tables** (`vessels.deadweight_t`, `crew_members.first_name/last_name/agency`, `env_reports.period_seq`, `nav_event_noon.rob_uree_t/rob_eau_douce_t`, `ports.mrv_scope`, `voyage_emission_summaries.co2eq_t`) + **7 tables obsolètes** jamais supprimées (`create_all` ne supprime rien). Symptôme : `/tracking`, `/planning` et `/dashboard` en **HTTP 500** (`asyncpg.UndefinedColumnError`).

   **Procédure corrigée** : soit laisser `create_all` construire le schéma courant sur une base **réellement vide** puis `alembic stamp head` (valable uniquement à cet instant, et à refaire à chaque évolution de modèle), soit — préférable — diagnostiquer la dérive avant usage en comparant `Base.metadata` à `information_schema.columns`, et rattraper par `ALTER TABLE … ADD COLUMN IF NOT EXISTS` avec un DDL **généré depuis le modèle** (`sqlalchemy.schema.CreateColumn` compilé sur le dialecte PostgreSQL), en retirant les `NOT NULL`.

   ✅ **RÉSOLU 2026-09-01 — la chaîne complète passe sur une base vierge, il n'y a plus lieu de `stamp`.** Vérifié sur la tête `20260828_0135` : les **144 migrations** s'appliquent d'un bout à l'autre sur une base réellement vide, sans erreur. La procédure propre, et **non destructive**, est donc :

   ```bash
   docker compose stop app                      # sinon create_all devance Alembic
   docker compose exec -T db psql -U towt -d postgres -c "create database towt_neuve owner towt;"
   docker compose run --rm --no-deps \
     -e DATABASE_URL='postgresql+asyncpg://towt:<pwd>@db:5432/towt_neuve' \
     app alembic upgrade head                   # conteneur jetable : pas de boot de l'app
   # puis bascule par renommage, l'ancienne base restant disponible en repli :
   #   alter database towt rename to towt_legacy_<date>;
   #   alter database towt_neuve rename to towt;
   docker compose start app && docker compose exec -T app python -m scripts.seed_demo
   ```

   ⚠️ **Reconstruire l'image avant** (`docker compose up -d --build app`) : le service `app` **embarque le code**, il ne le monte pas. Une image ancienne fait échouer `alembic current` sur `Can't locate revision …` — la révision existe dans le dépôt, pas dans l'image. Et `restart: unless-stopped` relance les anciens conteneurs au démarrage du daemon : leur présence ne prouve pas que le build a eu lieu.

   **Pourquoi ne pas se contenter de `create_all`** — mesuré le 2026-09-01 en comparant colonne par colonne une base `create_all` et une base Alembic : **46 colonnes** posées par les migrations `0114`→`0135` manquaient à la base `create_all` (`packing_list_batches.bl_*`, `cashbox_movements.medium`, `onboard_sales.refund_*`, `rate_offers.*`, `commercial_clients.is_prospect`…). `create_all` crée les tables absentes mais **ne modifie jamais** une table existante : après une évolution de modèle sur table existante, la base est silencieusement incomplète et les écrans concernés tombent en 500.

   **Dérive modèles ↔ migrations, état au 2026-09-01** : **aucune** colonne du modèle absente des migrations — la chaîne couvre intégralement les modèles. En revanche **45 colonnes** sont `NOT NULL` côté modèle et **nullables** côté migration (`activity_logs.created_at`, `client_accounts.language`, `docker_shifts.nb_dockers`…), et `ports.mrv_scope` l'inverse. Dette ancienne et de faible sévérité, mais réelle : la production autorise NULL là où le modèle l'interdit. À traiter par une migration dédiée, pas en urgence.

4. 🔴 **`alembic upgrade head` est cassé sur `main` — blocage de déploiement** (constaté 2026-07-29) : `FAILED: Multiple head revisions are present`. Deux chaînes de migration **divergentes** coexistent — `20260716_0112_noon_rob_annexes.py` (chaîne MRV) et `20260720_0107_generated_reports.py` (chaîne rapports générés/trombinoscope) : deux branches de fonctionnalité ont ajouté des migrations sans se rebaser l'une sur l'autre. Or `CLAUDE.md` indique que **la production utilise Alembic exclusivement** ⇒ un déploiement par `alembic upgrade head` échoue en l'état. Correctif : une **migration de fusion** (`alembic merge`), à faire valider (touche l'historique de schéma). **À vérifier avant tout déploiement** : quelle révision la base de production porte réellement, et si l'écart au modèle y est le même qu'en local.

   ✅ **Résolu deux fois — et désormais sous filet.** Fusion `20260807_0113` (chaînes MRV × crewing, panne du 07/08), puis fusion `20260826_0119` (chaînes BL #158 × QHSE #160, panne du 26/08 au déploiement de 96a5c70 — restauration automatique du snapshot). **Le motif est structurel** : deux branches de fonctionnalité chaînent leurs migrations sur le même parent, `main` absorbe la première sans rien dire, la seconde recrée deux têtes. Il ne se voyait qu'en production parce que la CI ne regardait pas le graphe de migrations : c'est corrigé par la sentinelle `tests/regression/test_alembic_single_head.py` (tête unique + chaîne parcourable de la base à la tête, sans connexion à la base). **Règle** : une révision **déjà fusionnée sur `main`** ne se rechaîne pas — réécrire son ascendance ferait considérer l'autre chaîne comme appliquée sur toute base qui porte déjà la révision, et ses tables manqueraient silencieusement. On pose une fusion. Le rechaînage ne vaut que pour une révision non publiée (branche de travail).
5. **Aucun utilisateur n'existe après ces étapes** — le compte admin documenté dans le README (`INITIAL_ADMIN_*` dans `.env`) n'est **pas** créé automatiquement au démarrage. Il faut lancer manuellement le script de seed : `docker compose exec app python -m scripts.seed_demo` (crée admin/commercial/client démo + navires/ports/legs de démonstration).
6. Environnement Windows utilisé pour cette session : Docker Desktop n'était pas installé au départ (installé via `winget install Docker.DockerDesktop`) ; le crédential helper (`docker-credential-desktop.exe`, dans `C:\Program Files\Docker\Docker\resources\bin`) doit être sur le `PATH` pour que `docker compose pull` fonctionne (sinon `error getting credentials`).

**Comptes de démo** (après `seed_demo`) : `admin` / `ChangeMeFirst!2026` (administrateur), `commercial` / `Demo!Commercial2026`, client `demo@example.com` / `Demo!Client2026`.

**Recommandation** : ce contournement est fonctionnel mais artisanal — envisager un `Makefile`/script `scripts/dev_up.sh` officiel qui encapsule ces 5 étapes, pour que le prochain développeur (ou le manager à son retour) n'ait pas à les redécouvrir. Non fait pour l'instant (hors scope de la découverte).

## 8. Visite guidée du 2026-07-28 — ce qui a été vu en direct

Connecté en tant qu'`admin`, données de démo (6 navires, 6 legs planifiés, aucun booking/marin/mouvement financier réel — base vide de données opérationnelles). Confirmations visuelles des constats de découverte :

- **Tableau de bord** : KPIs (bookings à confirmer, legs à venir, tickets P1, flotte, CA prévisionnel, CO2 évité, remplissage), carte de position flotte (vide — alimentée par `POST /api/tracking/upload`), sidebar organisée en 6 sections (Pilotage, Commercial, Cargo, Opérations, + RH/Performance/Admin en scroll).
- **Planning** : Gantt annuel par navire, codes de legs conformes au glossaire (`4APTUS6` = navire 4 Atlas, 1er voyage 2026, PT→US), 100% respect calendrier (données démo).
- **Commercial** : remplissage des legs en commercialisation (0/978 palettes par leg, 5868 total flotte).
- **Cargo** : confirmé vide tant qu'aucun booking n'est confirmé (cohérent avec le constat §3 — pure génération documentaire post-confirmation).
- **Escale** : sélection navire → liste des escales de l'année → détail vide tant qu'aucune escale n'est sélectionnée.
- **MRV** : confirmé visuellement — Voyages MRV, Soutages (BDN), FLGO (Marad, lecture seule), Qualité MRV, Datasets OVDLA/OVDBR, Paramètres MRV, **Archive — événements MRV** (mrv_events gelée) — correspond exactement à l'architecture décrite en §2-3.
- **Crew** : écran vide (aucun marin en base démo) — pas de bouton Trombinoscope visible avec une liste vide, à vérifier avec des données réelles.
- **Finance** : revenu/coûts/marge par leg (vide), gestion OPEX/Ports.
- **Dashboard environnemental** (`/dashboard-env`) : **confirme en direct le design KPI décarbonation déjà documenté en mémoire de session** — 3 méthodes de calcul EF/CO2 explicitement séparées et étiquetées "3 bases légitimement différentes — jamais mélangées dans un même chiffre" (Occupation réelle / Standardisée / Cargo MRV réglementaire), comparateurs porte-conteneurs/avion marqués **PROVISOIRE**, état de complétude par navire (2/6 voyages avec KPI carbone calculé).

## 9. Rôles & processus métier (What/Who/When/Where/Why) — 2026-07-28

### Matrice des permissions (`app/permissions.py`, `_MATRIX`) — niveau max par rôle × module

C = Consulter, M = Modifier, S = Supprimer/déverrouiller (cumulatif : CMS ⊃ CM ⊃ C). `—` = aucun accès.

| Module | administrateur | operation | armement | technique | data_analyst | marins | commercial | manager_maritime | rh |
|---|---|---|---|---|---|---|---|---|---|
| planning | CMS | CM | C | C | C | C | C | CM | C |
| commercial | CMS | CM | — | C | C | — | CMS | CM | — |
| escale | CMS | CMS | C | CMS | C | C | C | CM | — |
| cargo | CMS | CMS | — | C | C | C | CM | CM | — |
| finance | CMS | — | — | — | CMS | — | — | — | C |
| kpi | CMS | C | C | C | C | C | C | C | — |
| captain | CMS | CM | C | CM | C | C | C | CMS | — |
| crew | CMS | CM | CMS | C | C | C | — | CM | C |
| claims | CMS | CMS | — | C | C | — | — | CM | — |
| mrv | CMS | CM | C | CM | CM | C | — | CM | — |
| qhse | CMS | C | C | CM | C | CM | C | CMS | C |
| rh | CMS | C | C | C | C | C | C | C | CMS |
| booking | CMS | CM | — | — | C | — | CMS | CM | — |
| tickets | CMS | CMS | — | CM | C | CM | — | CMS | — |
| analytics | CMS | C | — | — | CMS | — | C | CM | C |
| veille | CMS | CM | C | C | CM | C | CM | CMS | C |
| admin | CMS | — | — | — | — | — | — | C | — |

Overrides possibles en base (`role_permissions`, `/admin/permissions`, cache 60s, **fail-closed** vers la matrice codée en dur si la base est indisponible). Cellule `(administrateur, admin)` verrouillée en dur — l'admin ne peut jamais se couper de l'administration.

**Asymétries notables** : `armement` = lecture seule partout sauf `crew` (CMS) — l'écriture RH passe obligatoirement par le rôle `rh` dédié. `data_analyst` = lecture seule partout sauf `finance`/`analytics` (CMS) — scope BI strict. `commercial` n'a **aucun accès** à claims/crew/mrv/tickets — cloisonné du monde opérationnel/navire. `manager_maritime` est le seul rôle non-admin avec CMS sur qhse/tickets/veille et avec un peu de visibilité sur `admin` (C) — il arbitre les escalades transverses.

### Deux mécanismes transverses (présents dans quasi tous les workflows)
- **Notifications in-app** (`app/services/notifications.py`) : ciblent un utilisateur précis, un rôle entier, ou un client — préchargées sur chaque requête staff (cloche topbar).
- **Piste d'audit** (`app/services/activity.py`) : `ActivityLog` append-only sur toute action de modification, avec anonymisation automatique des emails (RGPD).

### Workflows clés (résumé — détail complet dans le rapport agent du 2026-07-28)

- **Devis → Booking → Confirmation → Livraison** : devis public (10 req/30min anti-spam) → wizard client 3 étapes (invité) → `on_status_change()` = point unique qui déclenche email + notif + (à confirmation) création auto du portail packing-list, et (à discharged/delivered) émission auto du certificat Anemos. Facturation **hors plateforme** (virement, équipe compta). Frais d'annulation calculés automatiquement par palier (0%/25%/50%/100% selon délai avant ETD).
- **Planning + cascade ETD/ETA** (`app/services/date_cascade.py`) : déplacer un leg décale rigidement tous les legs suivants du même navire (non partis), résout les chevauchements, re-date escales/dockers **jamais réalisés** (règle d'or : "on ne touche jamais un fait réalisé"), renumérote les leg_codes si changement d'année, notifie tous les clients des legs impactés. Verrou optimiste sur `updated_at` (409 si édition concurrente).
- **Escale** : ATA/ATD sont des **faits**, jamais recalculés par la cascade planning ; l'ATD posé côté escale **n'écrase jamais** un ATD déjà saisi par le bord (le bord fait autorité). Verrou/déverrouillage : verrouiller = `M`, déverrouiller = `S` — asymétrie volontaire (rouvrir un dossier clos doit être plus dur que le fermer).
- **MRV** : brouillon (auteur seul) → finalisé (moteur de règles bloquant, ex. R05 = position GPS manuelle sans justification = refus) → validé. Double validation **au niveau rapport** : master (bord, `captain:M`) puis siège (`mrv:M`, **restreint aux rapports Carbon uniquement** — `SiegeValidationNotAllowedError` sinon). Relance auto des brouillons dormants (R19 : 24h capitaine, 48h siège).
- **Claims** : 6 statuts, création = notif auto `manager_maritime` + entrée SOF auto sur le leg concerné (traçabilité dans le journal de bord officiel). Notifications re-déclenchées aux étapes clés (provisionné/réglé/rejeté) seulement.
- **RH/Crew** : pas de self-service marin pour poser un congé — c'est le rôle `rh` qui saisit **et** décide (RH = autorité centrale, peut auto-approuver à la saisie). Sync Marad strictement **lecture seule** (jamais d'écriture retour), idempotente, additive (jamais d'écrasement par une valeur Marad vide), champs sensibles (RIB, adresse) volontairement jamais importés.
- **Automatique vs humain** : quasi tout le reste est une action humaine derrière `require_permission()`. Seules les crons (`/api/...`, token dédié, 503 si non configuré) sont automatiques — cf. §11.

## 10. Portail client ↔ ERP — flux de données

### Trois identités distinctes, jamais mélangées
- **Invité** (wizard non connecté) : cookie signé `towt_booking_draft` (2h) — ne voit que son propre brouillon.
- **Client connecté** (`/me`) : session cookie — chaque route re-vérifie individuellement `booking.client_account_id == client.id` (pas de middleware central — dépend de la rigueur de chaque route).
- **Expéditeur par token** (`/p/{token}`) : aucune connexion, scope strict à une seule packing list, token haché SHA-256 (jamais en clair), rate-limité, expire à 90 jours.

### Le cycle complet
1. Devis public (sans compte) → cookie de rappel → wizard invité → écriture `draft` (`channel="client"`).
2. Un booking peut aussi être créé **côté staff** pour un client identifié (`channel="operator"`) — mêmes emails/notifications déclenchées côté client, aucune différence visible pour lui.
3. `on_status_change()` est **l'unique point de diffusion** ERP → client à chaque changement de statut (email best-effort + notif in-app).
4. À **confirmed** : création auto du portail packing-list (`/p/{token}`), lien poussé au client.
5. Sur le portail token : l'expéditeur édite la packing list, importe un Excel, uploade des documents, échange des messages — **tout est audité champ par champ** (acteur = "client").
6. À **discharged/delivered** : certificat CO2 Anemos émis automatiquement, distance/CO2 tracés avec leur méthode de calcul (jamais une estimation silencieuse).
7. Devis → booking : cookie de rappel + pré-remplissage automatique du panier, le devis passe en statut "accepted" à la soumission (désamorce la relance J+1 automatique).

### Points de vigilance identifiés (sécurité/qualité, non bloquants)
- **`/devis/{reference}` n'a aucun contrôle de propriété** — quiconque obtient le lien voit nom/email/société/prix du prospect. Volontaire pour le partage de lien, mais sans expiration ni journalisation comparable au portail token.
- **Aucun verrou optimiste sur `Booking`** (hors le verrou de capacité pris à la confirmation) — deux actions concurrentes (staff + client) sur la même réservation : la dernière écriture gagne silencieusement.
- **Les messages du portail expéditeur (`PortalMessage`) ne déclenchent pas de notification côté staff**, contrairement au fil de messages de la page réservation (`BookingMessage`) qui, lui, alerte le rôle `operation`.
- Les deux fils de messages (booking vs portail) sont **deux tables séparées, jamais fusionnées** — un client connecté et son contact expéditeur (personnes potentiellement différentes) peuvent avoir deux conversations non liées sur la même expédition.
- Les corrections de jalons logistiques par le staff (`/staff/bookings/{ref}/milestones`) **ne déclenchent aucune notification** — contrairement à une progression via `advance()`.

## 11. APIs & intégrations externes

### Exposées PAR mynewtowt (entrant)

**API publique B2B** (`/api/v1/*`, lecture seule) : santé, recherche de ports, legs réservables/capacité — auth `X-API-Key`, **503 si `PUBLIC_API_KEY` non configurée** (secure-by-default), 401 si clé invalide (comparaison à temps constant).

**10 endpoints cron** (tous protégés par un token `X-API-Token` dédié, 503 si non configuré, appelés par Power Automate) :

| Variable d'env | Route | Fréquence |
|---|---|---|
| `TRACKING_API_TOKEN` | `/api/tracking/upload` | Quotidien (rapport satcom) |
| `WEATHER_API_TOKEN` | `/api/weather/refresh` | Toutes les 30 min |
| `VEILLE_API_TOKEN` | `/api/veille/refresh` | Périodique |
| `TICKETS_SLA_API_TOKEN` | `/api/tickets/escalate-sla` | Filet de secours (le déclenchement principal est automatique à l'ouverture du kanban) |
| `QUOTE_FOLLOWUP_API_TOKEN` | `/api/quotes/followup` | J+1 |
| `MRV_DRAFTS_API_TOKEN` | `/api/mrv/draft-reminders` | Règle R19 (24h/48h) |
| `MRV_QUALITY_API_TOKEN` | `/api/mrv/quality-run` | Nocturne |
| `MARAD_SYNC_TOKEN` | `/api/marad/refresh` | ≥30-60 min (limite Marad) |
| `MARAD_FLGO_TOKEN` | `/api/marad/flgo-refresh` | Cron dédié |

**Webhook Stripe** (`/webhooks/stripe`) : uniquement pour la vente à bord (jamais le fret), signature vérifiée, idempotent nativement (pas de table dédiée — `settle_sale()` est un no-op si déjà réglé).

### Consommées PAR mynewtowt (sortant)

- **Marad/MaraSoft** : équipage + relevés carburant (FLGO), **strictement lecture seule** (jamais d'écriture retour, whitelist d'endpoints), champs sensibles jamais importés, limite 1 req/min sur les endpoints équipage (d'où les tokens de sync séparés). Piège tenant documenté : chaque client Marasoft a son propre serveur numéroté, une clé valide sur le mauvais serveur renvoie une flotte vide.
- **Pipedrive** (CRM) : sync manuelle (bouton), pas de cron dédié.
- **NewsData.io** : polling sortant uniquement (jamais de push entrant), scoring heuristique déterministe toujours actif + couche IA Claude optionnelle en repli gracieux.
- **Windy → Open-Meteo** : bascule plus fine qu'un simple "si Windy échoue" — Open-Meteo est **toujours** interrogé en parallèle (seule source de courants marins), fusionné avec Windy qui a priorité sur les champs qu'il fournit.
- **Anthropic Claude** ("Newtowt Agent") : 4 outils lecture seule réellement câblés (la docstring en annonce 5 — dérive doc mineure), permission re-vérifiée à *chaque* appel d'outil (jamais de confiance aveugle au LLM), prompt caching actif, détection d'injection de prompt avant tout envoi.
- **SMTP** : sortant uniquement, best-effort (pas de file d'attente durable), bascule en clair si STARTTLS indisponible (acceptable en dev, à vérifier en prod).
- **MapTiler/Mapbox** : jeton visible côté client (normal pour ce type de jeton, généralement restreint par domaine chez le fournisseur).

## 12. Journal de développement & ADR

- Journal : `docs/DEVELOPMENT_JOURNAL_2026-07-27_2026-08-17.md`.
- ADR : `docs/architecture/` — **ADR-010** (refonte commerciale, 2026-08-26),
  **ADR-011** (caisse : espèces ≠ encaissements CB), **ADR-012** (cloisonnement
  par navire), **ADR-013** (remboursement, valeur du registre de vente, gel à la
  relève). Les trois derniers sont datés du 2026-08-27 et **acceptés**.
  **ADR-014** (reprise d'historique TOWT, 2026-09-02) est **accepté** — sept
  décisions, la 6ᵉ (table d'archive des noon reports) ouvre le lot 2.
- **PLN-SEQ (2026-09-01)** : refonte de la séquence de planification —
  déclarations « départ du POL » / « arrivée au POD » (escale + SOF bord →
  `services.voyage_transitions`, chemin unique du réel), re-ancrage d'ETA sur
  l'ATD + cascade des legs suivants, historisation du réel dans
  `schedule_revisions` (migration 0136), phase dérivée `Leg.phase`
  (en mer / à quai), planification saisie à la journée, dates effectives
  (`effective_etd/eta`) dans dérive/Gantt/transit. Doc :
  `docs/design/05-sequence-planification.md`.
- **PLN-BUGS (2026-09-02)** : quatre retours d'usage sur la planification.
  (1) Le **leg de référence** de la création est choisi (« Chaîner après »,
  `chain_options`) — le dernier leg par ETD reste le défaut mais devient faux
  dès qu'un voyage lointain est saisi à l'avance, avec l'ETD **et** le POL qui
  en découlent. (2) L'**audit de séquence** parle chiffré, sur les dates
  effectives, et n'instruit plus un leg appareillé (son ATD est un fait).
  (3) La **suppression d'un leg** ne sort plus en 500 : quatre FK vers
  `legs.id` n'étaient ni déliées ni couvertes par un `ondelete`
  (`packing_lists` en tête, ajouté par COM-11 après l'écriture de
  `delete_leg`) ; inventaires nommés + SAVEPOINT + sentinelle de FK, et les
  registres d'argent **bloquent** au lieu d'être déliés. (4) La **distance
  théorique** absente (port sans coordonnées) est repliée au rendu, corrigeable
  dans Admin → Ports (recalcul immédiat des legs du port) et reprise par
  `scripts/backfill_leg_distances.py` — l'écart et l'allongement réels s'en
  dérivant, les trois colonnes tombaient ensemble.
- **TOWT-HIST (2026-09-02)** : reprise d'historique de l'ancienne compagnie —
  `legs.origin` (`newtowt` | `towt_archive`, migration 0138), garde unique
  `assert_leg_mutable` (lecture seule : édition, déplacement, suppression,
  déclarations, escale), exclusion de la renumérotation (code TOWT d'origine
  conservé), filtre `origin` + badges « TOWT » dans `/planning`, protection
  anti-purge des positions `source='towt_archive'`, décimation d'affichage de
  `/tracking`. Scripts : `import_towt_legs` (CSV versionné des 36 voyages),
  `towt_gps_consolidate` (local) → `import_towt_positions` (serveur),
  `towt_noon_extract` (prototype local). Doc :
  `docs/audit/2026-09-02-reprise-historique-towt.md`, **ADR-014** (accepté le 2026-09-02).

## 13. Audit de cohérence métier (2026-07-28) — feedback logiciel vs compagnie maritime réelle

Analyse ciblée du code (pas une relecture de doc) pour repérer des incohérences entre le modèle de données/permissions et la logique d'exploitation d'une compagnie maritime. Confirmé/recalibré avec Yasmin le même jour.

### Confirmé non-problématique après clarification

- **Certificats statutaires navire (ISM, classification, sécurité)** : `app/models/vessel.py` n'a aucun champ pour ça — **volontaire**, la donnée de référence vit dans **Marad (FMS)**, pas mynewtowt. Point de vigilance résiduel (non tranché) : aucune alerte croisée planning × expiration de certificat n'existe côté mynewtowt ; à évaluer si utile un jour.
- **Coût carburant absent du P&L par voyage** (`finance_rollup.py` : OPEX mer = forfait `opex_daily_sea`, repli 12 000 €/jour ; `bunker.py` n'a aucun champ prix) : **volontaire**, le rapprochement facture réelle se fait dans **Pennylane** (comptabilité), pas dans le module "Finance" de mynewtowt qui reste un outil de pilotage opérationnel (prévisionnel/réel), pas la source de vérité comptable. Voir mémoire `reference_external_systems_marad_pennylane`.
- **Armement en lecture seule (`C`) sur le module Escale**, alors que `escale.py` définit une catégorie d'opération "armement" (embarquement/débarquement/EOSP-SOSP/passage PAF) qui semblait taillée pour ce rôle : **volontaire**, Armement décide et saisit ces événements dans **Marad**, pas dans mynewtowt. Point de vigilance résiduel (non tranché) : pas de garde-fou vérifié contre une dérive entre la décision Marad et le module Crew de mynewtowt (sync lecture seule 30-60 min) ou le dossier d'escale saisi par Opérations.
- **DPA (Designated Person Ashore, rôle statutaire Code ISM) = Manager Maritime** — confirme et justifie ses droits pleins (CMS) sur QHSE/Tickets/Captain dans la matrice de permissions ; ce n'est pas qu'un choix d'organigramme interne, c'est une exigence réglementaire.

### Points restant ouverts (pas de trou de sécurité, mais à garder en tête)

- **Vente à bord (`onboard_sales`/`cashbox`) non consolidée dans `LegFinance`** — revenu réel mais invisible dans la marge par leg ; montant faible, rapprochement manuel en compta probablement suffisant, à confirmer.
- **`port_fees_eur` "réel" est une estimation tarifaire recalculée à chaque rollup** (`PortConfig` × opérations d'escale), pas la facture reçue de l'agent portuaire — et le rollup **écrase toute saisie manuelle** (la doc du code le dit explicitement, `other_costs_eur` est le seul champ vraiment manuel). Risque de divergence silencieuse entre "réel affiché" et "réel facturé".
- **Transitions de statut des claims non verrouillées en code** (`claims_router.py`) — confirmé sans impact actuel : le process claims avec le P&I club/assureur **n'est pas encore formalisé côté métier** ("pas encore", réponse Yasmin 2026-07-28). À revisiter si ce process se formalise (le code devra alors appliquer les étapes obligatoires).

### Complément d'audit (2026-07-28, 2e passe) — couverture des 4 familles métier

> ⚠️ Cette 2e passe est **superseded par l'audit approfondi §14** (6 domaines, 2026-07-28 après-midi). Conservée pour l'historique ; en cas de divergence, §14 fait foi.

La première passe était surtout Technique/Armement/Finance ; complément ciblé Commercial et Crewing, plus un point Technique↔Planning qui manquait :

- **Commercial — anti-surbooking solide** : `services/booking.py::confirm()` refait un contrôle de capacité avec verrou de ligne (`check_and_lock`) au moment de la confirmation, pas qu'un affichage — protégé contre une double confirmation concurrente.
- **Commercial — aucun contrôle de crédit client** avant confirmation (pas de suivi de statut d'impayé). Cohérent avec la facturation fret hors plateforme (décision A5) — pas un bug, mais ce risque est entièrement porté par le jugement humain du commercial ; le logiciel ne peut pas aider puisqu'il ne suit même pas le statut de paiement des factures.
- **Crewing — aucune vérification d'effectif minimum de sécurité (manning scale) par navire/leg** : `models/crew.py` a un rôle en texte libre (captain, chief_mate, ab, cook...) sans contrainte qui bloquerait un départ sans capitaine/chef mécanicien affecté.
- **Crewing — aucun suivi des heures de repos réglementaires (MLC/STCW)** : le seul comptage existant est "embarqué vs au repos" (statistique RH), pas une vérification légale d'heures de repos minimales. Seuls Schengen et l'expiration de documents sont réellement couverts (cf. §"confirmé non-problématique" ci-dessus).
- **Technique ↔ Planning — aucune notion d'indisponibilité navire (dry-dock/maintenance)** dans `models/vessel.py` ni `services/planning.py`. Rien n'empêche Commercial de confirmer une réservation sur un leg pendant une période où Technique (via Marad) sait le navire indisponible — angle mort inter-systèmes, la donnée de disponibilité vit dans Marad sans être consultée par Planning/Commercial dans mynewtowt.

### Points positifs notés (l'audit n'a pas cherché que des trous)

- Claims ↔ Assurance bien lié en base (`insurance_contract_id` sur `Claim`, franchise/plafond/prime portés par `InsuranceContract`).
- Conformité équipage (passeport, visas US/BR, livret marin, STCW/médical/GMDSS) modélisée avec dates d'expiration et statut "warning" calculé (`crew_compliance.py`), pas un champ texte oublié.
- Escale/Technique cohérent : le rôle Technique a bien les droits d'écriture (`CMS`) correspondant à sa catégorie d'opération escale (soutage/avitaillement/inspection) — contrairement à Armement.

---

## 14. Audit approfondi 6 domaines (2026-07-28) — regard expert maritime

Audit conduit par 6 explorations parallèles du code, chacune avec une grille de lecture métier spécialisée (chartering/liner, opérations portuaires, crewing/MLC, superintendent technique/MRV, documentation & sinistres, organisation/SI). **Périmètre : cohérence métier, pas revue de style ni audit de sécurité applicative.**

**Convention de fiabilité** : les constats marqués ✅ ont été **re-vérifiés directement dans le code** (lecture du fichier, pas confiance au rapport). Les autres sont rapportés avec leur preuve `file:line` mais non recontrôlés — à vérifier avant d'agir.

### 14.0 Correction d'une hypothèse erronée (importante)

Une hypothèse posée le matin — « ces voiliers sont probablement sous 5 000 GT, donc hors MRV UE obligatoire, le module MRV pourrait être de la sur-ingénierie » — **est fausse** :

- Le **règlement (UE) 2023/957** a étendu le MRV aux navires de charge général de **400 à 5 000 GT depuis le 01/01/2025**. La flotte est dans le périmètre.
- Ce n'est pas de l'anticipation : `docs/strategy/CDC_VERIFICATION_TIERCE_ANEMOS.md:18-21` indique que les émissions sont **déjà surveillées, déclarées et vérifiées par un organisme accrédité**, avec références **THETIS-MRV** par navire ; les datasets OVDLA/OVDBR sont **déposés chez DNV** (`app/models/mrv_dataset.py:1-11`). Destinataire externe accrédité réel.
- Le CDC pose honnêtement la distinction : **émissions** vérifiées par un tiers vs **méthode d'évitement Anemos** = auto-déclaration documentée.

⇒ **La priorité du module MRV ne doit pas être revue à la baisse.** Dérive documentaire à corriger au passage : `docs/strategy/RAPPORT_ARCHITECTURE_UNIQUE.md:79-84` présente le MRV comme produisant « une preuve officielle d'intensité carbone » — le MRV vérifie des émissions, pas la méthode d'évitement. C'est le type de formulation que l'ECGT sanctionne (échéance 27/09/2026).

### 14.1 Motif transverse dominant : « le vert par défaut »

L'absence de donnée s'affiche comme conformité, de façon persistée et horodatée. C'est le motif le plus dangereux identifié, présent à plusieurs endroits :

- ✅ **Schengen** : `crew_compliance.py:229-233` lit **uniquement** `CrewAssignment` (alimentée par la seule saisie d'escale), saute les affectations sans leg (`if leg is None: continue` — pourtant chemin normal, décision A4 `leg_id` nullable), et `crew_compliance.py:251-257` conclut `else: status = "compliant"` quand `days == 0`. Le même fichier admet 200 lignes plus bas que les marins viennent exclusivement de Marad et qu'un autre calcul a dû être rebasé sur `MaradCrewSchedule` « sans eux, l'indicateur restait à 0 » — correction non appliquée au calcul Schengen.
- ✅ **Sur-comptage symétrique si l'ETA manque** : `crew_compliance.py:248` `arr_start = max(d for d in (arrival, embark) if d is not None)` — si `arrival` est `None`, retombe sur `embark` ⇒ **toute la traversée comptée comme présence Schengen**. Sur un FR→BR de 30+ jours = faux dépassement de 30 jours d'un coup, sur la route principale.
- **Garde-fou d'embarquement disparu** : `passport_blocking_reason` (`crew_compliance.py:277`) existe et n'a **aucun appelant** dans tout le dépôt. `docs/audit/specs/SPEC-CREW-reprise-P0.md:226` affirme que ce garde-fou et son override audité (`action="crew_assignment_override"`) sont « PRÉSERVÉS — un gain V3 à ne pas casser » : `grep` ne trouve plus l'override. On peut embarquer un marin au passeport périmé, médical expiré et en dépassement Schengen sans un avertissement. L'embarquement réel se crée via `escale_crew.couple_crew_assignment` sous permission `escale:M`, pas `crew:M`.
- **Part voile** : voir §14.5 — l'absence de relevé de charge moteur classe en « vélique pur ».

L'arithmétique Schengen elle-même est **correcte** (fenêtre glissante `[today-179, today]`, bornes inclusives, comptage en jours distincts via `set[date]`, liste des 29 États à jour 2026). Le problème est la **source**, pas l'algorithme. Aucun test ne couvre `refresh_schengen_for_members` (contraste avec la sentinelle `test_factor_whitelist` posée pour les facteurs d'émission).

### 14.2 Il n'existe pas de registre de connaissements (BL)

Cluster d'exposition juridique. Quatre faits qui, ensemble, rendent aucun BL défendable devant un P&I club — non parce qu'il serait faux, mais parce que la compagnie ne peut prouver ce qu'elle a émis, quand, par qui, en combien d'exemplaires :

- ✅ **Émettre un BL est un `GET` en permission « Consultation », et cette lecture écrit en base** : `cargo_packing_router.py:361-374` — `require_permission("cargo","C")` puis `assign_bl_number()` qui persiste `bl_number` + `bl_issued_at`. Aucun `activity_record` (contraste : `lock_pl` est journalisé). Titulaires de `cargo:C` donc habilités à émettre : `technique`, `data_analyst`, **`marins`**. Déclencheur = un simple lien `<a href target="_blank">` « Générer BL ». Un préchargement de lien ou un scan de sécurité émet des BL en série.
- ✅ **L'import Excel détruit les BL émis et recycle leurs numéros** : `cargo_packing_router.py:535-540` fait `for b in list(pl.batches): await db.delete(b)` puis recrée. Seule garde = `can_modify(pl)`. La séquence étant calculée par **comptage** des BL existants, après suppression le compteur redescend et `TUAW_{leg}_001` est **réattribué à une autre marchandise**. La contrainte UNIQUE ne protège plus rien, la ligne d'origine n'existant plus.
- **Contenu mutable après émission** : `can_modify()` (`services/packing_list.py:230-231`) ne teste que `pl.status != "locked"`, ignore `batch.bl_number`. Shipper/consignee/poids/description éditables (par le staff **et** par l'expéditeur via le portail), batch supprimable. `assign_bl_number` étant idempotent et le rendu réutilisant `bl_issued_at`, **le second téléchargement produit un PDF au contenu différent, même numéro, même date d'émission**. `unlock` (`cargo:S`) rouvre sans garde sur les BL émis.
- **Ni original/copie, ni registre de remise** : `pdf_generator.py:99` `"number_of_obl": 3` en littéral, rendu « 3 OBL signés » ; aucun filigrane ORIGINAL/COPY, aucun n° d'exemplaire, réimpressions illimitées via `GET`. `grep surrender` → 0 résultat. La livraison sans présentation d'original (misdelivery) est **exclue de la couverture P&I**.
- **Le template du rail booking n'a ni consignataire ni notify party** (`templates/pdf/bill_of_lading.html:7-27`), alors qu'il est servi au staff **et au client** sous le titre « Bill of Lading · Connaissement ». Le template du rail packing list, lui, est correct (consignee, notify, marks & numbers, HS code). Deux qualités de « BL » coexistent selon la porte d'entrée, sans indication de laquelle fait foi.
- **Absents** : négociabilité (« to order »/Seaway Bill), `said to contain`, clausing/réserves (le Mate's Receipt existe mais **aucun chemin de données ne le relie au BL**), `shipped on board date` (la date d'émission = l'instant du clic ⇒ **BL antidaté structurel**, exclusion de garantie P&I ; un BL est délivrable dès `submitted`, avant chargement).
- **Aucun tally** (rapprochement déclaré / réellement chargé) : `grep tally|loaded_weight|VGM|discrepan` → 0 résultat. Le BL imprime le **déclaré** par l'expéditeur. C'est la source n°1 des litiges cargo, et sans tally la compagnie n'a que le chiffre du réclamant.
- **Le portail expéditeur écrit cette donnée sans aucune trace d'audit** : 8 routes mutantes dans `cargo_portal_router.py` (dont **suppression** de batch et de document), **zéro appel à `activity.record()`**. `portal_access_logs` trace les accès, pas les mutations.

### 14.3 Deux portes sur la même cale, une seule est gardée

- ✅ **`order_confirm` ne contrôle aucune capacité** : `commercial_router.py:2399-2437` pose `status="confirmed"` + flush, sans appel de capacité. Or les commandes confirmées **comptent bien** comme cale réservée (`capacity.py:60` `_ORDER_RESERVED_STATUSES = ("confirmed","loaded")`, comptage `:63-96`). Idem `order_assign_submit` et `order_split_submit`. Conséquence chaînée : confirmer 1 500 palettes sur 978 ⇒ capacité négative clampée à 0 ⇒ **le rail booking client se ferme silencieusement** (traversées masquées, `booking_router.py:128`) sans alerte.
- **`submit()` ne verrouille pas** alors que `submitted` **réserve déjà** la cale (`capacity.py:50-56`) : trois brouillons de 40 palettes sur 40 disponibles passent tous, puis les trois soumissions ⇒ 120 réservées sur 40. Le seul verrou (`check_and_lock`, avec `with_for_update`) est dans `confirm()`.
- ✅ **Anti-surbooking rail booking : solide** (verrou de ligne réel). Le problème n'est pas le verrou, ce sont les portes latérales qui ne l'empruntent pas.
- **Aucune contrainte de poids ni de volume** : la capacité est exclusivement un compte de palettes (`capacity.py:127`). `Vessel.deadweight_t` existe et n'est jamais lu par la chaîne commerciale. `Booking.total_cubage_m3` et `oversize` ne sont **écrits nulle part**. `unit_weight_kg` est nullable et un poids absent compte pour 0. 30 palettes d'IBC à 1 800 kg (54 t) passent comme 30 palettes de café (9 t). Le seul contrôle de masse est au stowage, en **warnings d'affichage** ; `used_t_total` n'est jamais comparé à un port en lourd navire.
  ⇒ Sur un voilier de charge à faible déplacement, stabilité et port en lourd contraignent **avant** la surface. Ne pas tarifer au poids est un choix commercial ; ne pas le **contraindre** est un risque nautique.

### 14.4 SOF, horodatage et alerting opérationnel

Fil conducteur : **un excellent système d'enregistrement, pas encore un outil d'exploitation.** La capture est au-dessus de la moyenne du secteur (26 types SOF, signature commandant, hash SHA-256 anti-altération, verrouillage, documents guidés NOR/LOP/Mate's Receipt en anglais). Le déficit est dans l'exploitation.

- ✅ **L'ATD/ATA posé par le bord est l'heure de saisie, pas l'heure de l'événement** : `voyage_events.py:93-99` et `:120-126` écrivent `datetime.now(UTC)` ; la signature `on_vessel_departed(db, leg)` n'accepte même pas de timestamp, donc l'`occurred_at` du SOF est ignoré. Le flux **escale** fait ça correctement (parse `status_time`) — c'est le flux **bord**, celui qui fait autorité, qui corrompt. Sur une traversée 30+ jours, la saisie après coup est la norme (satcom coupé, arrivée de nuit). Contamine : **taux de service publié sur la vitrine** (|ATA − ETA| < 24 h), fenêtre d'association GPS, `LegKPI.duration_hours`/`avg_speed_kn`, rollup finance.
- ✅ **Un navire en mer dont l'ETA est dépassée ne déclenche aucune alerte** : `dashboard_alerts.py:100` `if eta and not ata and not atd:` — la condition `not atd` élimine le cas opérationnel réel (parti, pas arrivé, ETA dépassée). L'alerte ne couvre que le leg jamais parti. C'est la question quotidienne des Opérations. Correctif : retirer `not atd`.
- **Inversion ATD > ATA possible** : le statut « pilote départ » du port d'**arrivée** écrit dans l'`atd` du leg quand celui-ci est NULL (`escale_router.py:757-777`) — le commentaire du code **documente le piège puis y tombe** (la protection ne couvre que l'ATD déjà saisi par le bord). Durée de traversée négative ⇒ KPI faux, fenêtre GPS inversée (0 position rattachée, distance réelle = 0). Aucune contrainte `atd < ata` nulle part. Se déclenche quand le bord n'a pas encore le réflexe SOF ⇒ **premiers voyages**.
- **Le SOF n'est pas exploitable pour un décompte de staries** : le NOR capture `notice_date`/`notice_time`/`position` + `agent_stamp` en **texte libre**, mais **pas d'heure d'acceptation** (celle qui déclenche le décompte), pas de turn-time. `grep laytime|SofStoppage` → 0 occurrence. Ni périodes d'exclusion (pluie, attente de poste, panne grue), ni termes de charte-partie (SHINC/SHEX/WWD), ni cumul. `add_sof_event` ne valide aucune chronologie. **`docs/strategy/SOF_UPGRADE_PLAN.md:52-61` documente exactement cet écart (S1→S8, daté 2026-06-22) — plan jamais exécuté.** `LegFinance` n'a aucun poste laytime : l'impact est invisible même a posteriori.
- ✅ **La cascade fabrique une anomalie que l'outil qualifie ensuite de « critique »** : deux règles divergentes — résolveur `planning.py:636` `prev_eta = peta` (0 h entre deux legs aval) vs auditeur `planning.py:1195-1209` `ready_at = prev.eta + port_stay` ⇒ `PlanningIssue("critical","port_stay_overlap")`. Décaler un leg peut produire une chaîne où le navire arrive et repart au même instant. Passe les validations dures (`validate_leg_schedule` utilise des inégalités strictes). Extraire un helper `ready_after(leg)` partagé.
- **La cascade ignore le verrou d'escale** : `date_cascade.py:198-266` ne filtre que `actual_start/end IS NULL`, aucune jointure sur `Leg.escale_locked_at` — alors que les 10 endpoints d'écriture escale respectent ce verrou. La clôture administrative devient une illusion.
- ✅ **Bon comportement confirmé** : la cascade ne touche jamais un fait réalisé (vérifié ligne à ligne) ; un leg déjà appareillé sur son chemin lève `LegOverlap` plutôt que d'être déplacé (`planning.py:626-631`), avec dégradation propre.
- **Escalade SLA : différée, jamais perdue** (balayage de rattrapage sans borne inférieure) — mais elle produit une ligne en base, **sans email ni SMS**, vers un **rôle unique** (`manager_maritime`). Un P1 « urgence médicale » à SLA 2 h n'est donc pas un dispositif d'astreinte, et si le DPA est en congé personne ne la voit.
- **Aucune alerte de silence satcom** : la dernière position est *affichée*, jamais *surveillée*. Et `vessel_position.get_latest_position(max_age_hours=6)` avec une cadence **quotidienne** (Thalos) renvoie `None` la plupart du temps ⇒ préremplissage des écrans commandant/client silencieusement inopérant.
- **Distance réelle structurellement sous-estimée** : haversine entre points à 1/jour (~120 NM) **ignore bords et empannages** d'un voilier ⇒ `real_elongation` (performance de route) faux **à la baisse** par construction. Filtre anti-saut 30 nœuds sur 24 h ⇒ accepte tout point à 720 NM. Aucun contrôle de bornes lat/lon à l'ingestion, ni détection d'inversion lat/lon, ni rejet de (0,0).

### 14.5 Arrimage, IMDG et propulsion voile

- **Overstowage structurellement indétectable** : `StowageItem` n'a **aucun champ de port de déchargement** ; l'ordre de remplissage est purement géométrique. Le plan est **unique par leg** et ne collecte que les commandes de ce leg ⇒ une cargaison encore à bord héritée du leg précédent est **invisible**. `suggest_plan` supprime tous les items et régénère (aucune continuité inter-legs). Mettre la cargaison du 1er port de déchargement sous celle du 2e = re-handling à quai + faute imputable à l'armateur.
- **Aucune ségrégation IMDG classe × classe** : `stowage.py:107-124` ne lit qu'un booléen `is_dangerous` ; la valeur d'`imdg_class` est **recopiée mais jamais lue** par l'algorithme. Les 3 zones dangereuses mélangent IMO et hors-gabarit. Pas de matrice « away from / separated from / separated by a complete compartment ». Le placement hors zone dédiée ne produit qu'un warning de niveau **`info`**. ⇒ Classe 3 + 5.1 dans la même zone = non-conformité IMDG directe. Et **la cargaison de référence est du café vert et du cacao = denrées alimentaires**, pour lesquelles l'IMDG impose une séparation des classes 6.1/8. `StowageZoneSpec.segregated` existe mais désigne la ségrégation **climatique**, pas IMDG.
- **Déclaration IMDG non exploitable** : manquent `packing_group`, `flash_point`, `proper_shipping_name`, `marine_pollutant` (0 occurrence dans `app/`). N° ONU **optionnel**. Aucun contrôle avant chargement (`advance(→loaded)` ne vérifie ni FDS, ni classe, ni PL verrouillée). Seul type de doc DG au portail = `"msds"`, pas de Dangerous Goods Declaration (IMO DGD).
- **Le rail opérateur perd la classification IMDG** : `staff_booking_router.py:227-235` construit les items avec `hazardous=hazardous` **sans** `imdg_class`, `un_number`, `hs_code` (comparer `booking_router.py:243-245` qui les renseigne) et sans exigence de FDS. **Le rail interne — celui des gros comptes — est moins contrôlé que le formulaire public.**
- **Un plan d'arrimage approuvé est écrasable en un clic** : `StowagePlan.status` (`draft`/`approved`/`loaded`/`locked`) est **écrit mais jamais lu comme garde** ; `suggest_plan` supprime tous les items sans regarder le statut, aucun snapshot de l'état écrasé. En cas d'avarie, le plan tel qu'approuvé n'est pas reconstituable — alors que `claims` référence les zones d'arrimage.
- ✅ **Heure « sous voile pure » surévaluée d'un facteur 6** : `carnet_bord.py:533-537` fait `+= 24` par ligne de voilure, avec le commentaire `# Approximation`. Or `NoonReportSail` est documenté « Relevé voilure **horaire (4 h)** » avec un champ `slot_time`, et `NAV_TIME_SLOTS` définit **6 créneaux/jour**. Un voyage de 10 j tout sous voile imprime **1 440 h au lieu de 240**, sous le libellé « Heures sous voile pure » (`chapitre_6_performance_navigation.html:22`). Les **pourcentages restent justes** (le +24 se compense) — c'est l'**absolu** qui ment, et c'est lui qui est imprimé. Second volet : ce calcul lit `NoonReportSail`, **gelée en écriture depuis le lot 14** ⇒ pour un voyage capturé en MRV v2, `sailing_hours = 0` et `sail_pct = 0 %`. Correctif : `+= 4`, ou mieux rebrancher sur `kpi_env.propulsion_profile` (déjà correct, déjà testé) au lieu de maintenir un second calcul divergent sur une table gelée. **P0 : document client, claim central, échéance ECGT 27/09/2026.**
- ✅ **La part voile est 100 % déclarative et le doute profite à la voile** : `kpi_env.py:685-695` — `voile_on` = OU de 5 cases cochées par le bord ; `moteur_on = _positive(me_ps_load_pct) or _positive(me_sb_load_pct)` avec `_positive` (`:669-670`) qui renvoie **False pour `None`** ⇒ **voiles cochées + charge moteur non renseignée = « velique_pur »**. Le mode de défaillance le plus probable biaise systématiquement vers la voile. Aucune règle du catalogue ne confronte la catégorie d'un créneau au **delta du compteur carburant** de l'intervalle — donnée capturée juste à côté. Recommandation : rendre `me_*_load_pct` obligatoire (bloquant) ou reclasser en `indetermine`, jamais en `velique_pur` ; + une règle croisant créneau voile et ΔL.

### 14.6 Chaîne de mesure carburant et BDN

- **Les données mesurées du BDN sont saisies puis jetées** : `inter_event_compute.py:260` calcule `conso_t = ΔL × 0,001 × densité` où `densité` vient du seuil R16 (**défaut 0,845**), alors que le BDN capture la **densité réelle à 15 °C** (`bunker.py:82`, NOT NULL) — utilisée seulement pour un contrôle de plage. Idem `lower_heating_value` ignoré au profit de la constante `MDO_LHV_MJ_PER_T = 42700` (`emission_ledger.py:66`), et `ef_ttw_co2` (`bunker.py:87-89`, saisi et affiché) **jamais lu** par le grand livre. Le facteur est résolu depuis `vessel.default_fuel_type` (`emission_ledger.py:487`), pas depuis le carburant réellement livré. **Aucune correction de température/VCF** (ASTM D1250/ISO 91) : les compteurs mesurent à 30-50 °C, la densité appliquée est celle à 15 °C. ⇒ Biais systématique **dans le même sens** (±1 % densité, 1-2 % température) sur chaque tonne déclarée au vérificateur et vendue en certificat. Capturer une donnée réglementaire et l'ignorer est pire que ne pas la demander.
- **Le BDN n'est pas un BDN au sens MARPOL Annexe VI** : `sulfur_content_pct` est **nullable** et n'apparaît que dans le modèle, le parsing de formulaire, la whitelist et 3 templates — **aucune règle ne le lit**. Absents : n° d'échantillon scellé, lieu et échéance de rétention (12 mois), signature du représentant fournisseur, déclaration Annexe VI Reg. 18.3, statut off-spec, FONAR. `supplier_name` nullable. ⇒ La ligne Fécamp ↔ São Sebastião traverse la **SECA Manche/mer du Nord (0,10 % S)** puis la zone globale (0,50 %) : un BDN à 0,45 % pour un transit SECA est une non-conformité détectable en PSC (détention, amende, sanction pénale côté français), et le système a la donnée sans moyen de le signaler. Sans traçabilité de l'échantillon, aucun recours fournisseur en cas d'avarie machine sur carburant off-spec.
- **Réconciliation ROB : bien conçue, calibrage inutilisable.** Elle existe (sondage `rob_declared_t` vs compteurs `rob_calculated_t`, R14 à 3 paliers + contre-vérification indépendante FLGO/Marad R17 + IR02) — c'était une bonne surprise. Mais les seuils seedés sont mineur **0,5 t** / majeur **2 t** / **bloquant 5 t**, tous `provisional=True`, face à une conso de référence de **750 L/j ≈ 0,63 t/j** : le seuil bloquant vaut **~8 jours de consommation**. 5 t de gazole disparues avant qu'un blocage survienne. À recalibrer en **% du ROB ou en jours de burn** (pratique fuel-reconciliation), en tête des 21 seuils provisoires du voyage pilote. Et **un `rob_calculated_t` négatif n'est jamais détecté** (R06 ne teste que le ROB *déclaré*, et seulement sur Departure/Arrival).
- **Contrôles manquants au catalogue** (le catalogue est par ailleurs sérieux — voir §14.9) : vitesse implicite hors bornes / saut de position impossible (**R09 est décrit comme tel dans le seed mais ne contrôle que distance déclarée vs calculée** — `speed_kn` est calculé et jamais validé) ; conso d'un intervalle > capacité des cuves ; ROB calculé négatif ; conso à quai vs en mer **non discriminées** (même `seuil_conso_ref_l_j` de 750 L/j des deux côtés) ; cohérence ΔL vs Δheures moteur (`running_hours_h` calculé, jamais confronté) ; créneau voile vs carburant consommé.
- **Un BDN oublié en brouillon casse la chaîne ROB sans rappel** : seuls les soutages `valide_master` entrent dans la chaîne (`emission_ledger.py:230-234`), mais R19 (rappel brouillons dormants) ne requête que `NavEvent.status == "brouillon"` (`draft_reminders.py:39,88-94`) — jamais `BunkerOperation`. Un BDN non validé produit un écart ROB **exactement égal à la masse soutée** ⇒ alerte R14 « bloquant » garantie, faux positif récurrent qui noie le vrai signal.

### 14.7 QHSE Phase 0 (code sur `feature/qhse-foundation`)

- 🔴 **Un filtre par mot-clé détruit des non-conformités ISM légitimes** : `qhse_ingestion.py:78` `_TEST_PATTERN_RE = re.compile(r"\b(test|essai|demo)\b", re.I)` puis `:301-311` — toute ligne dont `Subject` **ou** `Description` contient ce mot est quarantainée, **jamais importée**, et l'anomalie n'existe que dans `QhseImportReport.errors` (liste en mémoire, affichée une fois, **jamais persistée**). Or « test » est un mot du métier QHSE : *« Fire pump test not carried out before departure »*, *« essai de l'appareil à gouverner non effectué »*, *« emergency generator load test failed »*. Un registre ISM incomplet est une non-conformité majeure en audit, et la perte est **invisible après fermeture de l'écran**.
- 🔴 **Un `rollback()` dans la boucle d'import détruit les lignes déjà importées tout en les comptant** : `qhse_ingestion.py:267-270` `except Exception: await db.rollback()` **à l'intérieur** de la boucle. Comme `get_db` (`app/database.py:47-57`) commite une seule fois en fin de requête, un rollback à la ligne N annule les lignes 2..N-1 — alors que `report.imported` les a déjà comptées (`:404`). Écran « importés = 120 » quand 40 subsistent. Le patron cité en référence (`flgo_sync`) ne fait pas ça. Correctif : savepoint par ligne (`async with db.begin_nested():`), et ne compter qu'après succès.
- **Socle non réconciliable (trou de schéma, pas d'UI)** : la colonne `Code` de l'export FMS est déclarée dans les alias (`qhse_ingestion.py:169`) mais **jamais lue**, et `models/qhse.py:63-132` n'a **aucune** colonne de référence source ni de lot d'import (`import_batch_id`). La déduplication « prévue en Phase 1 » exigera une **migration**. Réconciliation FMS ↔ mynewtowt impossible ; écrasement vs doublon indécidable.
- **Règles de qualité mortes** : RQ01-RQ03 sont enregistrées (`qhse_validation_rules.py`) mais `run_rules(db, "qhse", …)` n'est **jamais appelé**. La seule application réelle est le rejet en dur de l'ingestion.
- **Champs ISM structurants jamais alimentés** : `report_source` forcé à `"operational"` à l'import (`:349`) ⇒ audits internes ISM et inspections externes **PSC/Class/Flag indiscernables** alors que le modèle prévoit les trois. Le référentiel `DeficiencyCode` (Paris MoU/USCG, `qhse.py:135-162`) n'a **aucun alias d'en-tête** ⇒ codes de déficience PSC jamais importés, suivi de détention non opérationnel.
- **Le DPA n'a aucun outil de son rôle statutaire** : il a les droits (`qhse:CMS`, `captain:CMS`) mais `/qhse` affiche **un compteur** (`qhse_router.py:45`). Absents : programme d'audits internes, **revue de direction**, rapport d'incident structuré, détection de CAPA en retard (`limit_date` dépassée jamais testée), périodicité des exercices SOLAS (`OnboardChecklist` a `fire_drill`/`abandon_drill`/`isps_audit` **sans périodicité ni détection de retard** — SOLAS exige mensuel, et sous 24 h si >25 % d'équipage renouvelé). Présents et bien faits : `near_miss` comme grade, registre ISPS visiteurs (`watch_log.py:60-77`).
  ⇒ **mynewtowt est aujourd'hui une copie non réconciliable du FMS : le coût de la double saisie sans le bénéfice de la source unique.** Question de fond à trancher avant tout dashboard Phase 1 : **le FMS reste-t-il la source de vérité QHSE ?** Si oui, mynewtowt doit être un miroir strictement idempotent en lecture, jamais une seconde source d'écriture.

### 14.8 Séparation des tâches, alerting calendaire, divergence Marad

**Séparation des tâches inexistante sur les trois circuits d'argent** :

| Circuit | Constat |
|---|---|
| Sinistres | Déclarer + provisionner + solder tous en `claims:M`. `settled_eur` alimente directement `claims_cost_eur` du résultat par leg. Un seul détenteur fabrique une charge sans second regard. |
| Paie | Saisir EVP + verrouiller période + exporter Silae tous en `rh:M`. Une personne seule crée, fige et transmet un virement de salaires. `PayrollVariable.created_by_id` existe déjà — il suffirait de le contrôler. |
| Booking | Créer et confirmer partagent `booking:M`, et la confirmation crée du CA consolidé. Commercial et Opérations n'ayant pas de chef commun, personne au-dessus pour rattraper. |

- **Hiérarchie inversée sur les sinistres** : le code notifie `manager_maritime` comme gestionnaire, mais `operation` a `claims:CMS` et `manager_maritime` seulement `CM` (`permissions.py:76` vs `:152`). L'autorité alertée — le DPA — a **moins** de droits que le déclarant.
- **Même motif sur l'escale** : `technique` (subordonné du DPA) peut **déverrouiller** une escale clôturée et supprimer des shifts dockers alimentant `docker_costs_eur` ; `manager_maritime` ne peut ni le faire ni le défaire, et **n'est pas prévenu** (le type `leg_locked` est déclaré dans `NOTIFICATION_TYPES` mais jamais branché). Recommandation : `("manager_maritime","escale") = "CMS"` + notification + motif obligatoire au déverrouillage.
- **`data_analyst` a `finance:CMS`** (seul rôle non-admin avec `S` sur finance) : peut réécrire `revenue_eur`/`margin_eur` à la main et **supprimer** le paramètre OPEX ⇒ `finance_rollup.py:266-271` retombe **sans alerte** sur `FALLBACK_OPEX_DAILY_EUR = 12000`, modifiant rétroactivement la quote-part de tous les legs recalculés ensuite. Un repli silencieux sur une constante est le pire comportement : le chiffre reste plausible, donc personne ne le questionne.
- ✅ **Erreur de calcul confirmée — deux formules de marge divergentes** : `finance_router.py:220` `margin = rev - port - docker - opex_s - other` (**`claims_cost` absent**) vs `finance_rollup.py:298-300` qui le déduit. `finance_leg_upsert` (`:237-249`) écrit `margin_eur` **sans toucher `claims_cost_eur`**, qui reste à sa valeur consolidée ⇒ après une édition manuelle, la ligne affiche un poste sinistres à 50 k€ **et** une marge qui ne le déduit pas. **Marge surévaluée du montant exact du sinistre, silencieusement** ⇒ l'arbitrage des rotations s'oriente vers les routes les plus sinistrées. Correctif : extraire **une** fonction de marge appelée par les deux chemins, et rendre `claims_cost_eur` non-éditable (il est dérivé).
- **RBAC contourné sur `/rh/moi`** : ces routes n'ont **aucun** `require_permission`, seulement `get_current_staff` (`rh_router.py:996,1026,1055,1094,1649,1677`). Deux conséquences : tout compte staff (rôle `marins` inclus) peut déposer une demande de congé — ce qui **contredit la règle d'organisation confirmée** ; et ces routes sont **immunes aux overrides de permissions**, donc `/admin/permissions` **mentira** sur l'état réel des droits.

**Alerting calendaire absent là où le métier vit de délais** :

| Échéance | État |
|---|---|
| **Prescription sinistre (1 an, La Haye-Visby)** — que le BL invoque lui-même | Aucun champ, aucune alerte. `dashboard_alerts.py` ne contient **aucune** occurrence de `claim`. Passé le délai sans assignation ni extension, le recours est éteint. **Meilleur rapport valeur/effort du périmètre sinistres.** |
| **Expiration de police d'assurance** | `_active_contracts` (`claims_router.py:148-160`) ne filtre que `is_active` (booléen manuel), **pas `valid_to`** ⇒ police expirée sélectionnable. Aucun rapprochement `claim.occurred_at` ↔ `[valid_from, valid_to]`. `InsuranceContract` n'a **pas de `vessel_id`**. Aucune alerte d'expiration ⇒ un navire sans P&I valide ne doit pas naviguer (refus d'escale, résiliation d'affrètement, responsabilité des dirigeants). |
| **Expirations documents équipage** | Strictement passives (visibles si on ouvre l'écran), **un seul palier à 30 jours** codé en dur à deux endroits non factorisés, aucun cron, aucun email. Or STCW/visite d'aptitude/visa demandent 6-10 semaines ⇒ à J-30 il est déjà trop tard. Bug annexe : `days <= 30` est vrai pour les négatifs ⇒ un document expiré depuis 45 j s'affiche « expire dans -45 j », noyé parmi les « bientôt ». |
| **Anomalies qualité MRV** | `_alert_roles` (`validation_rules_catalog.py:1646-1663`) ne retourne un destinataire que pour **R10, R24, R14 bloquant, R27** — tout le reste `return ()` (« journal seul »). Un `fail` **bloquant** qui refuse la finalisation au bord **n'alerte personne à terre**. Le bord est en satcom intermittent ; si le commandant abandonne, la donnée MRV du voyage est trouée. La mécanique de dédup 24 h est bien faite — c'est le **périmètre** qui manque. Recommandation : inverser le défaut (tout `fail` bloquant notifie `manager_maritime` + `administrateur`). |

**Divergence Marad ↔ mynewtowt non bornée, et elle contamine un document client** :

- **Clé synthétique incluant la date de début** (`marad_sync.py:371-386`, les serveurs Marad ne renvoient pas d'id de planning) : or **décaler une date d'embarquement est l'opération la plus courante du crewing** ⇒ nouvelle clé ⇒ nouvelle ligne, **l'ancienne reste en base pour toujours**.
- **Aucune réconciliation négative** : pas de `last_seen_at`, pas de soft-delete, pas de comparaison de l'ensemble récupéré. Si Armement débarque quelqu'un dans Marad, mynewtowt ne le saura **jamais** — pas « en retard », jamais. La cadence 30-60 min n'est pas le problème : les ajouts/modifs remontent, les **annulations et débarquements anticipés** non.
- **Double comptage** : `embarked_days_by_member` (`crew_compliance.py:430-451`) additionne `CrewAssignment` **et** `MaradCrewSchedule` sans détection de recouvrement. `CrewAssignment` n'a **ni `marad_id` ni champ `source`**. Le même fichier déduplique correctement pour l'affichage de la bordée — la précaution existe pour l'écran, pas pour le compteur. En droit français/maritime ce compteur commande le seuil des 183 jours (régime social/fiscal), les congés acquis à la mer et l'assiette des primes de mer.
- **Contamination d'un document client** : `crew_for_leg` alimente les noms d'équipage imprimés sur le **certificat Anemos** (`cargo_router.py:382-387`), remis au client et revendiqué comme preuve opposable sur `/preuves`. Y imprimer un marin qui n'était pas à bord attaque le seul argument différenciant.

### 14.9 Certificat Anemos et exposition ECGT

- **Émis à `discharged` (ou `delivered`)**, donc **avant** la validation siège des données MRV, en `try/except` avalé (`booking_lifecycle.py:137-142`). **Idempotent par booking** : un second appel retourne l'existant **sans jamais recalculer** (`anemos.py:139-152`).
- Si `leg_co2_t` est nul ou partiel, bascule sur `method = "theoretical"` = forfait 1,5 g/t·km — **la valeur la plus flatteuse** — et la **fige** (`anemos.py:191-193`).
- `AnemosCertificate` n'a **ni `revoked_at`, ni `superseded_by`, ni version**. Le seul mécanisme de révocation du dépôt concerne les tokens de partage planning. Et ces chiffres sont servis publiquement à vie sur `/verify` et `/verify/{ref}` (`vitrine_router.py:403-490`).
- ⇒ Un chiffre d'émissions publié, opposable, non révisable et systématiquement biaisé du bon côté, alors que le système possède **plus tard** la vraie donnée sans jamais corriger : ce n'est pas une incertitude de mesure, c'est un refus structurel de rectifier. **C'est précisément l'aggravant que vise la directive (UE) 2024/825 (ECGT), applicable au 27/09/2026.** Expose `/preuves` et le positionnement B2B2C café/cacao.
- **Deux éléments l'atténuent** : le grand livre est immuable (pas de mutation cachée) et R20 (Cargo MRV vs B/L) existe en `info`. **Un élément l'aggrave** : le biais densité/facteur par défaut (§14.6) dégrade la valeur mesurée figée dans les certificats émis en branche « déclaré ».
- Recommandation : certificat **provisoire** explicitement marqué à `discharged`, version définitive à la validation siège ; `version`/`revoked_at`/`superseded_by_id` ; recalcul déclenché par toute correction MRV du leg produisant une **version successeur** (jamais une mutation en place) ; `method` et `distance_source` affichés en clair sur le PDF et `/verify` (les champs existent déjà — il ne manque que l'affichage et la révocabilité).

### 14.10 Dettes de référentiel (faible coût, forte valeur d'audit)

- **Le critère d'applicabilité réglementaire n'est pas une donnée du système** : `models/vessel.py` porte `dwt`, `lightweight_t`, `deadweight_t`, `imo_number`, `flag` mais **aucun champ de jauge brute (GT)** — alors que le seuil du règlement est exprimé en GT. Ni référence THETIS-MRV, ni identifiant/version de plan de surveillance, ni drapeau « in scope MRV/ETS/FuelEU/IMO DCS », ni date d'entrée en périmètre. Impossible de répondre dans l'outil à « ce navire est-il en périmètre ? » ; à la livraison des 4 navires en construction, personne ne saura lesquels y entrent. Un vérificateur accrédité commence par le plan de surveillance et le GT.
- **Aucune maintenance planifiée, alors que la donnée est collectée** : `grep maintenance|PMS|overhaul|survey|drydock|class_society` sur `app/models/` et `app/services/` → aucune entité. `running_hours_counter_h` est capturé par moteur à chaque événement mais agrégé **seulement par intervalle MRV**, jamais en compteur cumulé ni rattaché à un échéancier. `vessel_env.py` décrit 6 moteurs par navire sans une seule échéance d'entretien. ⇒ Rien ne relie une échéance technique au planning : **l'armement peut vendre une transat qui chevauche un passage en cale sèche, et le système ne dira rien.** Code ISM ch. 10 est un chapitre d'audit à part entière. Recommandation : ne pas construire un PMS (Marad peut le porter) mais la **jonction** — vue des heures cumulées par moteur (quasi gratuite) + table d'échéances avec alerte J-60/J-30 superposée au Gantt.
- **`technique` est un rôle mais pas un module de permission** (`app/permissions.py`) : le service technique n'a aucun périmètre applicatif propre.
- **Tirant d'eau non contrôlé contre le port** : `Vessel.draft_max_m` existe et est affiché en vitrine ; `models/port.py:15-57` n'a **aucune** profondeur ni tirant admissible. Or « 5,5 m ouvre des escales inaccessibles aux porte-conteneurs » est un argument différenciant — la contrainte structurante du modèle n'est pas modélisée, et une escale impossible n'est découverte qu'à l'arrivée. Ajout minuscule (`Port.max_draft_m` + avertissement à la création de leg) pour un cas d'échec sévère.
- **Réalité physique de l'escale non modélisée** : aucun champ `berth` ni action `shifting` (BERTHED/UNBERTHED existent en SOF mais non appariés ⇒ un shifting est indiscernable d'un appareillage ; impact laytime direct) ; `PortConfig.closed_saturday/sunday` existe mais n'est pas relié au NOR ni au décompte (pas d'heures d'ouverture, pas de « NOR hors bureau réputé remis à l'ouverture ») ; PILOT_ON/OFF jamais appariés en durée ; **MARPOL Annexe V (registre des ordures) : 0 occurrence dans `app/`** — obligation par escale, exigible en PSC ; niveau MARSEC seulement en ligne de checklist texte, pas de Declaration of Security ; `avitaillement` sans quantité ni nature. Bon point : `passage_paf` auto-créé.
- **Frais de quai calculés sur le planifié, pas le réel** : `finance_rollup.py:110-116` utilise `leg.port_stay_planned_hours` alors que `ata`/`atd` existent ⇒ écart planifié/réel non capté sur un poste facturé au jour.

### 14.11 Autres constats commerciaux

- **États métier manquants : ni roll-over, ni shut-out, ni part-shipment, ni waitlist.** La machine à états est strictement linéaire (`booking.py:192-201`). **Aucune route ne modifie `booking.leg_id`** (grep exhaustif) ⇒ un booking est **soudé à son leg à vie**. `leg.status = "cancelled"` est sticky et **n'a aucun effet sur les bookings** du leg ; `capacity.get_available_capacity` ne filtre pas les legs annulés. `loaded` est tout-ou-rien (pas de `loaded_palettes`). ⇒ Un navire annulé ou décalé de 3 semaines laisse 20 bookings confirmés sur un voyage fantôme, comptant toujours dans sa capacité, et la seule sortie est l'annulation sèche (qui envoie l'email « Votre réservation a été annulée » alors que la marchandise part simplement plus tard). Ce sont les trois événements les plus fréquents de l'exploitation liner. **Chantier structurant : introduire `rolled`/`waitlisted`/`part_shipped` modifie le contrat de tous les consommateurs de `Booking.status`** (`capacity._RESERVED_STATUSES`, `emission_ledger._ACTIVE_BOOKING:364`, `kpi.py:75`, `anemos.py:121`, `notifications.py:226`, `carnet_bord.py:260`) — donc le reporting MRV et les certificats. À réserver à une validation explicite du manager.
- **Prix ni traçable ni reproductible, et devis non honoré à la conversion** : `Booking` ne stocke que deux scalaires (`estimated_price_eur`, `confirmed_price_eur`), aucun `grid_id`, aucune table de lignes — contrairement au devis qui persiste correctement ses `QuoteLine` et sa grille. `_quote_prefill` (`booking_router.py:188-210`) ne reprend du devis que **format + quantité** ; le prix est **intégralement recalculé** à la date du booking, `quote.total_eur` n'est jamais comparé ni recopié, et `valid_until` n'est pas vérifié à la conversion. ⇒ Un devis à 12 400 € peut se convertir à 13 100 € ; le client a deux documents à deux prix, aucun décomposé. Or **la facturation partant chez Pennylane hors plateforme, la booking note est la seule pièce contractuelle de prix** : sans détail ligne à ligne, la compta facture à l'aveugle et un litige tarifaire est indéfendable.
- **Grille d'annulation calculée, stockée… jamais utilisée** : `booking.py:233-290` implémente correctement les paliers 0/25/50/100 % et écrit `cancellation_fee_eur`, mais `grep` ne trouve **aucune** occurrence dans un template, un export ou une notification. Les frais sont facturables en droit (CGV signées, `signed_terms_version`) et personne dans l'entreprise ne les voit ⇒ ils ne remonteront jamais à Pennylane. Par ailleurs un **refus NEWTOWT** et une **annulation client** passent par la même route `reject` avec le même calcul ⇒ un refus commercial à J-3 facture 50 % au client. Aucune route d'annulation côté client.
- **Ni no-show (dead freight), ni état « en attente de documents »** : `goods_arrived_pol_at` existe mais est purement déclaratif (lu seulement par la timeline d'affichage). Aucune alerte « J-2 avant cut-off, marchandise non arrivée / PL non verrouillée / FDS manquante ». Un booking `confirmed` sans FDS, sans PL remplie et sans consignee est indistinguable d'un booking prêt à charger. Le no-show est juridiquement distinct de l'annulation (fret dû en totalité, cale perdue sans revente possible).
- **Rail commande sans grille d'annulation et cycle post-livraison incomplet** : `order_cancel` ne calcule aucun frais ; `_ORDER_FORWARD = {"confirmed":"loaded","loaded":"delivered"}` — pas d'`at_sea`, pas de `discharged`, donc le rail A ne peut représenter la cargaison en transit. Or il sert justement les gros comptes/transitaires, ceux dont les annulations coûtent le plus cher. `Order.booking_id` existe déjà (convergence rail A → rail B amorçable).
- **Instruments juridiques du dossier sinistre absents** : `CLAIM_DOC_TYPES` traite l'« expertise » comme une simple catégorie de pièce jointe (sans date, sans expert, sans caractère contradictoire, sans convocation de l'autre partie). Absents de `app/` : lettre de garantie (LOI), subrogation/recours contre tiers, réserve à la livraison. Le type `third_party` existe mais aucun champ n'identifie le tiers responsable ni le montant recherché/recouvré ⇒ `insurance_kpi.claims_exposure` calcule `net_company_total = settled + franchise` **sans les recours encaissés**, donc surévalue structurellement l'exposition nette. ⇒ Un dossier cargo se gagne sur trois pièces : réserve écrite du réceptionnaire, expertise contradictoire, recours dans les délais. **Le process P&I n'étant pas formalisé côté métier, cadrer les champs avec le correspondant P&I avant de coder.**
- **Token du portail expéditeur ni révocable ni renouvelable** : `token_expires_at` fixé une seule fois à la création, aucune route de révocation/rotation/prolongation (contraster avec `POST /planning/shares/{id}/revoke`). Passé 90 j, 410 définitif. Deux scénarios : (a) **mauvais destinataire** — le tiers garde un accès lecture **et écriture** 90 jours sans qu'on puisse le couper (pour un accès par token porteur, la révocabilité **est** le contrôle d'accès) ; (b) **litige tardif** — les sinistres se révèlent à la livraison et se prescrivent à 1 an, mais à 90 j l'expéditeur perd l'accès à sa propre déclaration, à la messagerie et à ses pièces, précisément quand le contentieux commence.
- **Multi-devise déclarée mais non transportée** : `RateGrid.currency` / `GridQuote.currency` / `Quote.currency` existent, mais `Booking` n'a que des colonnes `_eur` et `create_draft` y écrit `quote.total_eur` sans regarder la devise ; la booking note imprime « EUR » en dur. Pas de mécanisme CAF. Faible impact aujourd'hui (routes facturées en EUR), bloquant dès une grille négociée en USD — et **le risque est le silence** : pas d'erreur, juste un montant faux.
- **`services/pricing.py` est du code mort porteur d'une logique contradictoire** : `compute_quote` n'est appelé que par son test. Il implémente un yield management (early-bird −10 %, late-seat +30 %) et des constantes en dur (`DEFAULT_BASE_PRICE_EUR = 38`, `DOCS_FEE_EUR = 50`) **divergentes** de la logique en vigueur dans `quoting.py`, et duplique `PALLET_COEFS` face à `PALETTE_COEFFICIENTS`. Un contributeur qui le lit (docstring affirmative, tests verts) croira la tarification dynamique en production. Nettoyage de 10 minutes.
- **Trois notifications écrites et jamais branchées** : `notify_new_order` (`notifications.py:114`), `notify_new_cargo_message` (`:124` — **c'est la cause du silence PortalMessage déjà connu : le helper existe, `cargo_portal_router.py:434` ne l'appelle pas**), `notify_new_claim` (`:169`, doublon mort — `claims_router.py:301` refait un `create` inline). Types déclarés sans producteur : `packing_to_review`, `leg_locked`. ⇒ Un helper écrit et non branché est plus dangereux qu'une absence : la revue conclut que le cas est couvert. Ces trois-là couvrent précisément les points de contact client (commande, message, avarie).
- **Angle mort commercial** : `commercial` n'a aucune cellule `claims`, `mrv`, `crew`, `tickets`. Croisé avec le message portail qui ne notifie personne, un commercial dont le client appelle pour une cargaison avariée ne peut ni voir le sinistre, ni le ticket d'escale, ni avoir été alerté — et doit passer par une filière avec laquelle il n'a pas de chef commun. Recommandation : `("commercial","claims") = "C"` et `("commercial","tickets") = "C"` en lecture seule.

### 14.12 MLC 2006 : le socle contractuel maritime est absent

`grep MLC|engagement maritime|rapatriement|repatriation|seafarer employment|abandon` sur `app/` → une seule correspondance sans rapport (`watch_log.py:49`, `'abandon_drill'`). `EmploymentContract` est explicitement **terrestre** (docstring « collaborateurs **sédentaires** », FK `employee_id → employees`, `CONTRACT_TYPES = cdi/cdd/apprentissage/professionnalisation/stage`). Un marin n'a de contrat que s'il est **aussi** saisi comme sédentaire (`Employee.crew_member_id`).

| Exigence MLC 2006 | État |
|---|---|
| **SEA** (contrat d'engagement maritime, Règle 2.1) | Absent. Contenu obligatoire normé (art. A2.1 §4), exemplaire signé **obligatoirement à bord**, opposable en PSC. |
| **Rapatriement** (Règle 2.5) | Absent. `CrewTicket` modélise un billet sans rattachement au droit ni au motif. |
| **Durée max d'embarquement continu (11 mois)** | Absent. `_embarkation_timeline` *dessine* les périodes sans calculer de durée continue ; le seul compteur est **annuel par année civile** ⇒ structurellement incapable de détecter un embarquement à cheval sur deux années. |
| **Garantie financière d'abandon** (Règle 2.5.2, amendements 2014) | Absent (ni certificat, ni assureur, ni validité). |
| **Heures de repos** (Règle 2.3) | Absent (seul comptage : embarqué vs au repos). |

⇒ Garantie financière d'abandon et certificat de rapatriement sont **exigibles en Port State Control** (Annexe A5-III) : leur absence est un motif de déficience directe, et pour un armateur pionnier sous attention médiatique, un risque réputationnel disproportionné au coût de la modélisation.
**Question factuelle à trancher AVANT de coder** : Marad porte-t-il déjà les SEA, certificats de rapatriement et garantie financière ? Si oui c'est un import lecture seule ; si non c'est un module. Répondre d'abord évite de construire un doublon du système de référence.

### 14.13 Effectif minimum, brevets, paie marin, congés

- **Brevets/certificats STCW : la table existe, rien ne peut la remplir, rien ne la lit.** `CrewCertification` (`models/crew.py:99-113`) est complet (`kind`, `issued_at`, `expires_at`, `marad_document_id`) mais : **aucune route POST/PUT/DELETE** (grep `certification` dans `app/routers/` → 1 seule ligne, celle du contexte d'affichage) ; Marad n'en importe aucun (`_sync_passports` n'écrit que passeport) ⇒ `marad_document_id` est une **colonne morte** ; `/crew/compliance` ne vérifie **jamais** `CrewCertification`. **Aucun lien `rank` → certificats requis**, aucune notion de jauge/zone/puissance ⇒ impossible de valider qu'un capitaine a un brevet adapté au navire. `CrewMember.role` est un `String(60)` libre alimenté par le 1er élément de `ranks` Marad. ⇒ L'inspecteur PSC demande la matrice poste × brevet, pas une liste de noms. Et l'écran affiche « Aucune certification renseignée » sans qu'on sache si c'est un trou de saisie ou une réalité.
- **Effectif minimum : informatif seulement, et l'indicateur lui-même est faux.** `vessel_readiness` est explicitement non bloquant (« V1 : informatif »). Deux défauts le rendent inexploitable : (a) **normalisation de rang cassée sur la donnée Marad** — `ROLE_SYNONYMS` a ses clés en `snake_case` (`"chief_mate"`, `"chief_officer"`) mais `normalize_role` ne fait que `strip().lower()` sans remplacer les espaces, or Marad renvoie « Chief Officer » ⇒ `"chief officer"` absent du dictionnaire ⇒ le poste reste dans `missing` : **navire déclaré non armé alors que le second est à bord** (seuls `captain`/`master`, mots simples, survivent) ; (b) **le mauvais champ est lu** — `crew_router.py:167` utilise le rôle générique `c["m"].role` alors que le rang réellement tenu à bord est résolu juste au-dessus dans `c["role"]` ; `vessel_readiness` fait correctement l'inverse. Deux écrans, deux logiques, deux résultats. ⇒ Un indicateur qui crie au loup en permanence sera ignoré en trois semaines, et ne servira plus le jour où le poste manque vraiment.
- **Paie marin : pas de ressaisie, pas de lien du tout.** `PayrollVariable.employee_id → employees`, **aucune** colonne `crew_member_id`. `EVP_TYPES` n'a **aucune rubrique maritime** (ni jours embarqués, ni prime de mer, ni indemnité de nourriture, ni majoration de nuit/dimanche à la mer) — mais un « télétravail ». La seule automatisation part d'une absence sédentaire. Les jours embarqués sont calculés, affichés, et s'arrêtent là. ⇒ **Ne pas construire le pont maintenant** : il s'alimenterait d'un compteur faux (double comptage §14.8) issu d'une source incomplète. Séquence : fiabiliser la source, puis obtenir les rubriques Silae réelles (question ouverte tracée dans `silae_export.py:7-11` — **dépendance externe à long délai, à débloquer par un mail dès maintenant**), puis générer des lignes EVP `source="embarkation"` avec clé de déduplication.
- **Congés : aucun contrôle de cohérence.** `rh_router.py:161-206` — la validation totale est « champs obligatoires présents », et la décision pose `leave.status = decision` sans un seul contrôle. **`end_date >= start_date` n'est même pas vérifié.** Absents : contrôle congé ↔ affectation (congé accordé à un marin embarqué sur un leg en cours), congé ↔ congé (superposition), congé ↔ effectif (`vessel_readiness` jamais appelé depuis `rh_router`), notification à Armement à l'approbation. Le service unifié `leaves.py` est explicitement **lecture seule** et ne croise ni `CrewAssignment` ni `MaradCrewSchedule`. ⇒ Conséquence directe du choix d'autorité centralisée : centraliser chez RH est défendable, mais **RH ne dispose alors d'aucun signal d'exploitation** — le contrôle que le self-service aurait rendu visible doit être réinjecté côté RH, sinon la centralisation transfère le risque au lieu de le réduire. Recommandation : **avertir, pas bloquer** (RH est l'autorité, on l'informe) + `end_date >= start_date` tout de suite.

### 14.14 Forces confirmées (à ne pas retoucher)

- ✅ **Anti-surbooking rail booking** : verrou de ligne réel (`with_for_update`), pas un affichage.
- ✅ **Cascade de dates** : ne touche jamais un fait réalisé (vérifié ligne à ligne) ; un leg déjà appareillé lève `LegOverlap` plutôt que d'être déplacé, avec dégradation propre et motif remonté.
- ✅ **Arithmétique Schengen 90/180** : fenêtre glissante `[today-179, today]` correcte (pas d'année civile), bornes inclusives des deux côtés, comptage en jours distincts (`set[date]`, naturellement immunisé contre les chevauchements), 29 États à jour 2026 (BG/RO/HR inclus, IE/CY exclus).
- **Règle d'or des facteurs d'émission** : vérifiée sans fuite ; GWP-100 (25/298) conforme Annexe I ; WtT et CO₂eq TtW explicitement non sommés.
- **Compteur décroissant / reset non déclaré** : couvert deux fois (R10 avec escalade temporelle + confirmation administrateur, IR04 bloquant), et la **propagation de l'indétermination est rigoureuse** — une anomalie sur un moteur rend le total du groupe `None` au lieu de fabriquer un chiffre.
- **Réconciliation ROB sondage vs compteurs** : existe et est bien conçue (3 paliers + contre-vérification indépendante FLGO/Marad). Seul le calibrage est en cause.
- **Brouillons réellement exclus des calculs** : aucun contournement.
- **Σ allocations par cuve vs masse BDN** : couvert (R23). **Σ compartiments vs total** : R25. **BDN sans contrepartie FLGO** : R24. **Haversine vs distance loguée** : R28. **Doublon de date / ROB figé / position figée** : IR01/IR03/IR05.
- **SOF signé réellement immuable** : hash SHA-256 + `is_locked` + refus backend.
- **Verrou optimiste `updated_at`** : bien implémenté, y compris la gestion délicate rollback + refresh ORM.
- **Détection de conflit de port** : modélisation en intervalles `[ETA, ETA+escale]`, méthodologiquement correcte.
- **Suppression de leg** : scanne 12 tables dépendantes, message lisible plutôt qu'une erreur d'intégrité ; bloque explicitement sur `Booking`/`OrderAssignment`.
- **Gouvernance des overrides de permissions** : correctement auditée (avant/après journalisé, cache invalidé, `updated_by`, cellule `(administrateur, admin)` protégée).
- **Verrouillage de période paie** : étanche, aucune route d'unlock.
- **Sécurité du portail par token** : hash SHA-256 seul, audit y compris des tentatives invalides, rate-limit par IP, scope strict à une PL. Uploads via `safe_files` + double vérification de taille. Protection mass-assignment (`coerce_batch_form`).
- **Discipline lecture seule Marad réellement respectée** : aucune écriture retour, `_apply` authentiquement non destructeur (chaque champ sous condition de valeur exploitable), filtrage des placeholders, champs sensibles exclus et documentés. Robustesse soignée : insensibilité à la casse des clés JSON, verrou consultatif multi-workers, gestion fine du rate limit avec `?only=`.
- **`ClaimProvisionHistory`** : vrai historique (montant + motif + auteur + date).
- **Idempotence des alertes** (qualité MRV, rappels brouillons, cut-off, SLA) : dédup par lien+rôle / 24 h / acquittement.
- **Réconciliation de la ventilation multi-legs** : impose `sum(palettes) == booked_palettes`, refuse doublons de leg / parts ≤ 0 / legs partis, contrainte base `uq_order_assignment_order_leg`. Double-comptage rail A/rail B **correctement évité** (`Order.booking_id.is_(None)`).
- **Minimum de fret et THC présents** (`min_charge_eur` avec ligne d'ajustement visible ; option de grille `per_palette` + `Order.thc_included` ; 4 unités de tarification ; frais documentaires BL et booking séparés).
- **Capture événementielle du bord** : 26 types SOF, signature, hash, verrouillage, PDF/XLSX, documents guidés NOR/LOP/Mate's Receipt avec mentions légales pré-remplies en anglais — au-dessus de la moyenne des ERP d'armement de cette taille.
- **BAF absent mais défendable** : un voilier n'a pas d'exposition soute significative, et `RateGrid.adjustment_index` offre un levier manuel. (L'exposition de change CAF, elle, reste réelle.)
- **Tarification « w/m » absente par choix cohérent** : la tarification à l'emplacement palette avec coefficients de format est le bon modèle pour du palettisé — mais ça n'excuse pas l'absence de **plafond** de poids.

### 14.15 Séquencement recommandé

**A. Avant l'embarquement (~2 semaines) — correctifs courts, forte valeur, faible risque de régression**

1. **Schengen** : cesser d'afficher `compliant` par défaut d'information (→ `indéterminé` quand `presence` est vide alors que des périodes d'embarquement sont connues) ; corriger le sur-comptage ETA manquante ; recâbler le garde-fou d'embarquement existant avec override tracé.
2. **Horodatage bord** : `on_vessel_departed`/`on_vessel_arrived` doivent prendre l'`occurred_at` du SOF ; contrainte `atd < ata` ; ne plus écrire l'ATD depuis le « pilote départ » du port d'arrivée. *(Se déclenche précisément quand l'équipage n'a pas encore le réflexe SOF — donc les premiers voyages. Les données faussées maintenant ne se rattrapent pas.)*
3. **Carnet de bord** : corriger `+= 24` → `+= 4` (ou rebrancher sur `kpi_env.propulsion_profile`) et masquer la section quand aucun créneau n'est rempli. *(Document client, claim central, ECGT 27/09/2026.)*
4. **Capacité** : contrôle de capacité sur `order_confirm`/`order_assign`/`order_split` ; verrou à `submit()` ; plafond de poids (`available_weight_t` + `unit_weight_kg` obligatoire).
5. **Marge** : extraire une fonction unique de calcul appelée par les deux chemins ; `claims_cost_eur` non-éditable.
6. **Alerte ETA en mer** : retirer `not atd` de `dashboard_alerts.py:100` (une ligne).
7. **QHSE** (branche `feature/qhse-foundation`, avant toute mise en production) : supprimer le rejet par mot-clé (→ drapeau + table de rejets persistée) ; remplacer le `rollback()` par un savepoint par ligne et ne compter qu'après succès.
8. **Divers faible coût** : `end_date >= start_date` sur les congés ; normalisation des rangs Marad + alignement du champ lu ; brancher `notify_new_cargo_message` (one-liner, seul canal client du portail) ; `Port.max_draft_m` + avertissement à la création de leg.

**B. Chantier structurant à ouvrir ensuite (délimité, testable)**

- **Registre de connaissements** : émission en `POST`/`cargo:M` journalisée (+ `bl_issued_by_id`), gel du batch après émission (révision numérotée pour corriger), interdiction de l'import destructif si un BL existe, séquence non recyclable (table append-only ou séquence Postgres), hash SHA-256 du PDF émis. Transforme un risque juridique majeur en risque résiduel.
- **Source unique d'embarquement** : un `embarkation_periods()` fusionnant `CrewAssignment` + `MaradCrewSchedule` en jours calendaires (patron de la règle d'or `emission_ledger`). Débloque simultanément Schengen, jours embarqués, bordée et noms sur le certificat Anemos. **Rien d'autre n'a de sens avant** : brancher des alertes ou la paie sur une source fausse propage l'erreur.
- **Alertes calendaires** : job quotidien (patron `trombinoscope_scheduler`, déjà multi-workers safe) pour expirations équipage J-90/60/30/0, expiration de polices, et prescription sinistre (1 an) ; paliers en base, pas en dur.
- **Contrôle soufre** : règle bloquante `sulfur_content_pct` vs limite de zone du leg + champs d'échantillon scellé.

**C. À réserver explicitement à la revue du manager (décisions d'architecture métier)**

- **Certificat Anemos** : provisoire → définitif à la validation siège, versionnement et révocabilité. Touche la chaîne MRV et le positionnement commercial.
- **États booking `rolled`/`waitlisted`/`part_shipped`** : modifie le contrat de tous les consommateurs de `Booking.status`, donc le reporting MRV et les certificats.
- **Modélisation MLC** (SEA, rapatriement, 11 mois, garantie financière) — **précédé de la question factuelle : Marad les porte-t-il déjà ?**
- **Multi-POD par leg** (prérequis d'un vrai contrôle d'overstowage) : décision de modèle, pas correctif.
- **FMS source de vérité QHSE ?** Si oui, mynewtowt doit être un miroir strictement idempotent, jamais une seconde source d'écriture. Détermine tout le design de la Phase 1.
- **Densité/facteur par lot de carburant** au lieu des valeurs par défaut, et correction VCF (ou documentation explicite du biais dans la méthodologie publiée).

**D. Dépendances externes à débloquer maintenant (coût nul, long délai)**

- Rubriques Silae réelles (cabinet de paie) — tracé dans `silae_export.py:7-11`.
- Liste des pièces exigibles par le correspondant P&I (avant de coder les champs de sinistre).
- Id stable de planning côté éditeur Marad (supprime la clé synthétique et ses doublons fantômes).
- Hydrostatiques et capacités officielles des cuves (déjà tracé Q11) — débloque le cargo MRV auto et R23 en bloquant.

### 14.16 Écarts de documentation relevés au passage

- `docs/strategy/RAPPORT_ARCHITECTURE_UNIQUE.md:79-84` : le MRV présenté comme produisant « une preuve **officielle** d'intensité carbone » — glissement à corriger (le MRV vérifie des émissions, pas la méthode d'évitement Anemos, que le CDC qualifie honnêtement d'auto-déclaration). Formulation exactement du type que l'ECGT sanctionne.
- `docs/audit/specs/SPEC-CREW-reprise-P0.md:226` affirme que le garde-fou de conformité et son override audité sont « PRÉSERVÉS — un gain V3 à ne pas casser » : ils ont disparu du code. Spec à corriger ou garde-fou à recâbler (cf. §14.1).
- `docs/strategy/SOF_UPGRADE_PLAN.md:52-61` documente précisément l'écart laytime (S1→S8, daté 2026-06-22) — plan jamais exécuté. À requalifier (backlog assumé) ou à exécuter.
- `docs/audit/specs/SPEC-CREW-reprise-P0.md:18` signalait déjà d'adapter les requêtes filtrant `CrewAssignment.leg_id` pour les affectations « navire seul » — non traité (cf. §14.1).
- `vessel_position.get_latest_position` : docstring affirmant « ~toutes les heures » alors que la cadence réelle est quotidienne (`max_age_hours=6` rend le helper inopérant).

---

## 15. Refonte du module commercial (2026-08-26)

Chantier conduit sur `claude/commercial-module-multi-agent-fe0jhc`, en 7 lots,
après audit multi-agents et arbitrage explicite (Q1–Q6) de Julien. Détail des
décisions : `docs/architecture/ADR-010-refonte-module-commercial.md`. Journal :
entrée du 2026-08-26.

### Ce qu'il faut savoir avant de toucher au commercial

1. **Le tarif négocié ne sort jamais vers une identité non établie.** Le
   rattachement d'un compte plateforme à un client commercial est la clé d'accès
   aux prix : il se pose **à la main** par un opérateur `commercial:M`, jamais
   par dérivation d'une donnée auto-déclarée. Le parcours public dépose une
   demande **non chiffrée** ; le libre-service chiffré vit dans `/me/estimations`,
   borné aux grilles actives du client.
2. **Trois rails réservent la même cale** — offres, commandes, bookings. Une
   marchandise ne doit être comptée qu'une fois : `capacity.py` exclut les offres
   portant une commande et les commandes reprises en booking. Tout nouveau rail
   doit poser la même exclusion.
3. **`rate_offer_revisions` est append-only et chaînée.** Ni exportable, ni
   purgeable, aucune écriture hors insertion. Sa valeur probante tient à ce
   qu'aucune retouche ne passe inaperçue.
4. **« Booking note » = le contrat**, pas la confirmation de réservation client
   (renommée). Ses conditions générales sont verbatim dans
   `services/booking_note_terms.py` — ne pas les reformuler.
5. **Signature ≠ règlement**, et les conditions de règlement sont
   **déclaratives** : la facturation du fret reste hors plateforme (A5).

### État des lieux après refonte

| Sujet | Avant | Après |
|---|---|---|
| Commercial attitré | inexistant | `Client.assigned_user_id`, import Pipedrive + saisie manuelle (la saisie fait foi) |
| Réf. de grille | `RG-{année}-{NNNN}` (2 générateurs) | + `P-MMAA-MMAA-XX-YY` par ligne-route (ISO alpha-2) |
| Grilles actives par client | une seule | plusieurs, arbitrage par `is_route_default` |
| Conditions de règlement | aucune | 1 à 3 échéances, somme = 100 %, déclaratives |
| Statuts d'offre | 5 dont 2 inatteignables | 4 réels, `echue` matérialisée par balayage |
| Volume réservé par une offre | non compté | compté, sans double-comptage |
| Historique des offres | `activity_logs` sans diff | table dédiée chaînée SHA-256 |
| Booking note | export manuel « offre commerciale » | établie à la validation, corrigeable puis gelée, signable |
| Devis | public, chiffré, sans notification | estimation extranet chiffrée + demande publique non chiffrée, commercial notifié |

### Points ouverts (repris du journal)

- Rendu Word réel de la booking note à valider (LibreOffice indisponible dans le
  conteneur de développement — relecture faite par extraction structurée).
- Bac à sable Yousign avant première signature client (l'API réelle n'a jamais
  été appelée).
- Coefficients des paliers par défaut à confirmer commercialement.
- Verrou anti-impayé (pas de connaissement sans règlement) : identifié, non
  construit — suppose le suivi d'encaissement dans l'outil.
- Portail client authentifié en ternaires FR/EN alors que le catalogue couvre 5
  langues : un client passé en portugais retombe en français dans son espace.
  Hors périmètre commercial.

---

## 16. Audit et remédiation « Vente à bord » + « Caisse de bord » (2026-08-27)

Branche : `claude/audit-ventes-onboard-3uwjd7` — 15 commits, **aucune PR ouverte**.
Rapport : `docs/audit/2026-08-27-audit-vente-a-bord-caisse.md`.
Migrations : `0125` → `0131`.

### Point de départ

Un **test réel à bord n'avait pas abouti**. Un audit conduit par six auditeurs
indépendants (architecture, sécurité & intégrité financière, couverture
fonctionnelle, UX terrain, QA, reconstitution des conditions terrain) a établi
le diagnostic : un **MVP de faisabilité technique promu au rang de module
livré**. Qualité de surface réelle (patterns projet respectés, lint vert, tests
verts) ; les invariants qu'exigent de la monnaie et un registre douanier n'étaient
pas tenus.

**Cause la plus probable de l'échec du test** : `("marins", "captain")` valait
`"C"` dans la matrice, et l'override `CM` annoncé par la documentation n'était
posé par aucune migration ni aucun seed. La barre latérale se contentant du
niveau `C`, le commandant voyait le menu, ouvrait les écrans, et se heurtait à
un **403 au premier bouton**. `scripts/check_user.py` imprimait déjà
l'avertissement ; il n'avait pas été exécuté.

### Défauts corrigés (les quatre critiques)

| # | Défaut | Correctif |
|---|---|---|
| V-01 | Aucun `Session.expire` dans le dépôt : après « Basculer en espèces » ou « Annuler », le lien Stripe restait payable ~24 h → **double débit réel du marin**, absorbé en silence, non remboursable | `expire_session` sur les trois chemins ; le geste est **refusé** si la fermeture n'est pas garantie ; TTL du lien ramené à 30 min |
| V-02 | `settle_sale` créditait la caisse quel que soit le moyen de paiement → variance de clôture fausse du montant des ventes CB, chaque mois | Colonne `medium` (ADR-011) |
| V-03 | `Decimal("nan")` traversait les parseurs (`NaN == 0` vaut `False`) jusque dans des tables append-only sans route de suppression | `app/utils/decimals.py`, revalidation en service, `CHECK` en base |
| V-04 | Module hors périmètre du service worker : **aucun mode hors connexion**, contrairement à la notice | Notice rectifiée. **Le défaut technique subsiste** |

Également livrés : durcissement du webhook (montant, devise, session attendue,
`livemode`, `env`), sérialisation du règlement (`with_for_update` + unicité +
table `stripe_webhook_events`), échec transitoire → 500 pour que Stripe rejoue,
`/webhooks/` exempté du mode maintenance.

### Fonctionnalités ajoutées

- **Contrôle de caisse** (`cash_counts`) : le commandant sortant déclare sa
  caisse **coupure par coupure** à chaque fin d'embarquement et fin de mois,
  d'après le document « Master's Cash Box ». Le total est **recalculé** depuis
  les quantités, l'écart **figé** avec le solde théorique du moment. Donne
  enfin un **détenteur nommé** au cash.
- **Gel à la relève** : une déclaration de fin d'embarquement fige la
  comptabilité du débarquant. Une saisie manuelle y est refusée ; un **règlement
  de vente est reporté** au premier jour ouvert — on ne perd jamais l'écriture
  d'un paiement encaissé.
- **Remboursement** (`finance:M`, siège) par **contre-passation** — mouvement de
  caisse négatif, retours en stock, `stripe.Refund` pour les ventes CB. Le bord
  peut seulement *demander*.
- **Cloisonnement par navire** sur les deux modules (ADR-012).
- Mouvements de caisse **datés à la journée**, plus à l'instant.
- Uploads : **HEIC / HEIF / AVIF acceptés** — les justificatifs photographiés
  depuis un iPhone étaient rejetés. Correctif posé dans le validateur commun,
  donc valable pour tous les uploads photo de l'application.

### Ce qui reste absent (dit sans détour)

1. **Aucun mode hors connexion** — le plus gros manque, et celui qui compte le
   plus en mer. `sw.js` ne couvre que `/onboard*` et `/static/*`.
2. **Aucun reçu remis à l'acheteur.**
3. **Aucune correction d'un mouvement de caisse** (ni UPDATE ni DELETE).
4. Pas de reporting de CA, pas de consolidation Finance/KPI, et
   `_default_leg_id` prend le **dernier leg créé** — la donnée d'imputation au
   voyage est déjà peu fiable, à corriger **avant** de construire du reporting.
5. UX terrain notée 4/10 : erreurs métier en **JSON brut** (aucun handler HTML
   pour 400/503), pavés `onclick` inertes sous CSP, rechargement complet à
   chaque ligne, aucun `hx-` dans les gabarits du module.

### Avant tout nouveau test à bord

Checklist complète en §10 du rapport d'audit. Les points qui bloquent :

1. `SITE_URL` = URL **publique** (le défaut `localhost` casse le retour Stripe).
2. `/admin/permissions` : la migration 0125 pose `(marins × captain) = CM` —
   vérifier qu'elle est appliquée.
3. `/admin/users` : le compte du commandant doit avoir un **`assigned_vessel_id`**
   — sans lui, le cloisonnement le refuse (avec un message actionnable).
4. `docker compose exec app python scripts/check_user.py <username>` doit ne
   plus rien signaler.
5. Annoncer que **le module exige le lien satellite**.

**Recommandation de méthode** : ce module n'a **jamais eu de cahier des
charges** (recherche exhaustive de `docs/`). 45 minutes avec les Opérations pour
cadrer le besoin réel vaudraient plus que n'importe quel lot de développement.

---

## 17. Premier retour d'usage réel du contrôle de caisse (2026-08-30)

Branche : `claude/user-message-au0tqk`. Origine : courriel du Cdt de l'ANEMOS du
2026-08-29 (4 remarques). Détail complet : journal, entrée du 2026-08-30.

**Le module a fonctionné en conditions réelles** — deux déclarations
enregistrées, écarts figés, gel non déclenché (motif « fin de mois »). Les
quatre remarques portent sur l'interface et l'usage, aucune sur le calcul.

### Ce qui a été corrigé (front uniquement, aucune migration)

| # | Remarque | Correctif |
|---|---|---|
| R3 | « Le contrôle de la caisse a cassé toute la mise en page » | `</div>` en trop dans `staff/cashbox/detail.html` — il refermait le `<main>` du layout, tout le contenu suivant sortait de la grille. **Le même défaut existait sur `staff/onboard_sales/vessel.html`**, pas encore rencontré. Sentinelle `tests/regression/test_template_tag_balance.py` |
| R1 | Aucun total pendant la saisie du comptage | *Total déclaré / Théorique / Écart* recalculés à chaque frappe (`app/static/js/cash-count-form.js`), en **centimes entiers**. Rien n'est pré-rempli depuis le théorique ; le serveur reste seul à calculer la valeur écrite |
| R2 | Une déclaration partie sur une erreur de manipulation | Confirmation portant le **récapitulatif exact** de ce qui va être écrit. Défaut trouvé au passage : `cashbox-form.js` doublait l'écouteur global de `forms.js` → **deux boîtes de dialogue** sur la clôture et la rectification. Corrigé |
| R4 | « Théorique 1 676,89 € / réel 1 988,35 €, comment corriger ? » | Question d'usage : réponse dans la notice commandant §7 bis. Un écart se corrige en **remettant les écritures manquantes**, jamais en retouchant le contrôle (figé par conception) |

### Leçon transverse (vaut au-delà de la caisse)

Le défaut R3 ne se déclenchait que dans la branche `{% if cash_counts %}` : il
était **invisible pendant tout le développement et toute la recette**, et est
apparu à la seconde où le premier état de caisse a été validé. Une page rendue
« à vide » n'est pas une page vérifiée. Les tests de page ajoutés rendent
désormais le gabarit **complet, layout compris**, sur des données réelles.

### Ouvert — à arbitrer (candidat ADR-014)

Deux manques révélés par ce retour, **volontairement non implémentés** parce que
ce sont des règles de contrôle interne, pas des détails d'interface :

1. **Régularisation d'un écart de caisse.** Aujourd'hui un commandant peut faire
   disparaître un manquant par un simple « Autre encaissement », indiscernable
   d'une écriture ordinaire — le contrôle de caisse y perd l'essentiel de sa
   valeur. Symétrique exact du remboursement (ADR-013 : geste du **siège**).
   Proposition : catégories `regularisation_excedent` / `regularisation_manquant`
   sous `finance:M`, rattachées au `cash_count` soldé. Coût : une migration
   (`CHECK` sur `category`), une route, un écran.
2. **Suite donnée à un contrôle.** `cash_count.review_count()` (validé /
   contesté) existe et est testé depuis le 2026-08-27 mais **n'est exposé par
   aucune route** : une déclaration partie par erreur reste « DÉCLARÉE »
   indéfiniment. La prévention est livrée, le remède non.

---

## 18. Reprise d'historique TOWT (2026-09-02)

**Ce qu'il faut savoir avant de toucher aux legs ou aux positions.**

- Un leg `origin = 'towt_archive'` est un **fait** de l'ancienne compagnie :
  `assert_leg_mutable` refuse toute mutation ; `is_archive` pilote badges et
  masquage des boutons. Son `leg_code` est le TRIP CODE TOWT (`1YMB4`,
  `2LQF5-B`), **jamais** renuméroté — c'est la clé des noon reports (« Voyage
  number ») et de l'ancien PBIX. Ne pas « normaliser » ces codes.
- Le réel d'archive (ATD/ATA au jour, minuit UTC) est posé directement par
  `scripts/import_towt_legs.py`, pas par `voyage_transitions` (pas de SOF, pas
  de leg suivant à activer) : exception assumée, documentée ADR-014 D3.
  `etd = atd`, `eta = ata` : il n'y a **aucun prévisionnel** TOWT.
- Positions GPS d'archive : `vessel_positions.source = 'towt_archive'`,
  `import_batch` = fichier consolidé, protégées de la purge par
  `admin_data.PURGE_PROTECTED_ROWS`. Rattachement au leg **temporel**
  (`voyage_track.leg_window`) : importer les legs **avant** les positions.
- Couverture connue : legs 2024-08-09 → 2026-01-31 ; GPS à partir du
  **2024-10-21** seulement (source antérieure à confirmer) ; noon reports
  2024-09 → aujourd'hui, **non repris** (lot 2, table d'archive à arbitrer).
- Ports créés par la reprise (`source=user`, coordonnées approximatives) :
  COSTM, GTPBR, REREU, CAMAT, FRCOC (+ FRFEC si absent) — à raffiner dans
  Admin → Ports.
- **Trois couches d'immutabilité** : garde de service (`assert_leg_mutable`),
  garde ORM `before_flush` (`app/models/leg.py`, échappement
  `session.info["allow_towt_archive_write"]` pour les scripts), trigger
  PostgreSQL `trg_legs_towt_archive_readonly` (`SET LOCAL
  newtowt.allow_towt_archive_write = 'on'` pour les scripts).
- **Hors séquence vivante et hors indicateurs** (ADR-014 D7) : toute requête
  qui construit la séquence d'un navire, un indicateur publié (ponctualité,
  compteur de traversées), le contrôle MRV nocturne ou le filtre transverse
  exclut `origin = 'towt_archive'`. `build_leg_filter(include_archive=True)`
  n'est posé que par `/tracking` et `/performance/navigation`. Toute nouvelle
  requête `select(Leg)` doit choisir explicitement ; une sentinelle reste à
  écrire (lot 2).
- Angles morts : aucune météo pour l'archive ; KPI annuels 2024-2025 sans
  cargo/OPEX/MRV ; `event_capture.prefill_position` étiquette toute position
  `thalos_auto` (préexistant).

