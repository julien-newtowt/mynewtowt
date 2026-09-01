# Analyse & étude de faisabilité — Trombinoscope Armement (génération automatique)

> Statut : v1 — analyse de l'existant + étude de faisabilité, en lecture seule sur la branche `main`.
> Périmètre : préparation du cahier des charges `docs/strategy/CAHIER_DES_CHARGES_TROMBINOSCOPE.md`. Aucun code, migration, branche ou commit n'a été produit à ce stade.
> Source de vérité technique citée ici : fichiers `app/**` sur `main` au 2026-07-17 (branche de travail locale : `feature/mrv-gaps-remediation`, non modifiée).

## 1. Architecture générale

**Stack** (`CLAUDE.md`) : FastAPI 0.115 / Python 3.12 / Uvicorn, PostgreSQL 16 via SQLAlchemy 2 async (`Mapped[]`), Alembic pour les migrations, Jinja2 SSR + HTMX 2 + Alpine.js (pas de framework JS lourd), auth par cookies signés `itsdangerous` + bcrypt + MFA. Génération de documents : **WeasyPrint** (PDF) et **python-docx** (DOCX). Design system "Kairos" (police Manrope/DM Serif Display, palette teal `#0D5966` / vert `#87BD29` / cuivre `#B47148` / sable `#F8F2E6`).

**Organisation du code** : `app/{models,routers,services,schemas,templates,static}` — un modèle par domaine, un routeur HTTP par domaine (souvent 1:1 avec le modèle), la logique métier dans `app/services/`, les gabarits PDF sous `app/templates/pdf/`.

**Rôles & permissions** (`app/permissions.py`) : matrice `(rôle, module) → niveau` où niveau ∈ {`C`onsulter, `M`odifier, `S`upprimer}, combinables (`"CMS"`). 9 rôles : `administrateur`, `operation`, `armement`, `technique`, `data_analyst`, `marins`, `commercial`, `manager_maritime`, `rh`. La ligne `armement` (ligne 84-92) :

```
("armement", "planning"): "C"
("armement", "escale"): "C"
("armement", "kpi"): "C"
("armement", "captain"): "C"
("armement", "crew"): "CMS"   # droits complets
("armement", "mrv"): "C"
("armement", "rh"): "C"
("armement", "chat"): "C"
("armement", "veille"): "C"
```

Le rôle `armement` a donc déjà les **droits complets (Consulter/Modifier/Supprimer)** sur le module `crew` — le module naturel d'accueil du trombinoscope. Les overrides sont persistés en base (`app/models/role_permission.py`, table `role_permissions`), éditables depuis `/admin/permissions`, avec repli fail-closed sur la matrice codée en cas d'erreur.

**Système documentaire** : `docs/audit/` accueille les analyses/audits ponctuels (ce document), `docs/strategy/` accueille les cahiers des charges vivants (mis à jour dans la durée, ex. `CAHIER_DES_CHARGES_SIRH.md`), `docs/operations/` les runbooks d'exploitation, `docs/architecture/` la documentation d'architecture (C4, ADR). Convention de nommage Alembic : `migrations/versions/YYYYMMDD_HHMM_description.py`, avec docstring French expliquant le *pourquoi* métier (ex. `20260706_0095_crew_member_photo.py`).

## 2. Modèle de données

### 2.1 `CrewMember` (`app/models/crew.py`, table `crew_members`)

Champ | Type | Remarque
---|---|---
`full_name` | `String(200)` | **un seul champ**, pas de Nom/Prénom séparés
`role` | `String(60)` | texte libre — normalisé applicativement (voir §2.3)
`nationality` | `CHAR(2)` | code pays
`date_of_birth` | `Date` | nullable
`is_active` | `Boolean`, défaut `True` | **jamais modifié par la sync Marad** ; bascule via `POST /crew/members/{id}/toggle-active`
`photo_path` / `photo_filename` / `photo_mime` | `String`, tous nullable | ajoutés par la migration `20260706_0095_crew_member_photo.py` ; « enrichissement ERP local, jamais écrasé par la sync Marad » (commentaire du modèle) ; `NULL` = pas de photo → avatar par initiales
`marad_id` | `String(36)`, unique, indexé | clé de réconciliation Marad (GUID), `NULL` si saisi manuellement dans l'ERP
`email`, `phone`, `notes`, documents (passeport, Schengen, visas, livret professionnel) | — | hors périmètre trombinoscope

