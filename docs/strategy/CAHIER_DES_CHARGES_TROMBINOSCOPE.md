# Cahier des charges — Trombinoscope Armement (module `crew`)

> Statut : v1 — cadrage quasi finalisé (rédaction du 2026-07-17, mise à jour du 2026-07-17 après réception du gabarit réel et arrêt du mapping de taxonomie). **En attente du go du service Armement (rencontre prévue le 2026-07-20)**, puis de la validation explicite avant Phase 4 — création de branche et développement.
> Source de vérité fonctionnelle : ce document. Source de vérité technique au runtime : `app/permissions.py`, `app/models/crew.py`, `app/routers/crew_router.py`.
> Document compagnon : `docs/audit/2026-07-17-analyse-faisabilite-trombinoscope.md` (analyse détaillée de l'existant et étude de faisabilité, incluant l'analyse du gabarit réel `TROMBINOSCOPE NAVIGANTS_10032026.pdf` §5).

## 1. Contexte & objectifs

### 1.1 Constat de départ

Le service Armement produit aujourd'hui manuellement, sous Word, un trombinoscope mensuel des marins de la société (Fonction, Photo, Nom, Prénom). Ce document n'existe dans aucun système ; sa production est un travail manuel répété chaque mois.

MyTOWT dispose déjà de la quasi-totalité des données nécessaires :
- `app/models/crew.py::CrewMember` porte `role`, `is_active`, et depuis la migration `20260706_0095_crew_member_photo.py`, `photo_path`/`photo_filename`/`photo_mime` (photo d'identité, enrichissement ERP jamais écrasé par la synchronisation Marad).
- `app/services/crew_compliance.py` normalise déjà les fonctions (`normalize_role`, `ROLE_LABELS`).
- `app/services/marad_sync.py` synchronise en continu (toutes les 30 min) la liste des marins depuis l'API Marad, `is_active` restant piloté côté ERP.
- Le moteur de génération PDF du projet (WeasyPrint, `app/services/pdf_generator.py`) et un motif de grille de photos d'équipage existent déjà (`app/templates/pdf/carnet_bord/_base.html`, classes `.crew-grid`/`.crew-member-card`), bien qu'imparfaitement exploité (cf. §4, bug d'URL photo identifié en analyse).

Ce qui manque : un champ Nom/Prénom séparé, un mécanisme d'archivage serveur des documents générés (aucun n'existe dans le projet à ce jour — tous les PDF sont générés à la demande sans persistance), et l'assemblage bout-en-bout (génération auto + manuelle + notification).

### 1.2 Objectifs de la v1

ID | Objectif
---|---
O-1 | Générer automatiquement un trombinoscope PDF le dernier jour de chaque mois, à partir des marins actifs synchronisés depuis Marad
O-2 | Permettre une génération manuelle à la demande, à n'importe quelle date, avec les données les plus récentes
O-3 | Produire un PDF respectant la charte graphique TOWT, téléchargeable et archivable côté serveur
O-4 | Regrouper automatiquement les marins par fonction réellement présente dans les données (pas de liste figée codée en dur)
O-5 | Notifier les utilisateurs concernés (in-app) à chaque génération, avec un mécanisme facilement extensible (rôles, puis destinataires précis à définir ultérieurement)

### 1.3 Principe directeur

**Le trombinoscope est une vue dérivée de `CrewMember`, pas une nouvelle source de vérité.** Aucune donnée métier n'est dupliquée dans un nouveau modèle « marin » : seules deux extensions ciblées sont ajoutées à `crew_members` (nom/prénom séparés) et un nouveau modèle d'archivage générique (`generated_reports`) capture uniquement les métadonnées et l'emplacement des PDF produits, jamais les données métier elles-mêmes.

## 2. Périmètre

### 2.1 Dans le périmètre (v1)

- Génération d'un **document PDF unique pour toute la flotte** (tous navires confondus), listant les **marins actifs uniquement** (`CrewMember.is_active is True`), regroupés par fonction, avec une page dédiée par agence de sous-traitance pour le personnel externe (champ `agency`, cf. §4.1 et §11).
- Génération automatique (dernier jour du mois) **et** manuelle (à la demande).
- Téléchargement immédiat + archivage serveur consultable a posteriori (au moins la dernière génération par mois).
- Notification in-app au rôle `armement` à chaque génération (auto ou manuelle).
- Ajout des colonnes `first_name`/`last_name` sur `crew_members`, avec reprise de données depuis `full_name`.

### 2.2 Hors périmètre (v1)

Hors scope | Raison
---|---
Notification par email | Canal non branché aujourd'hui aux notifications (`app/services/email.py` est un service séparé) ; décision validée de le reporter à un lot ultérieur
Document distinct par navire | Décision validée : un document unique flotte entière suffit au besoin exprimé
Inclusion du personnel sédentaire (`Employee`) | Le besoin porte explicitement sur "les marins" ; `Employee` est un modèle distinct sans champ photo
Gestion du consentement/opt-out photo | Aucun mécanisme de consentement n'existe aujourd'hui sur `CrewMember` ; à qualifier avec la direction avant développement si nécessaire (question ouverte, §13)
Historique complet illimité des trombinoscopes (rétention longue) | Politique de rétention à définir (question ouverte, §13) ; v1 se limite à un archivage simple sans purge automatique

### 2.3 Articulation `crew` (existant) ↔ trombinoscope (nouveau)

```
crew_members (existant)                 generated_reports (nouveau)
 ├─ full_name          ─┐                 ├─ id
 ├─ first_name (NEW)   ─┼─ lecture ──►    ├─ type = "trombinoscope"
 ├─ last_name  (NEW)   ─┘                 ├─ period (YYYY-MM)
 ├─ role                                  ├─ file_path (via storage
 ├─ is_active                             │   sécurisé, cf. safe_files)
 ├─ photo_path/_filename/_mime            ├─ generated_at
 └─ marad_id                              └─ generated_by (nullable si auto)
```

Le service de génération (`app/services/crew_directory.py`, nouveau) lit `crew_members`, produit le PDF, puis écrit une ligne dans `generated_reports`. Aucune écriture inverse vers `crew_members`.

## 3. Acteurs & rôles

### 3.1 Acteurs

Acteur | Description | Accès
---|---|---
Armement (rôle `armement`) | Service produisant/consommant le trombinoscope ; déjà `crew:CMS` | Génération manuelle, téléchargement, consultation de l'historique
Autres rôles avec `crew:C` (`operation`, `manager_maritime`, `technique`, `data_analyst`, `marins`, `commercial`, `administrateur` selon la matrice actuelle) | Consultation du module crew | Téléchargement du dernier trombinoscope généré (lecture seule)
Flux externe Power Automate (nouveau) | Déclenche la génération automatique fin de mois | Appel `POST /api/trombinoscope/generate` via token dédié, aucun accès UI

### 3.2 Permissions — aucun nouveau rôle ni module

Contrairement au module SIRH (`rh`), le trombinoscope **ne justifie pas** un nouveau module de permission : c'est une capacité du module `crew` existant. Toutes les routes réutilisent `require_permission("crew", "C")` (consultation/téléchargement) ou `require_permission("crew", "M")` (déclenchement manuel, si l'on souhaite le restreindre au-delà de la simple consultation — à confirmer, cf. §13). Le rôle `armement` a déjà `crew:CMS` (`app/permissions.py`, ligne 88) — aucune modification de `_MATRIX` n'est nécessaire.

