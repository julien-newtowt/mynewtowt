# PROJECT_CONTEXT.md — mynewtowt

> Document de continuité de session, maintenu pendant l'absence du manager (2026-07-27 → 2026-08-17). Voir `CLAUDE.md` pour les consignes opérationnelles complètes, la stack technique détaillée, le glossaire maritime et les patterns critiques (base de données, permissions, MRV, sécurité) — ce document ne duplique pas ce contenu, il le référence.
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

**Priorité actuelle (2026-07-27)** : avant tout développement, Yasmin veut comprendre le fonctionnement fonctionnel/métier actuel du logiciel (pas seulement l'architecture technique déjà couverte en §2-3).

---

## 6. Prochaines étapes recommandées

Par ordre de valeur opérationnelle (cf. `CLAUDE.md` — priorité à ce qui aide Operations aujourd'hui) :
1. Combler le trou de couverture CI (P0-1) — changement mécanique, faible risque, gain de sécurité élevé avant toute autre modification.
2. Vérifier/activer la protection de branche `main` (P0-2).
3. Reprendre le backlog P1 fonctionnel (`docs/audit/backlog/`) en fonction des retours Operations à venir (retour progressif de congés, escale navire dans ~2 semaines).

## 7. Lancer l'app en local — procédure réelle (le README est incomplet sur ce point)

Testé et validé le 2026-07-28. Le README dit juste `docker compose up -d` puis `http://localhost:8000`, mais c'est insuffisant/inexact tel quel :

1. **`caddy` ne fonctionne pas en local** : son `Caddyfile` demande un vrai certificat Let's Encrypt pour `CADDY_DOMAIN` (`my.newtowt.eu` par défaut) — ça ne marche pas sans nom de domaine public. Et le service `app` n'expose **aucun port** vers l'hôte dans `docker-compose.yml` (accès prévu uniquement via `caddy`). **Contournement local** : un `docker-compose.override.yml` (non versionné, créé pour cette session) ajoute `ports: ["8000:8000"]` sur `app`, et on ne lance que `db` + `app` (`docker compose up -d db app`), sans `caddy`.
2. **`.env` à créer** (absent du repo, seulement `.env.example`) — copier `.env.example` en `.env` et changer `SECRET_KEY` (doit faire ≥32 caractères et ne pas être dans la liste `WEAK_SECRETS`, sinon le démarrage est refusé). Pour tester sans le garde-fou MFA admin (sinon on est bloqué sur l'écran de config TOTP à la première connexion), ajouter `REQUIRE_MFA_FOR_ADMIN=false`.
3. **`alembic upgrade head` échoue sur une base fraîche** en `APP_ENV=development` — car `init_db()` (`app/database.py`) exécute déjà `Base.metadata.create_all()` au démarrage de l'app dans cet environnement, créant toutes les tables avant qu'Alembic ne s'exécute. Utiliser plutôt `docker compose exec app alembic stamp head` (synchronise l'historique sans rejouer le DDL).
4. **Aucun utilisateur n'existe après ces étapes** — le compte admin documenté dans le README (`INITIAL_ADMIN_*` dans `.env`) n'est **pas** créé automatiquement au démarrage. Il faut lancer manuellement le script de seed : `docker compose exec app python -m scripts.seed_demo` (crée admin/commercial/client démo + navires/ports/legs de démonstration).
5. Environnement Windows utilisé pour cette session : Docker Desktop n'était pas installé au départ (installé via `winget install Docker.DockerDesktop`) ; le crédential helper (`docker-credential-desktop.exe`, dans `C:\Program Files\Docker\Docker\resources\bin`) doit être sur le `PATH` pour que `docker compose pull` fonctionne (sinon `error getting credentials`).

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

Pas encore initiés — seront créés (`docs/DEVELOPMENT_JOURNAL_2026-07-27_2026-08-17.md`, registre ADR) au premier développement significatif de cette fenêtre, conformément aux consignes de `CLAUDE.md`.