### 2.2 Autres entités crew

- **`CrewAssignment`** (`crew_assignments`) : historique embarquement/débarquement, `crew_member_id` + (`leg_id` et/ou `vessel_id`, au moins un des deux) + `role_on_board` + `embark_at`/`disembark_at`. Utilisé pour reconstituer "qui est à bord de quel navire à un instant T", mais **pas nécessaire** pour un trombinoscope "flotte entière, marins actifs" (décision de cadrage validée, cf. cahier des charges §Périmètre).
- **`MaradCrewSchedule`** : miroir en lecture seule du planning d'embarquement Marad (`CrewingSchedule`).
- **`Employee`** (`app/models/employee.py`, table `employees`) : personnel sédentaire (SIRH), **entité distincte** de `CrewMember`, avec `first_name`/`last_name` séparés (contrairement à `CrewMember`) et pas de champ photo. Optionnellement lié à `CrewMember` via `crew_member_id`. **Hors périmètre v1** — le trombinoscope Armement porte sur les marins (`CrewMember`), pas sur le personnel sédentaire.
- **`Vessel`** (`app/models/vessel.py`) : `code`, `name`, `imo_number`, `is_active`, `build_status`. Pas de relation directe vers l'équipage hors `CrewAssignment`/`MaradCrewSchedule`.

### 2.3 Regroupement par fonction

`app/services/crew_compliance.py` définit déjà un référentiel de rôles :

- `REQUIRED_ROLES` (6 valeurs, "armement réglementaire") : `capitaine`, `second`, `chef_mecanicien`, `cook`, `lieutenant`, `bosco`, avec labels français dans `ROLE_LABELS` (`{"capitaine": "Capitaine", "second": "Second", "chef_mecanicien": "Chef mécanicien", "cook": "Cuisinier", "lieutenant": "Lieutenant", "bosco": "Bosco"}`).
- `app/routers/crew_router.py` (ligne 53) définit `CREW_ROLES`, la liste complète des 8 valeurs utilisées dans les formulaires : les 6 ci-dessus **+ `marin` + `eleve_officier`** — ces deux dernières valeurs **n'ont pas de libellé dans `ROLE_LABELS`** (gap à combler, cf. §5 Risques).
- `normalize_role()` rabat d'anciennes valeurs anglaises (héritées d'écrans obsolètes) sur l'enum canonique français via `ROLE_SYNONYMS`.