### 3.3 Principe de diffusion

Le document n'est pas nominatif ni cloisonné par navire — tout profil disposant de `crew:C` peut le consulter, cohérent avec le principe "flotte entière" validé en cadrage.

## 4. Modèle de données (cible)

Toute nouvelle colonne/table suit les conventions du projet : SQLAlchemy 2 (`Mapped[]`), migration Alembic nommée `YYYYMMDD_HHMM_description.py` avec docstring métier, écriture via `await db.flush()` (jamais `commit()` dans une route), audit via `services.activity.record()` sur toute action de génération manuelle.

### 4.1 `crew_members` — extension (migration additive)

Colonne (nouvelle) | Type | Notes
---|---|---
`first_name` | `String(100)`, nullable | Repris depuis `full_name` à la migration (heuristique premier mot / reste) ; alimenté par la sync Marad (`firstName`) désormais mappé explicitement au lieu d'être fusionné dans `full_name`
`last_name` | `String(100)`, nullable | Idem, `lastName` Marad
`agency` | `String(120)`, nullable | **Nouveau, décidé le 2026-07-17** (cf. §11) : agence/prestataire de sous-traitance (ex. "Pelican Marine Services"). `NULL` = marin directement employé, pas de sous-traitance. Enrichissement ERP par défaut (saisie manuelle, comme la photo) ; à ré-évaluer comme champ synchronisé si Marad s'avère exposer cette donnée lors du développement
`full_name` | inchangé | **conservé** (compatibilité avec tout code existant qui l'utilise — pas de suppression en v1) ; devient dérivable de `first_name`/`last_name` pour les nouvelles entrées, mais reste la source d'affichage historique ailleurs dans l'application

Format d'affichage confirmé sur le document réel (`docs/audit/2026-07-17-analyse-faisabilite-trombinoscope.md` §5.1) : une seule ligne, majuscules, gras, ordre `PRÉNOM NOM` — le gabarit concatène `first_name` + `last_name` dans cet ordre au moment du rendu.

Remarque : la synchronisation Marad (`app/services/marad_sync.py`) reçoit déjà `firstName`/`lastName` séparément dans le payload `/api/Crewing` — l'ajout de ces colonnes permet de les mapper directement sans passer par une concaténation, ce qui simplifie aussi la sync (à documenter dans `docs/integrations/marad-crew-readonly.md` lors de l'implémentation).

