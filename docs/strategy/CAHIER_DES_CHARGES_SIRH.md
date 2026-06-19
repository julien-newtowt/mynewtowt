# Cahier des charges — Module SIRH (`rh`) & intégration paie Silae

> Spécification fonctionnelle et technique du Système d'Information
> Ressources Humaines de `mynewtowt`. Cadre la transformation du module
> `rh` (aujourd'hui stub : congés marins uniquement) en un SIRH
> opérationnel pour les **collaborateurs sédentaires** NEWTOWT, articulé
> avec le module `crew` (marins) et le logiciel de paie **Silae**.
>
> Statut : **v1 — cadrage validé** (entretien de cadrage du 2026-06-18).
> Source de vérité fonctionnelle : ce document. Source de vérité
> technique au runtime : `app/permissions.py`, `app/models/`, routers.

---

## 1. Contexte & objectifs

### 1.1 Constat de départ

Le module `rh` existant (`app/routers/modules_router.py`,
`app/templates/staff/rh/index.html`) ne couvre qu'une fonction : la
saisie et la validation de **congés de marins** (`CrewLeave`, lié à
`crew_member_id`). Il n'existe :

- **aucune entité « collaborateur sédentaire »** (les salariés à terre —
  commercial, opérations, support, direction — ne sont pas modélisés) ;
- **aucun dossier salarié** (contrat, poste, coordonnées, documents) ;
- **aucun lien avec la paie** : les éléments variables sont aujourd'hui
  ressaisis manuellement dans Silae ;
- **aucun rôle RH dédié** dans la matrice RBAC (`armement` porte
  historiquement `("armement", "rh"): "CM"`).

### 1.2 Objectifs de la v1

| # | Objectif |
|---|----------|
| O-1 | Doter NEWTOWT d'un **dossier collaborateur** unique pour les sédentaires. |
| O-2 | Gérer les **contrats** (CDI/CDD, alternance, stages) et leurs **avenants** avec **alertes d'échéance**. |
| O-3 | Centraliser **congés & absences** des sédentaires (saisie/validation RH) + **self-service consultation**. |
| O-4 | Collecter tous les **éléments variables de paie (EVP)** et les **exporter vers Silae** (fin de la ressaisie). |
| O-5 | Offrir un **espace self-service collaborateur** (consultation : solde, fiche, bulletins, demandes). |
| O-6 | Produire le **reporting RH** (effectifs, masse salariale, absentéisme, turnover, pyramide, etc.). |
| O-7 | Respecter le **RGPD** : les données les plus sensibles (RIB, NIR) **restent dans Silae**. |

### 1.3 Principe directeur — répartition MyNewtowt ↔ Silae

> **MyNewtowt = SIRH opérationnel (source des EVP). Silae = paie +
> coffre-fort des données sensibles.**

- MyNewtowt **n'est pas un logiciel de paie** : il ne calcule pas de
  bulletin, ne stocke ni RIB, ni NIR (n° de sécurité sociale), ni pièces
  d'identité. Ces données restent dans Silae.
- MyNewtowt **produit** les éléments variables de paie d'un mois donné et
  les **transmet** à Silae (export). Silae **calcule** la paie et
  **renvoie** les bulletins (PDF) que MyNewtowt **archive** et **diffuse**
  en self-service.

---

## 2. Périmètre

### 2.1 Dans le périmètre (v1)

- **Collaborateurs sédentaires** (salariés à terre).
- Dossier salarié, contrats & avenants, congés/absences, EVP,
  self-service, coffre-fort de bulletins, entretiens/évolution de poste,
  reporting RH, flux d'intégration Silae.

### 2.2 Hors périmètre

| Hors scope | Raison |
|---|---|
| **Marins / navigants** | Gérés par le module `crew` (pool 44 marins, embarquements, compliance Schengen, PAF). Le SIRH **référence** le marin mais ne duplique pas sa fiche. |
| **Calcul de paie / bulletins** | Réalisé par **Silae**. |
| **RIB, NIR, données bancaires/identité** | Restent dans **Silae** (choix RGPD). |
| **Recrutement (ATS) / on-boarding workflow** | Backlog v2. |
| **Note de frais avec workflow complet** | v1 : les frais entrent comme **EVP** ; un module dédié est en v2. |

### 2.3 Articulation `rh` ↔ `crew`

Deux populations, un socle commun :

```
                ┌───────────────────────────┐
                │   Personne (socle RH)      │  ← identité, contact
                └─────────────┬─────────────┘
              sédentaire      │      navigant
        ┌───────────────────┐ │ ┌─────────────────────┐
        │ employees (rh)    │ │ │ crew_members (crew) │
        │ contrat, poste,   │ │ │ rôle bord, passeport│
        │ EVP, congés terre │ │ │ visa, Schengen, PAF │
        └───────────────────┘ │ └─────────────────────┘
                               │
              Tous deux → EVP & contrats côté SIRH si salariés NEWTOWT
```

> **Décision v1** : on crée une entité `employees` distincte pour les
> sédentaires (pas de fusion forcée avec `crew_members`). Un marin qui
> est aussi salarié NEWTOWT peut être **rattaché** via une clé optionnelle
> `employees.crew_member_id` afin que ses **contrats** et **EVP** soient
> gérés au même endroit, sans dupliquer sa fiche navigante.

---

## 3. Acteurs & rôles

### 3.1 Acteurs

| Acteur | Description | Accès |
|---|---|---|
| **Gestionnaire RH** | Saisit/valide toutes les données RH (autorité). | Nouveau rôle `rh` (voir 3.2). |
| **Manager** | Consulte son équipe, vise les demandes (workflow v2). | `manager_maritime` (C, visa en v2). |
| **Collaborateur** | Consulte **son** dossier (self-service). | Tout staff authentifié, **scopé à lui-même**. |
| **Administrateur** | Paramétrage, habilitations. | `administrateur` (CMS). |
| **Silae** (système) | Reçoit les EVP, renvoie les bulletins. | Flux machine (voir §10). |

### 3.2 Nouveau rôle `rh` (gestionnaire RH)

La matrice actuelle ne comporte **pas** de rôle RH : il faut en ajouter
un. Proposition à valider :

- Ajouter `"rh"` à `ROLES` dans `app/permissions.py`.
- Cellule par défaut : `("rh", "rh"): "CMS"` + accès `C` sur `planning`,
  `crew`, `analytics`, `chat` (contexte), et `C` sur `finance` (masse
  salariale) à arbitrer.
- Retirer la sur-attribution historique `("armement", "rh"): "CM"` →
  rétrograder à `"C"` (consultation), l'écriture passant au rôle `rh`.
- Les overrides par cellule restent possibles via `/admin/permissions`
  (mécanisme ARC-04 existant, table `role_permissions`).

### 3.3 Self-service — principe de cloisonnement

Le self-service **n'est pas** un nouveau rôle : c'est un **espace**
(`/rh/moi`) où **tout collaborateur authentifié** consulte **uniquement
ses propres données** (filtrage serveur sur `employee.user_id ==
current_user.id`). Lecture seule en v1, hors « demandes » (congés, mise à
jour de coordonnées soumise à validation RH).

---

## 4. Modèle de données (cible)

> Tables nouvelles préfixées par leur domaine. Conventions projet :
> SQLAlchemy 2 `Mapped[]`, `await db.flush()` (jamais `commit` en route),
> audit via `services.activity.record()`.

### 4.1 `employees` — fiche collaborateur sédentaire

| Colonne | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `user_id` | FK `users.id` nullable, unique | lien compte staff (self-service). |
| `crew_member_id` | FK `crew_members.id` nullable | si le salarié est aussi marin. |
| `matricule` | str unique | code RH (mono `JetBrains`). |
| `first_name`, `last_name` | str | |
| `email_pro`, `phone_pro` | str | coordonnées **professionnelles** uniquement. |
| `birth_date` | date | âge / pyramide (pas de NIR). |
| `job_title` | str | intitulé de poste. |
| `department` | str | service / direction. |
| `manager_id` | FK `employees.id` nullable | hiérarchie. |
| `work_location` | str | site / télétravail. |
| `entry_date` | date | date d'entrée NEWTOWT (ancienneté). |
| `exit_date` | date nullable | sortie des effectifs. |
| `status` | str | `active`, `suspended` (congé long), `left`. |
| `cp_balance`, `rtt_balance` | Decimal | soldes affichés (alimentés Silae/calcul). |
| `silae_id` | str nullable | clé de rapprochement Silae. |
| `created_at`, `updated_at` | datetime | |

> **Exclu volontairement** (reste dans Silae) : adresse personnelle, RIB,
> NIR, n° pièce d'identité, données bancaires.

### 4.2 `employment_contracts` — contrats & avenants

| Colonne | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `employee_id` | FK `employees.id` | |
| `contract_type` | str | `cdi`, `cdd`, `apprentissage`, `professionnalisation`, `stage`. |
| `parent_contract_id` | FK self nullable | un **avenant** pointe vers le contrat initial. |
| `is_amendment` | bool | distingue contrat / avenant. |
| `convention` | str | défaut **convention transport / maritime**. |
| `classification` | str | coefficient / position / niveau. |
| `start_date` | date | |
| `end_date` | date nullable | CDD / alternance / stage. |
| `trial_end_date` | date nullable | fin de période d'essai. |
| `weekly_hours` | Decimal | temps de travail. |
| `gross_monthly` | Decimal | rémunération brute mensuelle de référence. |
| `motive` | str nullable | motif CDD / objet de l'avenant. |
| `document_path` | str nullable | PDF signé (stockage interne sécurisé). |
| `status` | str | `draft`, `active`, `ended`. |
| `created_at` | datetime | |

**Alertes d'échéance** (propriétés calculées, cf. pattern `crew`
`compliance_status`) : `trial_days_remaining`, `contract_days_remaining`
→ statut `expired` / `warning` (< 30 j) / `ok`. Vue dédiée
`/rh/contracts/alerts`.