→ Le regroupement par fonction demandé ("Capitaine, Second Capitaine, Chef Mécanicien, Second Mécanicien, Bosco, Matelot...") peut réutiliser `CREW_ROLES`/`ROLE_LABELS`/`normalize_role`, à condition de compléter `ROLE_LABELS` pour `marin` et `eleve_officier` (et de vérifier si "Second Capitaine"/"Second Mécanicien" du besoin métier correspondent exactement aux valeurs `second`/`chef_mecanicien` existantes, ou s'il faut des rôles plus fins — point à confirmer, cf. cahier des charges).

## 3. Intégrations Marad

### 3.1 API Crewing (`app/services/marad_sync.py`, `app/routers/marad_router.py`, `app/utils/marad.py`)

- **Endpoints** : `GET /api/Crewing` (liste des marins), `GET /api/CrewingSchedule` (plannings d'embarquement), `POST /api/CrewingDocuments/GetPassportDetails` (détails passeport par lot de GUID), + endpoints support `getranks`, `getVessels`, `getSyncDetails`.
- **Champs reçus** (`/api/Crewing`) : `id` (GUID), `firstName`, `lastName`, `callName`, `ranks[]`, `nationality`, `birthDate`, `email`, `mobilePhone`, `phone`. **Aucun champ photo** dans le payload — confirmé à la fois par le code et par `docs/integrations/marad-crew-readonly.md` (les endpoints "documents" Marad ne renvoient que des métadonnées, jamais de fichier binaire).
- **Champs obligatoires côté Marad** : `id` (GUID) est la clé de réconciliation ; les autres champs sont traités comme optionnels côté import (upsert tolérant).
- **Identifiants techniques** : `CrewMember.marad_id` (GUID unique, indexé) — `NULL` pour les marins saisis manuellement dans l'ERP.
- **Photos** : non disponibles via Marad, confirmé. La photo est **exclusivement** une donnée ERP locale (`photo_path`/`photo_filename`/`photo_mime`).
- **Marins actifs/inactifs** : `is_active` est **géré uniquement côté ERP** ; l'upsert Marad (`_apply()` dans `marad_sync.py`) ne touche jamais ce champ ni les champs photo — principe "additif, non destructif" documenté explicitement.
- **Relation marin ↔ navire** : pas de FK directe sur `CrewMember` ; se déduit via `CrewAssignment`/`MaradCrewSchedule` (non nécessaire pour un trombinoscope "flotte entière").
- **Relation marin ↔ fonction** : champ `role` sur `CrewMember`, alimenté par la sync (mappé depuis `ranks[]` Marad) et normalisable via `crew_compliance.normalize_role()`.
- **Historique des affectations** : oui, via `CrewAssignment` (embarquements/débarquements successifs) — hors périmètre trombinoscope v1.
- **Fréquence de synchronisation** : `POST /api/marad/refresh` (route protégée par header `X-API-Token: <MARAD_SYNC_TOKEN>`), déclenché toutes les **30 minutes** par un flux planifié **Power Automate externe**, en deux appels séparés de ~90 s (`?only=crew` puis `?only=schedules`) pour respecter le quota Marad de 1 requête/minute et le timeout proxy Caddy (~60 s). Déclenchement manuel également possible via le bouton "Synchroniser Marad" sur `/crew`.
- **Gestion des erreurs** : détection automatique du schéma d'authentification (mémorisée), détection des 429 avec message de diagnostic, traitement par enregistrement (un enregistrement en échec n'interrompt pas le lot), fonction `diagnose()` classifiant les pannes (`unreachable`/`auth_refused`/`wrong_path`/`rate_limited`/`ok`).

### 3.2 Autres intégrations Marad / composants réutilisables

Aucun autre module ne consomme directement l'API Marad Crewing hors du module `crew`. Le **pattern d'endpoint interne token-protégé + déclenchement externe par Power Automate** est en revanche généralisé et directement réutilisable pour la génération automatique du trombinoscope : `/api/weather/refresh` (`WEATHER_API_TOKEN`), `/api/veille/refresh` (`VEILLE_API_TOKEN`), `/api/tickets/escalate-sla` (`TICKETS_SLA_API_TOKEN`), `/api/quotes/followup`. Tous partagent : header `X-API-Token`, comparaison à temps constant, `503` si le token n'est pas configuré (`app/config.py`), exemption CSRF par préfixe (`app/csrf.py`).

**Aucun scheduler in-process n'existe dans le projet** (pas d'APScheduler/Celery/croniter dans `requirements.txt`) — toute automatisation périodique passe aujourd'hui par ce pattern externe. Le calcul du "dernier jour du mois" a déjà un idiome dans le code : `calendar.monthrange(year, month)[1]` (`app/services/cashbox.py`, `app/services/payroll.py`).

## 4. Système de notifications

`app/models/notification.py` / `app/services/notifications.py` / `app/routers/notifications_router.py` :