### 4.2 `generated_reports` — nouvelle table (archivage)

Colonne | Type | Notes
---|---|---
`id` | `Integer`, PK | —
`type` | `String(60)` | `"trombinoscope"` (extensible à d'autres rapports générés à l'avenir)
`period` | `String(7)` | format `"YYYY-MM"`
`file_path` | `String(500)` | chemin relatif, résolu via un helper de lecture sécurisée sur le modèle de `safe_files.resolve_path()` (pas d'écriture via `save_upload`, le fichier n'étant pas un contenu utilisateur uploadé)
`generated_at` | `DateTime(timezone=True)` | `server_default=func.now()`
`generated_by` | `Integer`, FK `users.id`, nullable | `NULL` si généré par le flux automatique

Pas de contrainte d'unicité stricte sur `(type, period)` en v1 — une re-génération manuelle dans le même mois crée une nouvelle ligne (historique complet des générations, pas seulement la dernière), sauf décision contraire en cadrage (question ouverte, §13).

## 5. Modules fonctionnels

### Module TRB-1 — Service de génération (regroupement par fonction)

#### A — Vision développeur

- Service : `app/services/crew_directory.py` (nouveau), sur le patron dataclass + cache TTL de `app/services/fleet.py`.
- Requête : `CrewMember` où `is_active is True`, triés par groupe puis `last_name`/`first_name`.
- **Regroupement — taxonomie figée le 2026-07-17** (cf. `docs/audit/2026-07-17-analyse-faisabilite-trombinoscope.md` §5.2 pour l'analyse) : `CrewMember.role` reste stocké avec les valeurs canoniques françaises existantes de `CREW_ROLES` — **aucun changement de vocabulaire côté saisie/régulation**. Le trombinoscope affiche des libellés anglais (terminologie déjà utilisée opérationnellement par le service Armement) via un **nouveau dictionnaire de libellés dédié au trombinoscope**, défini dans le service `crew_directory.py`, strictement séparé de `ROLE_LABELS`/`REQUIRED_ROLES` (`crew_compliance.py`) qui restent inchangés (utilisés uniquement par le contrôle d'armement réglementaire, `vessel_readiness()`).

  Valeur canonique `role` | Libellé trombinoscope | Statut
  ---|---|---
  `capitaine` | MASTER | Existant, réutilisé tel quel
  `chef_mecanicien` | CHIEF ENGINEER | Existant, réutilisé tel quel
  `second` | CHIEF OFFICER | Existant, réutilisé tel quel
  `lieutenant` | MATE | Existant, réutilisé (cohérent avec `ROLE_SYNONYMS["officer"] = "lieutenant"` déjà présent)
  `bosco` | BOSUN | Existant, réutilisé tel quel
  `marin` | ABLE SEAMAN | Existant, réutilisé tel quel
  `eleve_officier` | CADET | Existant, réutilisé tel quel
  `cook` | COOK | Existant, réutilisé (n'apparaît pas dans le document analysé — conservé si renseigné)
  `electricien` **(nouveau)** | ASSISTING ELECTRICAL ENGINEERING OFFICER | Aucun équivalent existant — valeur ajoutée à `CREW_ROLES` (`app/routers/crew_router.py`), pas de migration DB requise (`role` déjà `String(60)` libre)
  `ajusteur` **(nouveau)** | FITTER | Idem — valeur ajoutée à `CREW_ROLES`
  `matelot_cuisinier` **(nouveau)** | ABLE SEAMAN COOK | Fonction combinée (matelot + cuisine), distincte de `cook` et de `marin` — valeur ajoutée à `CREW_ROLES`

- Ordre d'affichage des groupes (pages) : reprend l'ordre du document réel — MASTER → CHIEF ENGINEER → CHIEF OFFICER → MATE → ASSISTING ELECTRICAL ENGINEERING OFFICER → CADET → BOSUN → ABLE SEAMAN → pages par agence (§11).
- **Cas particulier agence externe (décidé, cf. §11)** : la page "PELICAN MARINE SERVICES" du gabarit réel regroupe par **prestataire externe**, pas par fonction, avec double affichage nom + fonction par fiche. Le nouveau champ `crew_members.agency` (§4.1) porte cette information : tout marin actif avec `agency` renseigné est extrait du regroupement par fonction et placé sur une page dédiée à son agence (une page par valeur distincte de `agency`), sa fonction individuelle affichée en sous-titre de sa fiche ; les marins sans agence (`agency IS NULL`) suivent le regroupement par fonction standard (module TRB-1 ci-dessus).
- Encodage photo : lecture du fichier via le mécanisme de résolution sécurisée existant (`safe_files.resolve_path`), redimensionnement à une résolution cible fixe, encodage base64 (data URI) injecté dans le contexte de rendu — **pas** de référence `<img src="{{ photo_path }}">` brute (corrige le bug latent identifié dans `carnet_bord/chapitre_2_equipage.html`).
- Permission : `require_permission("crew", "C")`.

#### B — Vision chef de projet

- Besoin : afficher chaque marin actif sous sa fonction, avec sa photo si disponible (sinon un espace réservé neutre), sans intervention manuelle de tri, à l'identique du document actuel (une page par fonction, plus une page dédiée au personnel sous-traité).
- Règles : un marin inactif n'apparaît jamais ; un marin sans photo apparaît quand même (pas d'exclusion) ; les fonctions sont détectées automatiquement, pas listées en dur dans le code.
- Acceptance : générer le trombinoscope avec un jeu de données mixte (marins avec/sans photo, plusieurs fonctions, au moins un marin inactif) → le PDF contient tous les actifs groupés par fonction (une page par groupe), aucun inactif, aucune erreur si une photo est absente.