### 4.3 `hr_absences` — congés & absences sédentaires

Distinct de `CrewLeave` (qui reste pour les marins). Mêmes statuts pour
cohérence UI.

| Colonne | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `employee_id` | FK `employees.id` | |
| `kind` | str | `cp`, `rtt`, `maladie`, `maternite`, `paternite`, `sans_solde`, `formation`, `autre`. |
| `start_date`, `end_date` | date | |
| `half_day_start`, `half_day_end` | bool | demi-journées. |
| `business_days` | Decimal | jours ouvrés décomptés (calcul serveur). |
| `reason` | str nullable | |
| `status` | str | `requested`, `approved`, `rejected`, `cancelled`. |
| `requested_by_id`, `decided_by_id` | FK `users.id` | |
| `decided_at` | datetime | |
| `silae_exported` | bool | marqueur d'envoi à la paie. |

### 4.4 `payroll_variables` — éléments variables de paie (EVP)

Cœur de l'intégration Silae. Une ligne = un élément variable d'un
collaborateur pour une **période de paie**.

| Colonne | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `employee_id` | FK `employees.id` | |
| `period` | str | `YYYY-MM` (mois de paie). |
| `evp_type` | str | voir table EVP §6.4. |
| `quantity` | Decimal | heures, jours, nombre. |
| `amount` | Decimal nullable | montant si saisi en €. |
| `comment` | str nullable | |
| `source` | str | `manual`, `absence` (auto depuis `hr_absences`), `import`. |
| `status` | str | `draft`, `locked`, `exported`. |
| `export_batch_id` | FK nullable | lot d'export Silae. |
| `created_by_id` | FK `users.id` | |
| `created_at` | datetime | |