- **Canal unique : in-app** (pas d'email/push branché sur ce modèle). `NOTIFICATION_TYPES` est un tuple codé en dur (ex. `new_order`, `eosp`, `new_claim`, `invoice_issued`...) — ajouter un type `trombinoscope_generated` nécessitera de l'étendre, ainsi que `NOTIFICATION_ICONS`.
- **Ciblage** : `target_user_id` (nominatif), `target_role` (diffusion à tout un rôle, ex. `target_role="armement"`), ou `target_client_id` (espace client). **Aucune table de destinataires configurables** n'existe (pas de `NotificationSubscription`) — la "configurabilité" demandée devra s'appuyer en v1 sur `target_role` (facilement extensible plus tard vers une liste explicite).
- **Automatique vs manuelle** : aucune distinction structurelle — une notification est simplement créée par le service/routeur appelant au moment de l'évènement métier (`notify_new_order`, `notify_eosp`, etc.). Le trombinoscope suivrait le même principe : le service de génération appelle `notifications.create(...)` en fin de traitement, que la génération soit déclenchée automatiquement (cron externe) ou manuellement (route staff).
- **Email** (`app/services/email.py`) : existe comme service **séparé et non branché** aux notifications in-app ; `smtplib` best-effort (no-op si `smtp_host` non configuré), templates 3 fichiers par évènement (`<stem>.subject.txt`/`.body.txt`/`.body.html`) sous `app/templates/emails/`. Pourrait être ajouté ultérieurement pour le trombinoscope (hors périmètre v1, décision validée).

## 5. Template existant

**Mise à jour (2026-07-17)** : le gabarit réel actuellement utilisé par le service Armement (`TROMBINOSCOPE NAVIGANTS_10032026.pdf`, 9 pages) a été fourni et analysé. Il remplace les hypothèses initiales ci-dessous par des constats concrets.

### 5.1 Structure observée

- **Format paysage**, ratio proche de l'A4 paysage (297×210 mm) — **correspond exactement** au socle déjà existant `app/templates/pdf/carnet_bord/_base.html` (`@page { size: A4 landscape; margin: 20mm 15mm; }`), directement réutilisable plutôt qu'à recréer.
- **Une page par groupe**, pas un flux continu de fiches : chaque fonction (ou, cas particulier, chaque agence de sous-traitance — voir §5.3) démarre sur une nouvelle page. Correspond exactement au mécanisme `.chapter { page-break-after: always; }` déjà présent dans `carnet_bord/_base.html`.
- **En-tête de page** : titre du groupe en grand, gras, blanc, centré en haut de page (ex. "MASTER", "CHIEF ENGINEER", "BOSUN").
- **Grille de photos** : jusqu'à 4 personnes sur une seule ligne (page "MASTER", 4 personnes) ; au-delà, retour à la ligne avec un maximum observé de 3 par ligne, dernière ligne centrée (ex. pages "CHIEF ENGINEER"/"CHIEF OFFICER"/"BOSUN" : 3 puis 2, centrées). Compatible avec le motif CSS déjà existant `.crew-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }`, à valider spécifiquement sur le comportement de centrage d'une dernière ligne incomplète.
- **Fiche individuelle** : photo circulaire, entourée d'un double anneau décoratif vert clair en arcs (effet "boussole/radar"), plus élaboré qu'une simple bordure pleine — raffinement graphique à reproduire spécifiquement, distinct du `.crew-photo { border: ... }` actuel. Nom affiché en dessous, en gras, **majuscules, sur une seule ligne**, format `PRÉNOM NOM`.
- **Fond de page** : couleur unie teal (couleur de marque), avec un filigrane semi-transparent du logo/rose des vents TOWT occupant une large partie de la page.
- **Pied de page** : mention de marque "TRANSPORT À LA VOILE", lettres espacées, semi-transparente, verdâtre.
- **Qualité photo hétérogène** : confirmé sur le document réel — certaines photos sont des ID-photos professionnelles sur fond blanc uni, d'autres sont des photos informelles avec arrière-plan visible et éclairage variable (ex. pages "CADET", "ABLE SEAMAN"). Aucune contrainte de résolution/format n'est visiblement appliquée aujourd'hui.

### 5.2 Écart important — taxonomie des fonctions

Les intitulés de fonction utilisés dans le document réel sont en **anglais maritime** : `MASTER`, `CHIEF ENGINEER`, `CHIEF OFFICER`, `MATE`, `ASSISTING ELECTRICAL ENGINEERING OFFICER`, `CADET`, `BOSUN`, `ABLE SEAMAN`, et (page agence, §5.3) `FITTER`, `ABLE SEAMAN COOK`.

Ceci ne correspond **pas** à l'enum `CREW_ROLES`/`ROLE_LABELS` actuel du code (français, 8 valeurs : `capitaine`, `second`, `chef_mecanicien`, `cook`, `lieutenant`, `bosco`, `marin`, `eleve_officier`) :

Fonction du document réel | Équivalent probable dans `CREW_ROLES` | Statut
---|---|---
MASTER | `capitaine` | Libellé à traduire (FR "Capitaine" vs EN "Master")
CHIEF ENGINEER | `chef_mecanicien` | Libellé à traduire
CHIEF OFFICER | `second` | Libellé à traduire
BOSUN | `bosco` | Libellé à traduire
ABLE SEAMAN | `marin` (?) | À confirmer — pas de certitude, `marin` n'a pas de libellé anglais de référence documenté
MATE | — | **Aucun équivalent** dans `CREW_ROLES` actuel
ASSISTING ELECTRICAL ENGINEERING OFFICER | — | **Aucun équivalent** — poste spécialisé absent de l'enum
CADET | `eleve_officier` (?) | À confirmer, pas garanti
FITTER | — | **Aucun équivalent**
ABLE SEAMAN COOK | `cook` (?) | Ambigu — combine "matelot" et "cuisinier", pas clairement mappable

Ce constat remet en cause la recommandation initiale ("réutiliser `ROLE_LABELS`/`normalize_role` tel quel") : `REQUIRED_ROLES`/`ROLE_LABELS` dans `crew_compliance.py` a été conçu pour un usage régulatoire étroit (armement réglementaire, 6 postes clés), pas comme référentiel exhaustif de fonctions à bord. Le regroupement du trombinoscope devra très probablement s'appuyer sur la valeur **brute** du champ `role` (texte libre `String(60)`, potentiellement alimentée directement par `ranks[]` de Marad en anglais) plutôt que sur `normalize_role()`, ou nécessiter une table de correspondance étendue et bilingue dédiée au trombinoscope. **Point à trancher explicitement en cadrage** (cf. cahier des charges, Questions ouvertes) — a fortiori si Marad renvoie les rangs directement en anglais dans `ranks[]`, ce que cette analyse n'a pas pu confirmer côté payload réel (seul le schéma de champs est documenté, pas un échantillon de valeurs `ranks[]`).

### 5.3 Cas particulier — regroupement par agence de sous-traitance

La dernière page du document ("PELICAN MARINE SERVICES") ne regroupe **pas par fonction** mais par **prestataire externe** (une agence de manning/sous-traitance) : elle mélange plusieurs fonctions (Fitter, Able Seaman, Able Seaman Cook) au sein d'un même groupe, chaque fiche affichant alors **deux lignes de texte** sous la photo — le nom, puis la fonction individuelle (ex. "MODY BA" / "FITTER").

**Aucun champ "agence"/"prestataire"/"employeur" n'existe sur `CrewMember`** (`app/models/crew.py`) ni ailleurs dans le modèle de données examiné. Ceci est une donnée manquante réelle : soit cette information est dérivable de Marad (à vérifier — un champ `ranks[]` ou un attribut d'agence non documenté dans le schéma confirmé par cette analyse), soit elle doit être saisie manuellement dans l'ERP (nouveau champ, à l'image de la photo), soit le regroupement "par agence" reste hors périmètre v1 et ce sous-groupe de marins apparaît simplement sous sa fonction comme les autres (à trancher en cadrage — cf. cahier des charges, Questions ouvertes).