### Module TRB-2 — Gabarit PDF & habillage de marque

#### A — Vision développeur

- **Gabarit confirmé par analyse du document réel** (`TROMBINOSCOPE NAVIGANTS_10032026.pdf`, cf. `docs/audit/2026-07-17-analyse-faisabilite-trombinoscope.md` §5.1) : **format A4 paysage**, une page par groupe (saut de page forcé entre chaque fonction/agence), fond teal uni avec filigrane semi-transparent du logo/rose des vents TOWT, pied de page "TRANSPORT À LA VOILE" en lettres espacées semi-transparentes.
- Gabarit : `app/templates/pdf/crew_directory.html` (nouveau), étend **`app/templates/pdf/carnet_bord/_base.html`** (déjà en A4 paysage avec `.chapter { page-break-after: always; }` — socle directement adapté, à préférer à `pdf/_base.html` qui est en portrait).
- Réutilise/adapte les classes CSS `.crew-grid`/`.crew-member-card`/`.crew-photo`/`.image-placeholder` de `carnet_bord/_base.html` (dupliquées proprement dans le nouveau gabarit plutôt que de faire hériter deux bases entre elles) ; **ajoute** l'effet de double-anneau décoratif vert clair en arcs autour de la photo, observé sur le document réel mais absent du CSS existant (raffinement graphique à développer spécifiquement).
- Grille : jusqu'à 4 fiches par ligne, retour à la ligne au-delà avec centrage de la dernière ligne incomplète (observé : 3 puis 2 centrées pour 5 personnes) — comportement à valider spécifiquement en CSS Grid/Flexbox.
- Nom affiché en une seule ligne, majuscules, gras, format `PRÉNOM NOM` — cohérent avec l'ajout de `first_name`/`last_name` (§4.1), à concaténer dans cet ordre et cette casse au rendu.
- En-tête de page : titre du groupe (nom de fonction ou nom d'agence) en grand, gras, centré.
- Rendu : `WeasyPrint` via `HTML(string=html, base_url=...).write_pdf()`, en miroir exact de `app/services/pdf_generator.py::_render_pdf`.
- **Point restant ouvert** : le mois/année de génération n'apparaît pas de façon identifiable sur le document actuel analysé (pas de page de garde ni de date visible sur les pages fournies) — à confirmer si le trombinoscope MyTOWT doit ajouter cette mention (recommandé, cf. §13).

#### B — Vision chef de projet

- Besoin : un document visuellement identique au trombinoscope actuel (même mise en page, même repère de marque), simplement produit automatiquement au lieu de manuellement, avec un repère de mois de génération.
- Règles : logo, palette et mise en page identiques au document actuellement utilisé par le service Armement ; pagination par groupe (une page par fonction/agence), pas de mélange de plusieurs fonctions sur une même page.
- Acceptance : le PDF généré, comparé visuellement au document de référence `TROMBINOSCOPE NAVIGANTS_10032026.pdf`, reproduit fidèlement la structure (une page par fonction), la disposition en grille, le style des photos, le fond de marque et le pied de page.

### Module TRB-3 — Génération manuelle

#### A — Vision développeur

- Routes (nouvelles, `app/routers/crew_router.py`) :
  - `GET /crew/trombinoscope` — aperçu HTML (optionnel v1)
  - `GET /crew/trombinoscope.pdf` — téléchargement direct, `Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=..."})`, idiome identique aux 13+ routes PDF existantes du projet.
- Permission : `require_permission("crew", "C")`.
- Audit : `services.activity.record(module="crew", entity_type="generated_report", action="generate", ...)` à chaque génération manuelle.
- Enregistre une ligne `generated_reports` (`generated_by` = utilisateur courant) puis déclenche la notification (module TRB-5).

#### B — Vision chef de projet

- Besoin : pouvoir régénérer le trombinoscope à tout moment (nouvel arrivant, erreur de photo corrigée), sans attendre la fin du mois.
- Règles : la génération manuelle utilise toujours les données les plus fraîches disponibles au moment de la demande (pas de cache figé sur plusieurs jours).
- Acceptance : depuis `/crew`, un utilisateur `armement` déclenche une génération manuelle et reçoit immédiatement le PDF à jour.

### Module TRB-4 — Génération automatique (fin de mois)

#### A — Vision développeur

- Endpoint : `POST /api/trombinoscope/generate` (nouveau, `app/routers/marad_router.py` ou un nouveau `app/routers/api_v1_router.py`-adjacent selon convention en vigueur au moment du développement), protégé par `X-API-Token: <TROMBINOSCOPE_API_TOKEN>` (nouveau setting dans `app/config.py`, `503` si non configuré — pattern identique à `MARAD_SYNC_TOKEN`), exempté de CSRF (ajout du préfixe dans `app/csrf.py`).
- Déclenchement : flux Power Automate planifié (nouveau, à créer côté IT), calendrier "dernier jour du mois" — calcul de référence côté app via `calendar.monthrange(year, month)[1]` si une vérification serveur du jour est nécessaire, sinon confiance faite à la planification Power Automate elle-même (à trancher en implémentation).
- `generated_by = NULL` dans `generated_reports` pour une génération automatique.
- Runbook opérationnel à créer : `docs/operations/0X-trombinoscope-generation-runbook.md`, en miroir de `docs/operations/04-marad-crew-sync-runbook.md`.

#### B — Vision chef de projet

- Besoin : le trombinoscope du mois est disponible sans action humaine, chaque dernier jour du mois.
- Règles : si la génération automatique échoue (ex. erreur transitoire), elle doit être rejouable manuellement sans perte (le module TRB-3 sert de filet de sécurité).
- Acceptance : au dernier jour d'un mois simulé en environnement de test, l'appel à l'endpoint protégé produit une nouvelle ligne `generated_reports` et une notification, sans intervention manuelle.

### Module TRB-5 — Notifications

#### A — Vision développeur

- Extension de `NOTIFICATION_TYPES` (`app/models/notification.py`) : ajout de `"trombinoscope_generated"`, + entrée correspondante dans `NOTIFICATION_ICONS`.
- Appel `notifications.create(db, type="trombinoscope_generated", title=..., detail=..., link="/crew/trombinoscope.pdf", target_role="armement")` en fin de génération (auto et manuelle) — réutilise le mécanisme `target_role` existant, pas de nouvelle table de destinataires en v1.
- Conçu pour rester facilement configurable : le rôle cible (`"armement"`) est un paramètre du service de génération, pas une valeur codée en profondeur — un futur lot pourra le remplacer par une liste explicite d'utilisateurs ou ajouter un canal email sans reprendre l'architecture.

#### B — Vision chef de projet

- Besoin : le service Armement est informé dès qu'un nouveau trombinoscope est disponible, sans avoir à vérifier manuellement.
- Règles : les destinataires exacts (au-delà du rôle `armement`) seront définis ultérieurement par le métier — le système ne doit pas bloquer sur cette décision et doit permettre de l'ajuster sans redéveloppement.
- Acceptance : après une génération (auto ou manuelle), tout utilisateur du rôle `armement` voit une notification in-app avec un lien vers le document généré.

## 6. Sécurité & RGPD

Règle | Décision v1
---|---
Photos de marins | Donnée déjà présente et gérée dans `CrewMember` ; le trombinoscope ne fait que les lire, jamais les modifier ni les dupliquer en base (seul le PDF généré, sur disque, contient une copie visuelle)
Consentement à la diffusion de la photo | Aucun mécanisme existant à ce jour — question ouverte (§13) à trancher avec la direction/RH avant mise en production si un consentement explicite est requis
Minimisation | Le PDF n'expose que Fonction/Photo/Nom/Prénom, aucune autre donnée sensible du dossier marin (passeport, Schengen, visas, notes)
Habilitations | Réutilisation strate de `crew:C`/`crew:M` existante, aucune nouvelle surface d'accès à auditer en dehors des nouvelles routes elles-mêmes
Audit | Toute génération manuelle journalisée via `services.activity.record()` ; la génération automatique journalisée avec `generated_by = NULL` explicite dans `generated_reports`
CSRF/CSP | Nouvel endpoint automatique exempté de CSRF par préfixe (pattern existant `app/csrf.py`), token dédié distinct des autres tokens de sync
Stockage du document | Fichier PDF stocké hors `uploads/` public, lu via un mécanisme de résolution sécurisée (pas de chemin traversal), jamais exposé par une URL statique directe
Rétention | Politique de purge non définie en v1 — question ouverte (§13)

## 7. Matrice de permissions (proposition)

Rôle | Module `crew` (existant) | Justification
---|---|---
`armement` | `CMS` (inchangé) | Génération manuelle + consultation + suppression de fiches marin déjà couvertes
`operation`, `manager_maritime`, `technique`, `data_analyst`, `marins`, `commercial` | `C` ou `CM` selon matrice actuelle (inchangé) | Consultation/téléchargement du trombinoscope, cohérent avec l'accès déjà accordé au module crew
`administrateur` | `CMS` (inchangé) | Accès complet, comme sur tous les modules

**Aucune modification de `app/permissions.py` n'est nécessaire** — le trombinoscope hérite intégralement des droits déjà accordés sur le module `crew`.

## 8. Reprise de données & go-live

1. Migration Alembic additive `first_name`/`last_name` sur `crew_members`.
2. Script de reprise (idempotent, exécuté une fois) : pour chaque `CrewMember` existant, dériver `first_name`/`last_name` depuis `full_name` par une heuristique simple (premier mot = prénom, reste = nom, ou l'inverse selon la convention de saisie observée dans les données réelles — **à valider sur un échantillon avant d'écrire le script définitif**, cf. §13).
3. Mise à jour du mapping de synchronisation Marad pour peupler `first_name`/`last_name` directement depuis `firstName`/`lastName` du payload `/api/Crewing`, sans repasser par la concaténation dans `full_name` pour les nouveaux enregistrements.
4. Déploiement du service + gabarit + routes manuelles (TRB-1 à TRB-3), testé en génération manuelle avant d'activer l'automatique.
5. Mise en place du flux Power Automate + token dédié (TRB-4), testée en environnement de recette avant bascule en production.
6. Activation de la notification (TRB-5) en dernier, une fois la génération validée.

## 9. Lotissement (roadmap)

Lot | Contenu | Dépendances | État
---|---|---|---
L1 | Migration `first_name`/`last_name` + `agency` (décidé, §11) + extension de `CREW_ROLES` (`electricien`/`ajusteur`/`matelot_cuisinier`, décidé, §11) + reprise de données | — | ⏳ à planifier, sous réserve du go Armement du 2026-07-20
L2 | Service `crew_directory.py` (regroupement par fonction et par agence, cache, encodage photo) | L1 | ⏳
L3 | Gabarit PDF `crew_directory.html` (gabarit confirmé, cf. TRB-2) | L2 | ⏳
L4 | Routes de génération manuelle + permission + audit | L2, L3 | ⏳
L5 | Endpoint automatique + token + flux Power Automate | L2, L3 | ⏳
L6 | Modèle + stockage d'archivage (`generated_reports`) + consultation d'historique | L2 | ⏳
L7 | Notification in-app | L4, L5 | ⏳
L8 | Tests unitaires/intégration + documentation (runbook, README) | L1-L7 | ⏳

## 10. Glossaire

Terme | Définition
---|---
Trombinoscope | Document listant les marins actifs avec photo, nom, prénom et fonction, regroupés par fonction
Armement | Service de l'entreprise en charge de la gestion des équipages ; également rôle RBAC (`armement`) dans MyTOWT
Marad | Fournisseur externe de l'API de gestion d'équipage (Crewing), source de synchronisation en lecture seule des marins
`CrewMember` | Modèle ORM représentant un marin dans MyTOWT (`app/models/crew.py`)
PAF | Police Aux Frontières — document de liste d'équipage réglementaire existant (`crew_list.html`), distinct du trombinoscope mais structurellement proche

## 11. Décisions de cadrage (échange du 2026-07-17)

Sujet | Décision
---|---
Nom / Prénom | Ajout de 2 colonnes additives (`first_name`/`last_name`) sur `crew_members`, avec reprise de données depuis `full_name`
Périmètre du document | Un document unique pour toute la flotte, marins actifs uniquement, regroupés par fonction — pas de document par navire
Notifications | Canal in-app uniquement en v1 (réutilise `target_role="armement"`) ; email explicitement hors périmètre v1
Agence / prestataire externe | **Confirmé** : ajout d'un nouveau champ sur `crew_members` (ex. `agency`, `String(120)` nullable, additif) pour reproduire le regroupement "PELICAN MARINE SERVICES" observé sur le document réel. Alimentation par saisie manuelle dans l'ERP en v1 (aucune confirmation que Marad expose cette donnée — à vérifier lors du développement ; si disponible côté Marad, ce sera un enrichissement de la sync plutôt qu'une saisie manuelle, sans changer le schéma). Le regroupement du trombinoscope traite alors ce champ en priorité : un marin avec `agency` renseigné apparaît sur la page de son agence (fonction en sous-titre), les autres sur la page de leur fonction.
Taxonomie des fonctions | **Figé le 2026-07-17** : `CrewMember.role` conserve ses valeurs canoniques françaises existantes (`CREW_ROLES`) ; le trombinoscope affiche des libellés anglais via un dictionnaire dédié dans `crew_directory.py`, séparé de `ROLE_LABELS`/`REQUIRED_ROLES` (inchangés, réservés au contrôle d'armement réglementaire). 3 nouvelles valeurs ajoutées à `CREW_ROLES` : `electricien` (Assisting Electrical Engineering Officer), `ajusteur` (Fitter), `matelot_cuisinier` (Able Seaman Cook). Mapping complet dans le module TRB-1 ci-dessus. **En attente de go final du service Armement (rencontre prévue le 2026-07-20).**

## 12. Questions ouvertes — réponses de cadrage attendues

### 12.1 Intrants métier attendus (débloquent le développement)

# | Question | Statut
---|---|---
1 | ~~Le gabarit Word actuel du trombinoscope n'est pas versionné dans le dépôt et n'a pas pu être analysé...~~ | **Résolu (2026-07-17)** — `TROMBINOSCOPE NAVIGANTS_10032026.pdf` fourni et analysé (cf. `docs/audit/2026-07-17-analyse-faisabilite-trombinoscope.md` §5). Format A4 paysage, une page par fonction, grille de photos en anneaux décoratifs, fond de marque — repris dans le module TRB-2 ci-dessus.
2 | ~~Le document réel utilise des intitulés de fonction en anglais maritime qui ne correspondent que partiellement à `CREW_ROLES`/`ROLE_LABELS`...~~ | **Figé (2026-07-17)** — mapping complet arrêté, cf. module TRB-1 et §11. 3 nouvelles valeurs à ajouter à `CREW_ROLES` (`electricien`, `ajusteur`, `matelot_cuisinier`). **Sous réserve du go final du service Armement, rencontre prévue le 2026-07-20** — si le service Armement invalide une correspondance lors de cet échange, ce point sera rouvert avant le lancement du développement (module TRB-1).
3 | ~~La dernière page du document réel ("PELICAN MARINE SERVICES") regroupe par agence de sous-traitance...~~ | **Résolu (2026-07-17)** — décision actée d'ajouter un champ agence/prestataire sur `crew_members` (cf. §11) ; repris dans le modèle de données (§4.1) et le module TRB-1.
4 | Un consentement explicite des marins est-il requis avant diffusion de leur photo dans ce document interne (aucun mécanisme de consentement n'existe aujourd'hui sur `CrewMember`) ? | En attente
5 | Quelle politique de rétention pour l'archive des trombinoscopes générés (conserver indéfiniment, purge après N mois, une seule version par mois ou historique complet) ? | En attente
6 | Le document réel analysé ne comporte pas de date/mois de génération visible — faut-il ajouter cette mention (recommandé) sur la version automatisée ? | En attente
7 | Les destinataires exacts de la notification (au-delà du rôle `armement`) — à définir ultérieurement, comme indiqué dans la demande initiale | Différé, non bloquant (architecture conçue pour rester configurable)

### 12.2 Petits points en suspens (non bloquants pour démarrer)

- Faut-il un endpoint d'aperçu HTML (`GET /crew/trombinoscope`) en plus du PDF, ou le PDF seul suffit-il en v1 ?
- Le déclenchement automatique doit-il re-vérifier côté serveur qu'on est bien le dernier jour du mois (défense en profondeur), ou peut-on faire confiance à la planification du flux Power Automate ?
- Faut-il restreindre le déclenchement manuel à `crew:M` plutôt que `crew:C`, par cohérence avec le fait qu'il produit un nouvel enregistrement (`generated_reports`) ?