### 4.5 `payslips` — bulletins (coffre-fort)

| Colonne | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `employee_id` | FK `employees.id` | |
| `period` | str | `YYYY-MM`. |
| `file_path` | str | PDF renvoyé par Silae (stockage sécurisé, accès audité). |
| `file_size`, `filename` | | |
| `uploaded_at` | datetime | |

### 4.6 `hr_reviews` — entretiens & évolution de poste

| Colonne | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `employee_id` | FK `employees.id` | |
| `review_type` | str | `annuel`, `professionnel`, `mi_parcours`. |
| `review_date` | date | |
| `next_due_date` | date nullable | rappel légal (entretien pro tous les 2 ans). |
| `summary` | text | synthèse. |
| `document_path` | str nullable | compte-rendu signé. |
| `created_by_id` | FK `users.id` | |

### 4.7 `silae_export_batches` — journal des flux

| Colonne | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `period` | str | `YYYY-MM`. |
| `kind` | str | `evp`, `absences`. |
| `format` | str | `csv` (v1) ou `api`. |
| `file_path` | str nullable | export généré. |
| `line_count` | int | |
| `status` | str | `generated`, `sent`, `acknowledged`, `error`. |
| `created_by_id` | FK `users.id` | |
| `created_at` | datetime | |

---

## 5. Modules fonctionnels