### 5.4 Gabarits analogues déjà en production (base technique réutilisable)

- **`app/templates/pdf/_base.html`** — socle de marque commun : `@page { size: A4; margin: 24mm 18mm 22mm 18mm; }` (portrait), en-tête avec logo (`{{ site_url }}/static/img/logo_NEWTOWT_web.png`), bloc type/référence de document, pied de page avec pagination + mention légale. Polices Manrope/DM Serif Display, palette Kairos.
- **`app/templates/pdf/carnet_bord/_base.html`** — variante paysage multi-chapitres, avec `.chapter { page-break-after: always; }` pour un saut de page forcé par section, et surtout un motif **directement pertinent** :
  - `.crew-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 20pt; }`
  - `.crew-member-card { text-align: center; }`
  - `.crew-photo { width: 120px; height: 120px; border-radius: 50%; }` (photo circulaire)
  - `.image-placeholder` (fond sable, bordure grise) pour les fiches sans photo.
- **`app/templates/pdf/carnet_bord/chapitre_2_equipage.html`** — implémente déjà ce motif pour lister des `crew_members` avec photo/rôle. **Point d'attention technique** : ce gabarit référence `member.photo_path` comme URL brute (`<img src="{{ member.photo_path }}">`) — un chemin relatif de stockage (ex. `crew_photos/<hex>.jpg`) qui **ne correspond à aucune route servie publiquement** (le dossier `uploads/` n'est monté ni par `app/main.py` ni par Caddy). Combiné à `HTML(string=html, base_url=settings.site_url or "").write_pdf()`, cet `<img>` résoudrait vers une URL invalide (404 probable) — il s'agit très vraisemblablement d'un **bug latent, non un pattern à reproduire tel quel**.
- **`app/templates/pdf/crew_list.html`** (route `GET /crew/border-police/{vessel_id}`) — bon modèle pour l'habillage de marque et la pagination (en-tête/pied de page, bilingue FR/EN) mais mise en page à plat (tableau trié alphabétiquement, sans regroupement, sans photo) — pas un point de départ pour la disposition en grille par fonction.

**Contraintes PDF confirmées** : format **A4 paysage** (confirmé par le document réel, cf. §5.1), moteur WeasyPrint, socle `carnet_bord/_base.html` directement réutilisable pour la page/marges/saut de page. Qualité d'image dépendante de la résolution source — les photos crew sont actuellement de simples uploads utilisateur sans contrainte de résolution minimale (cf. `app/utils/file_validation.py`), et le document réel confirme cette hétérogénéité (cf. §5.1) : pas de contrainte stricte à ajouter en v1, un simple redimensionnement à une résolution cible au rendu suffit (cf. §7). Poids de document non estimable précisément sans connaître le nombre exact de marins actifs (le document analysé en compte environ 28 sur 9 pages) et la résolution moyenne des photos sources.

## 6. Données disponibles vs manquantes / hypothèses à confirmer

Disponible aujourd'hui | Manquant / à construire
---|---
`role` (fonction) | Taxonomie complète : le document réel utilise des intitulés anglais (Master, Chief Engineer, Mate, Assisting Electrical Engineering Officer, Cadet, Fitter, Able Seaman Cook...) sans équivalent complet dans `CREW_ROLES`/`ROLE_LABELS` (6-8 valeurs françaises) — cf. §5.2, point à trancher en cadrage
`photo_path`/`photo_filename`/`photo_mime`, upload/consultation/suppression déjà implémentés | Rendu fiable de la photo **dans un PDF** WeasyPrint (le pattern existant dans `chapitre_2_equipage.html` est cassé, cf. §5.4) — nécessite un encodage base64 (data URI) au moment du rendu plutôt qu'une URL ; reproduction du double-anneau décoratif vert (cf. §5.1)
`is_active` (marins actifs/inactifs) | —
Réconciliation Marad (`marad_id`) | —
Pattern d'endpoint cron externe (Power Automate) | Le flux Power Automate lui-même (à créer, hors MyTOWT — action côté IT/Armement)
`Notification` in-app + `target_role` | Type de notification dédié (`NOTIFICATION_TYPES` à étendre) ; canal email si demandé plus tard
— | **Nom/Prénom séparés** : `CrewMember.full_name` est un champ unique. Décision validée : ajouter `first_name`/`last_name` (migration additive + reprise de données) ; affichage confirmé sur une seule ligne, majuscules, format `PRÉNOM NOM` (cf. §5.1)
— | **Archivage serveur des PDF générés** : aucun mécanisme n'existe dans tout le projet (tous les PDF actuels — facture, BL, carnet de bord, etc. — sont générés à la demande, jamais persistés). C'est une capacité nouvelle (modèle + stockage), pas une extension d'un pattern existant
— | **Champ « agence/prestataire externe »** : décidé le 2026-07-17 — nouvelle colonne `agency` sur `crew_members` (enrichissement ERP manuel), pour reproduire le regroupement "PELICAN MARINE SERVICES" observé sur le document réel (cf. §5.3 et cahier des charges §11)

## 7. Recommandation d'architecture technique