> Format aligné sur `NOTE_TECHNIQUE_CONTINUITE_OPERATIONNELLE.md`
> (Vision développeur / Vision chef de projet).

### Module RH-1 — Dossier collaborateur

#### A — Vision développeur
- **Router** : `app/routers/rh_router.py` (extraire le `rh` actuel de
  `modules_router.py`).
- **Modèle** : `employees` (§4.1).
- **Endpoints** : `GET /rh/employees` (liste + filtres service / statut /
  type de contrat), `GET|POST /rh/employees/create`,
  `/rh/employees/{id}`, `/rh/employees/{id}/edit`, `DELETE`.
- **Permission** : `require_permission("rh", "C"|"M"|"S")`.
- **Audit** : `services.activity.record()` sur create/edit/delete.

#### B — Vision chef de projet
- **Besoin** : dossier unique par sédentaire (contact pro, poste, service,
  manager, ancienneté, soldes).
- **Règles** : `matricule` unique ; un collaborateur `left` sort des
  effectifs actifs mais reste consultable (historique).
- **Acceptance** : créer un collaborateur, le rattacher à un compte staff
  (`user_id`) → il accède à son self-service.

### Module RH-2 — Contrats & avenants

#### A — Vision développeur
- **Modèle** : `employment_contracts` (§4.2), propriétés `*_days_remaining`.
- **Endpoints** : CRUD contrats sous `/rh/employees/{id}/contracts`,
  création d'avenant (`is_amendment=true`, `parent_contract_id`), vue
  transverse `GET /rh/contracts/alerts`.
- **Constantes** : `CONTRACT_TYPES`, `CONVENTION = "transport_maritime"`.

#### B — Vision chef de projet
- **Besoin** : gérer CDI/CDD, alternance/stages, historiser les avenants
  (poste, rémunération, temps de travail), alerter sur les échéances.
- **Règles** : convention par défaut **transport / maritime** (pilote les
  congés et la classification) ; un CDD/alternance/stage **doit** avoir
  `end_date` ; alerte `warning` à 30 j de la fin d'essai / du terme.
- **Acceptance** : créer un CDD se terminant dans 20 j → remonte dans
  `/rh/contracts/alerts` en `warning` ; créer un avenant de salaire →
  l'historique du contrat affiche les deux lignes.

### Module RH-3 — Congés & absences

#### A — Vision développeur
- **Modèle** : `hr_absences` (§4.3). Réutilise les pills de statut de
  `staff/rh/index.html`.