- **Étendre le module `crew` existant** plutôt que créer un nouveau module de permissions — le rôle `armement` a déjà `crew:CMS`, cohérent avec le principe observé dans le cahier des charges SIRH ("un module de permission par domaine fonctionnel réellement nouveau", pas par fonctionnalité).
- **Service de génération dédié** (nouveau, ex. `app/services/crew_directory.py`) suivant le patron déjà employé par `app/services/fleet.py` (dataclasses de résultat + cache TTL + tolérance aux erreurs) : requête des `CrewMember` actifs, regroupement par la valeur **brute** de `role` (plutôt que par `normalize_role()`/`ROLE_LABELS`, cf. §5.2 — ce référentiel régulatoire à 6 valeurs n'est pas la bonne source pour un regroupement exhaustif par fonction), encodage des photos en data URI, rendu via un nouveau gabarit `app/templates/pdf/crew_directory.html` étendant `carnet_bord/_base.html` (paysage, confirmé, cf. §5.1) et réutilisant/adaptant `.crew-grid`/`.crew-member-card` (à extraire/dupliquer proprement plutôt qu'à faire hériter deux bases entre elles), avec le double-anneau décoratif à reproduire spécifiquement.
- **Génération manuelle** : nouvelle route staff, ex. `GET /crew/trombinoscope` (aperçu HTML) et `GET /crew/trombinoscope.pdf` (téléchargement), protégées `crew:C`, suivant l'idiome existant `Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": "attachment; ..."})`.
- **Génération automatique** : nouvel endpoint interne `POST /api/trombinoscope/generate`, protégé par un nouveau token dédié (`TROMBINOSCOPE_API_TOKEN`, sur le modèle exact de `MARAD_SYNC_TOKEN`/`WEATHER_API_TOKEN`), exempté de CSRF (ajout du préfixe dans `app/csrf.py`), déclenché par un nouveau flux Power Automate planifié le dernier jour de chaque mois.
- **Archivage** : nouvelle table légère (ex. `generated_reports` : `id`, `type`, `period` (année/mois), chemin de fichier stocké hors du dossier `uploads/` habituel (ou dans un sous-dossier dédié `trombinoscope/`), `generated_at`, `generated_by`), avec une route de téléchargement de l'archive la plus récente / par mois, sur le modèle de lecture sécurisée de `safe_files.resolve_path()`.
- **Notification** : appel à `notifications.create(type="trombinoscope_generated", target_role="armement", ...)` en fin de génération (auto et manuelle), après extension de `NOTIFICATION_TYPES`/`NOTIFICATION_ICONS`.

## 8. Composants réutilisables vs à développer

Réutilisable tel quel | À développer
---|---
`crew_compliance.normalize_role()` / `ROLE_LABELS` (à compléter) | 2 colonnes `first_name`/`last_name` + migration de reprise
`CrewMember.is_active`, `photo_path`/`photo_filename`/`photo_mime` | Service `crew_directory.py` (requête + regroupement + cache)
`pdf/_base.html` (socle de marque) | Nouveau gabarit `pdf/crew_directory.html` (grille par fonction, pagination)
`.crew-grid`/`.crew-member-card`/`.crew-photo` (CSS, `carnet_bord/_base.html`) | Encodage photo → data URI au rendu (corrige le bug latent d'URL)
Pattern endpoint token-protégé (`X-API-Token`, `app/config.py`, `app/csrf.py`) | Nouvel endpoint `/api/trombinoscope/generate` + token dédié + flux Power Automate (côté IT, hors code)
`calendar.monthrange()` pour "dernier jour du mois" | Logique de détermination de la période à documenter/tester
`notifications.create()` / `target_role` | Nouveau type `NOTIFICATION_TYPES` + icône
`activity.record()` (audit) | Appel sur la nouvelle route de génération manuelle
— | Modèle + table d'archivage (`generated_reports`) + route de téléchargement/consultation d'historique
`require_permission("crew", "C"/"M")` | Aucune nouvelle permission — réutilisation directe

## 9. Risques

Risque | Sévérité | Mitigation
---|---|---
Taxonomie des fonctions incomplète (intitulés anglais du document réel — Mate, Cadet, Fitter, Able Seaman Cook... — sans équivalent dans `CREW_ROLES`/`ROLE_LABELS`, cf. §5.2) | Faible (mapping figé) | **Résolu** : mapping complet arrêté le 2026-07-17 (cf. cahier des charges §11 et module TRB-1) — 3 nouvelles valeurs `role` (`electricien`, `ajusteur`, `matelot_cuisinier`), libellés trombinoscope dédiés séparés de `ROLE_LABELS`. **Sous réserve du go du service Armement (rencontre du 2026-07-20)**
Regroupement par agence de sous-traitance ("PELICAN MARINE SERVICES", cf. §5.3) | Faible (décidé) | **Résolu** : ajout d'un champ `agency` sur `crew_members` (décision validée le 2026-07-17, cf. cahier des charges §11) — enrichissement ERP manuel en v1, à ré-évaluer comme donnée synchronisée si Marad s'avère l'exposer
Photos manquantes pour une partie des marins actifs | Moyenne–Haute (probable en v1) | Prévoir un placeholder visuel (avatar par initiales, déjà le comportement actuel côté UI) plutôt que d'exclure les marins sans photo
Synchronisation Marad — décalage entre la sync (30 min) et une génération "dernier jour du mois à minuit" | Faible | Le trombinoscope lit l'état ERP au moment de la génération ; un décalage de quelques minutes est acceptable pour un document mensuel, sans action supplémentaire nécessaire
Bug latent d'URL photo dans `chapitre_2_equipage.html` reproduit par erreur | Haute si non anticipé | Ne pas réutiliser l'`<img src="{{ photo_path }}">` tel quel ; encoder en base64 au rendu, comme documenté ici et dans le cahier des charges
Performance (nombre de marins × photos encodées en base64 dans un seul PDF) | Faible à ce stade (flotte de taille modeste, ~28 marins observés sur le document réel) | Mesurer sur un jeu de données réel lors du développement ; envisager un cache si le volume grossit
Poids/qualité d'image du PDF final (qualité hétérogène confirmée sur le document réel, cf. §5.1) | Faible-Moyenne | Redimensionner les photos à une résolution cible fixe avant encodage (ex. 300–400 px) plutôt que d'encoder les fichiers uploadés bruts
Fidélité du double-anneau décoratif et du filigrane de marque (raffinements graphiques au-delà du CSS `.crew-photo` existant) | Faible-Moyenne | Prévoir un temps d'intégration graphique dédié plutôt que de réutiliser tel quel le CSS de `carnet_bord/_base.html`
Maintenance — nouveau token/flux Power Automate à opérer (comme Marad/météo/veille) | Faible | Suivre le même runbook que `docs/operations/04-marad-crew-sync-runbook.md`, à dupliquer pour le trombinoscope
RGPD / consentement à la diffusion de la photo dans un document interne | À qualifier | Pas de mécanisme de consentement existant dans le modèle `CrewMember` — à vérifier avec la direction si un consentement explicite est requis avant diffusion (question ouverte)

## 10. Estimation de complexité par lot

Lot | Contenu | Complexité indicative
---|---|---
1 | Migration `first_name`/`last_name`/`agency` + extension `CREW_ROLES` (3 nouvelles valeurs, mapping figé cf. §5.2 et cahier des charges §11) + reprise de données depuis `full_name` | Faible
2 | Service `crew_directory.py` (requête, regroupement par fonction et par agence, cache, encodage photo base64) | Moyenne
3 | Gabarit PDF `crew_directory.html` (grille A4 paysage, double-anneau décoratif, filigrane, pagination, charte confirmée sur le document réel) | Moyenne
4 | Route manuelle (aperçu + téléchargement) + permission + audit | Faible
5 | Endpoint automatique token-protégé + configuration Power Automate (partie IT hors code) | Faible-Moyenne
6 | Modèle + stockage d'archivage + route de consultation de l'historique | Moyenne
7 | Notification in-app (`target_role="armement"`) | Faible
8 | Tests unitaires + intégration (génération, regroupement, permissions, non-régression) | Moyenne

Estimation globale : fonctionnalité de complexité **moyenne**, principalement portée par la fidélité graphique au gabarit visuel confirmé (lot 3) et la conception propre de l'archivage (lot 6) — le reste (y compris la taxonomie des fonctions, désormais figée) s'appuie sur des patterns déjà éprouvés dans le projet ou des décisions actées, sous réserve du go final du service Armement (rencontre du 2026-07-20).