- **Endpoints** : `GET|POST /rh/absences`, `/rh/absences/{id}/decide`
  (`approved`/`rejected`), calendrier `/rh/absences/calendar`.
- **Auto-EVP** : une absence `approved` génère/maj une ligne
  `payroll_variables` (`source="absence"`).

#### B — Vision chef de projet
- **Besoin** : **saisie et validation centralisées RH** ; le
  collaborateur **demande** depuis son self-service, la RH **décide**.
- **Règles** : décompte en **jours ouvrés** selon la convention ; le solde
  CP/RTT s'affiche sur la fiche ; pas de validation managériale en v1
  (workflow manager en v2).
- **Acceptance** : un collaborateur demande 3 jours de CP → la RH approuve
  → l'absence apparaît au calendrier et alimente les EVP du mois.

### Module RH-4 — Éléments variables de paie (EVP)

#### A — Vision développeur
- **Modèle** : `payroll_variables` (§4.4).
- **Endpoints** : `GET /rh/payroll/{period}` (grille mensuelle par
  collaborateur), `POST /rh/payroll/{period}/lines`,
  `POST /rh/payroll/{period}/lock` (fige avant export).
- **États** : `draft → locked → exported`. Un EVP `exported` est
  immuable.

#### B — Vision chef de projet
- **Besoin** : centraliser, **une fois pour toutes**, tous les EVP du
  mois pour supprimer la ressaisie dans Silae.
- **Catalogue EVP v1** (à figer en table de paramétrage `evp_type`) :
  heures supplémentaires / complémentaires, absences (par type), primes
  (ancienneté, exceptionnelle, objectifs), tickets restaurant, frais
  professionnels remboursables, indemnités (transport, télétravail),
  acomptes, astreintes.
- **Acceptance** : saisir les EVP de mars, verrouiller la période →
  génération du lot d'export Silae (§10).

### Module RH-5 — Self-service collaborateur

#### A — Vision développeur
- **Espace** : `/rh/moi` + sous-pages. **Filtrage serveur strict**
  `employee.user_id == current_user.id` (jamais d'ID en paramètre).
- **Endpoints** : `/rh/moi` (fiche), `/rh/moi/absences` (solde + demande),
  `/rh/moi/bulletins` (téléchargement audité), `/rh/moi/demandes`.

#### B — Vision chef de projet
- **Besoin** : chaque collaborateur consulte **son** solde de congés, sa
  **fiche**, ses **bulletins**, et **dépose des demandes** (congés, maj de
  coordonnées pro soumise à validation RH).
- **Règles** : **lecture seule** sauf demandes ; aucune donnée d'un autre
  collaborateur n'est accessible.
- **Acceptance** : un collaborateur télécharge son bulletin de mai →
  accès tracé dans `activity_logs`.

### Module RH-6 — Coffre-fort de bulletins

- `payslips` (§4.5) : dépôt des PDF renvoyés par Silae (upload RH ou flux),
  diffusion en self-service, accès **audité**. Conservation selon durée
  légale (à paramétrer).

### Module RH-7 — Entretiens & évolution de poste

- `hr_reviews` (§4.6) : planification des entretiens annuels/professionnels,
  rappel d'échéance (`next_due_date`), pièces jointes, lien avec
  l'historique des avenants (RH-2).

### Module RH-8 — Reporting RH

#### A — Vision développeur
- **Endpoints** : `GET /rh/reporting` + fragments HTMX ; exports CSV.
  Agrégations SQL sur `employees`, `employment_contracts`, `hr_absences`,
  `payroll_variables`.

#### B — Vision chef de projet — indicateurs v1 (tous retenus)
| Indicateur | Définition |
|---|---|
| **Effectifs** | ETP, par service, par type de contrat, à une date. |
| **Mouvements** | entrées / sorties, **turnover**. |
| **Masse salariale** | brut mensuel cumulé (depuis contrats + EVP). |
| **Absentéisme** | taux par type, par service, par période. |
| **Pyramide des âges** | distribution par tranche / sexe. |
| **Ancienneté** | moyenne, distribution. |
| **Congés** | soldes restants, jours pris/à poser. |
| **Contrats** | échéances CDD/alternance, fins d'essai à venir. |

---

## 6. Sécurité & RGPD

| Règle | Décision v1 |
|---|---|
| **Données sensibles** | RIB, NIR, identité, données bancaires **restent dans Silae** — **jamais** stockées dans MyNewtowt. |
| **Minimisation** | MyNewtowt ne conserve que les données pro nécessaires à l'opérationnel RH (réduction de la surface RGPD). |
| **Habilitations** | écriture réservée au rôle `rh` (+ `administrateur`) ; consultation `C` restreinte ; self-service scopé au seul collaborateur. |
| **Audit** | tout accès aux bulletins/contrats et toute écriture RH → `activity_logs` (append-only, viewer `/admin/activity-logs`). |
| **CSRF / CSP** | formulaires standard `towt_csrf` ; pas de `<script>` inline (CSP-strict). |
| **Documents** | contrats/bulletins stockés hors webroot, servis via endpoints permissionnés (pattern `crew/tickets/{id}/download`). |
| **Conservation** | durées de rétention par type de document à paramétrer (purge ciblée via `ALLOWED_TABLES` — backlog projet). |

> Les colonnes éventuellement sensibles qui resteraient utiles (peu
> probable en v1) devraient suivre le chantier « chiffrement at-rest »
> identifié dans la dette D-12 / backlog projet.

---

## 7. Matrice de permissions (proposition)

À intégrer dans `app/permissions.py` (`_MATRIX`), overrides possibles via
`/admin/permissions`.

| Rôle | Module `rh` | Justification |
|---|---|---|
| `administrateur` | CMS | paramétrage / supervision. |
| **`rh`** *(nouveau)* | CMS | autorité de saisie/validation. |
| `manager_maritime` | C | consultation équipe (visa en v2). |
| `armement` | **C** *(rétrogradé de CM)* | l'écriture passe au rôle `rh`. |
| `operation`, `technique`, `data_analyst`, `commercial`, `marins` | C | consultation contextuelle. |

Self-service : non géré par la matrice module (accessible à tout staff
authentifié, scopé serveur à ses propres données).

---

## 8. Reprise de données & go-live

- **Reprise** : import initial par **fichier (Excel/CSV)** — export Silae
  ou registre du personnel — via un écran `POST /rh/import` (mapping
  colonnes → `employees` / `employment_contracts`, dry-run + rapport
  d'erreurs avant commit).
- **Go-live progressif** (module par module), ordre recommandé :
  1. `employees` + `employment_contracts` (socle + import).
  2. `hr_absences` (congés) + self-service consultation.
  3. `payroll_variables` + export Silae (bascule de la ressaisie).
  4. `payslips` (coffre-fort) + `hr_reviews` + reporting.
- **Critère de bascule paie** : un mois de double-saisie (MyNewtowt +
  Silae) avec rapprochement avant d'abandonner la ressaisie manuelle.

---

## 9. Lotissement (roadmap)

| Lot | Contenu | Dépendances | État |
|---|---|---|---|
| **L0** | Rôle `rh` + extraction `rh_router.py` + matrice | `permissions.py` | ✅ livré |
| **L1** | `employees` + dossier + import fichier | L0 | ✅ livré |
| **L2** | Contrats/avenants + alertes d'échéance | L1 | ✅ livré |
| **L3** | Congés/absences sédentaires + self-service consultation | L1 | ✅ livré |
| **L4** | EVP + verrouillage période | L1–L3 | ✅ livré |
| **L5** | Export Silae (CSV puis API) + journal des lots | L4 | ⏳ à venir |
| **L6** | Coffre-fort bulletins + entretiens + reporting RH | L1–L4 | ⏳ à venir |
| **v2** | Workflow validation manager, note de frais, recrutement/ATS | — | ⏳ à venir |

---

## 10. Intégration Silae — spécification du flux

> **Format v1** : export **CSV** déposé/transmis (cohérent avec les flux
> Power Automate existants `tracking`/`weather`/`veille`). Une montée vers
> l'**API Silae** est prévue en évolution.

### 10.1 Sens des flux

| Flux | Sens | Contenu | Déclencheur |
|---|---|---|---|
| **EVP** | MyNewtowt → Silae | `payroll_variables` verrouillés du mois | clôture de période RH. |
| **Absences** | MyNewtowt → Silae | `hr_absences` approuvées | inclus au lot EVP. |
| **Bulletins** | Silae → MyNewtowt | PDF de paie → `payslips` | post-calcul Silae. |
| **Référentiel** | Silae → MyNewtowt *(option)* | soldes CP/RTT, `silae_id` | rapprochement. |

### 10.2 Règles

- Un lot d'export (`silae_export_batches`) fige les EVP d'une période
  (`status=exported`, immuables).
- Idempotence : un EVP déjà `exported` n'est jamais renvoyé sans avenant
  explicite (annule/remplace tracé).
- Rapprochement par `employees.silae_id` (ou `matricule` si convenu).

### 10.3 À confirmer avec l'éditeur / le cabinet de paie

- Modalité d'échange Silae (dépôt fichier vs API + authentification).
- **Format exact du fichier d'import EVP Silae** (colonnes, codes
  rubriques) → conditionne le mapping `evp_type` → rubrique Silae.
- Mode de récupération des bulletins (export Silae vs dépôt manuel RH).

---

## 11. Glossaire RH

| Terme | Définition |
|---|---|
| **SIRH** | Système d'Information Ressources Humaines. |
| **Sédentaire** | Salarié à terre (par opposition au navigant/marin). |
| **EVP** | Élément Variable de Paie (heures supp, primes, absences, frais…). |
| **Avenant** | Modification contractuelle (poste, salaire, durée). |
| **CP / RTT** | Congés Payés / Réduction du Temps de Travail. |
| **Période d'essai** | Phase initiale du contrat, échéance à suivre. |
| **Masse salariale** | Cumul des rémunérations brutes. |
| **Turnover** | Taux de rotation (entrées/sorties sur l'effectif). |
| **Silae** | Logiciel de paie tiers (calcul des bulletins). |
| **Coffre-fort** | Espace d'archivage sécurisé des bulletins. |
| **Convention collective** | Cadre conventionnel — ici **transport / maritime**. |

---

## 12. Décisions de cadrage (entretien 2026-06-18)

| Sujet | Décision |
|---|---|
| Population v1 | **Sédentaires** (marins via `crew`). |
| Modèle de saisie | **Centralisée RH** + self-service en **consultation** (+ demandes). |
| EVP | **Tous** les éléments variables collectés et exportés vers Silae. |
| Reporting | **Tous** les indicateurs RH retenus en v1. |
| Convention | **Transport / maritime**. |
| Contrats | CDI/CDD, alternance/stages, avenants & historique, alertes d'échéance. |
| RGPD | Données sensibles (RIB, NIR…) **restent dans Silae**. |
| Reprise | **Import fichier** + **go-live progressif**. |

## 13. Questions ouvertes (à trancher avant L4/L5)

1. **Format d'import EVP Silae** (rubriques) — bloquant pour L5.
2. **Compte des congés** : règles de décompte exactes de la convention
   transport/maritime (jours ouvrés vs ouvrables, RTT).
3. **Soldes CP/RTT** : calculés par MyNewtowt ou importés de Silae ?
4. **Effectif initial** & source précise du fichier de reprise.
5. **Durées de conservation** par type de document (RGPD).
6. **Accès `rh` au module `finance`** (masse salariale) — `C` ou nul ?
