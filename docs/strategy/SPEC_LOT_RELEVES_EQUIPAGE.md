# Spécification technique — lot « relèves d'équipage »

> **Statut** : conception, rédigée le 2026-08-03 pendant l'attente de la fusion du
> lot 3 (Alembic). Aucune ligne de code écrite.
>
> **Référence métier opposable** : `REFERENCE_METIER_RELEVES_EQUIPAGE.md` —
> analyse des deux classeurs Excel de l'Armement. Toute divergence avec ce
> document doit être justifiée explicitement, jamais subie.
>
> Documents liés : `PLAN_UPGRADE_PHASE2_2026-08.md` §12 (RAF R9-R13),
> `07-ordre-pr-et-merge.md` §1 bis (stratégie d'attente), `CLAUDE.md`
> (§Équipage — deux registres).

---

## 1. Ce que fait ce lot, et ce qu'il ne fait pas

**Il fait** : porter dans MyTOWT le processus de relèves aujourd'hui tenu dans
Excel — simulation, décision de dates, transmission PAF à l'agent d'escale — et
le **compteur de droits à congés** qui le sous-tend.

**Il ne fait pas** :

| Hors périmètre | Pourquoi |
|---|---|
| Écrire dans Marad | La synchro est **lecture seule** par décision d'architecture |
| Importer l'annuaire de 1801 lignes | « Annuaire à ne pas ajouter en MyTOWT » (Yasmin, 2026-08-03) |
| Refaire le planning navire | Déjà dans l'ERP : `legs` + Gantt `/planning`. Le classeur `Planning navire` le duplique |
| Modéliser `ARIES` / `ATHENAIS` | Navires annulés « pour l'instant » |
| Remplacer les alertes documentaires | Marad notifie déjà l'Armement en amont |

⚠️ **Le résultat part en paie.** Une erreur ne produit pas un écran faux mais un
**bulletin faux**. Le niveau d'exigence (tests, traçabilité, reproductibilité) est
celui de la paie, pas celui d'un tableau de bord.

---

## 2. Ce qui existe déjà (vérifié dans le code, 2026-08-03)

La chaîne est **plombée de bout en bout sauf son maillon central**.

| Maillon | État | Emplacement |
|---|---|---|
| Fiches marins | ✅ | `CrewMember` (sync Marad lecture seule) |
| Relèves décidées par l'Armement | ✅ lecture seule | `MaradCrewSchedule` |
| Embarquements transcrits par les Opérations | ✅ | `CrewAssignment` (seul producteur : `escale_crew.couple_crew_assignment`) |
| Jours embarqués par marin | ✅ corrigé le 2026-07-30 | `crew_compliance.embarked_days_by_member` — **union d'ensembles de jours**, plus de double comptage |
| Paramétrage en cascade + snapshot d'audit | ✅ **motif à réutiliser** | `validation_engine.get_threshold` · `ValidationRuleThreshold` |
| Éléments variables de paie | ✅ | `PayrollVariable` (`employee_id`, `period`, `evp_type`, `quantity`, **`source`**, `status`) |
| Export paie | ✅ | `SilaeExportBatch` + `silae_export.build_evp_csv` |
| Pont marin ↔ salarié | ✅ | `Employee.crew_member_id` (FK nullable) |
| Liste d'équipage PAF | 🟡 partielle | `/crew/border-police/{vessel_id}` — PDF bilingue, mais lit **seulement** les affectations rattachées à un leg, et **sans** n° de vol ni heure |
| Billets de transport | 🟡 | `CrewTicket` (`mode`, `reference`, `carrier`, `departure_at`, `departure_location`) — champs présents, non exploités pour la PAF |
| **Décision de relève** | ❌ | **rien** — c'est le maillon manquant |
| **Grand livre d'acquisition** | ❌ | `CrewLeave` gère demande/approbation ; `services/leaves.py` ne fait que lister et compter. **Aucune notion de solde** |
| **Simulation / anticipation** | ❌ | tout est dans Excel |

Chaîne cible :

```
décision de relève  →  jours classés par statut  →  coefficient (matrice)
                    →  PayrollVariable (source=accrual)  →  SilaeExportBatch  →  CSV
       [à créer]              [à créer]                        [existe]
```

---

## 3. ✅ Décision d'architecture — **option A retenue** (Yasmin, 2026-08-03)

**MyTOWT planifie et simule ; Marad reste le registre des relèves décidées.**
La synchro Marad demeure **en lecture seule**, et un écran de réconciliation
affiche les écarts entre le plan MyTOWT et ce qui a réellement été saisi dans
Marad. La double saisie est assumée comme le prix de l'absence de dépendance
externe.

Conséquence à tenir dans tout le lot : **trois registres coexistent**
(`marad_crew_schedules`, `crew_assignments`, `CrewRotationPlan`). La règle d'or de
`CLAUDE.md` — *tout indicateur d'équipage doit dire de quel registre il parle* —
devient la contrainte structurante du lot, pas une recommandation. Voir R-D au §12.

Le détail du choix est conservé ci-dessous pour mémoire.

---

## 3 bis. Le choix, pour mémoire

**Qui détient la vérité d'une relève décidée ?** Le processus réel est :
simulation Excel → décision Excel → **saisie dans Marad** → Marad resynchronise.

Si MyTOWT héberge la décision, deux voies incompatibles :

| | **A — MyTOWT planifie, Marad reste le registre** ✅ *recommandé* | **B — MyTOWT devient la source de vérité** |
|---|---|---|
| Décision saisie dans | MyTOWT (plan), puis **ressaisie dans Marad** par l'Armement | MyTOWT seul |
| Intégration Marad | **inchangée** (lecture seule) | nécessite une **écriture** vers Marad |
| Double saisie | oui, une fois par relève | non |
| Faisable maintenant | **oui** | non — hors périmètre, dépend de MaraSoft |
| Valeur immédiate | la **simulation**, qui est précisément ce qui manque | idem + suppression de la double saisie |

**Recommandation : option A**, avec un **écran de réconciliation** plan ↔ Marad qui
affiche les écarts. C'est faisable sans dépendance externe, et la réconciliation
apporte un bénéfice propre : aujourd'hui rien ne détecte un plan qui a divergé de
ce qui a réellement été saisi dans Marad.

⚠️ **Conséquence à assumer sous A** : il existera **trois registres**. La règle d'or
de `CLAUDE.md` (« tout indicateur d'équipage doit dire de quel registre il parle »)
devient encore plus contraignante. Le §5.4 ci-dessous fixe la règle de lecture.

---

## 4. Modèle de données

### 4.1 `CrewRotationPlan` — la relève décidée (nouveau)

Le bloc 1 des feuilles navires, rendu explicite.

| Champ | Type | Rôle |
|---|---|---|
`id` | `Integer` PK | |
`crew_member_id` | FK `crew_members`, **NOT NULL** | le marin |
`vessel_id` | FK `vessels`, **nullable** | navire — `NULL` si affectation à un lieu |
`place` | `String(80)`, nullable | lieu hors navire (ex. `VIETNAM`) |
`role_on_board` | `String(60)` | poste, **valeur canonique** (cf. §4.5) |
`manning` | `String(10)` | `TOWT` \| `PMS` |
`embark_planned_on` | `Date`, NOT NULL | **la décision d'Armement** |
`disembark_planned_on` | `Date`, nullable | calculé par défaut, surchargeable |
`contract_days` | `Integer`, nullable | `NULL` ⇒ résolu par la matrice (§5) |
`contract_days_override` | `Integer`, nullable | valeur imposée pour ce cas |
`contract_days_justification` | `Text`, nullable | **obligatoire si override** |
`status` | `String(40)` | workflow §4.2 |
`om_ok` / `saisie_ok` / `visa_state` | `Boolean` / `Boolean` / `String(20)` | **dimensions séparées** (§4.2) |
`reliever_plan_id` | FK self, nullable | la relève qui succède |
`marad_schedule_id` | FK `marad_crew_schedules`, nullable | rapprochement (§3, option A) |
`notes` | `Text` | |
`created_by_id` / `created_at` / `updated_at` | | traçabilité |

**Contrainte** : `CHECK (vessel_id IS NOT NULL OR place IS NOT NULL)` — une relève
porte **soit** un navire, **soit** un lieu, jamais aucun des deux.

⚠️ **Pas de XOR strict** : un marin *peut* être à un lieu rattaché à un navire en
construction. On exige au moins l'un des deux, pas l'exclusivité — contrairement à
`PackingList.order_id`/`booking_id` où le XOR est un vrai invariant métier.

**Contrainte** : `CHECK (contract_days_override IS NULL OR
contract_days_justification IS NOT NULL)` — l'override sans justification est
**refusé en base**, pas seulement dans le formulaire. La justification est le seul
moyen de distinguer une décision assumée d'une erreur de saisie (§5.3 de la
référence métier : les `60`/`70`/`90` en dur d'Excel sont indiscernables d'un
bug).

### 4.2 Statut — trois dimensions, pas une chaîne

Excel encode tout dans un seul libellé :
`« Visa en cours + OM OK + saisie OK »`. Quatre informations dans une chaîne, donc
**infiltrable et non filtrable**.

Décomposition retenue :

| Dimension | Champ | Valeurs |
|---|---|---|
Avancement | `status` | `ouvert` → `confirme` → `waiting_to_embark` → `embarque` → `debarque` → `annule` |
Ordre de mission | `om_ok` | booléen |
Saisie dans Marad | `saisie_ok` | booléen |
Visa | `visa_state` | `non_requis` \| `en_cours` \| `ok` |

Le libellé Excel se reconstitue à l'affichage. **Aucune information perdue**, et
chaque dimension devient filtrable — ce qu'un tableur ne permet pas.

Vocabulaire d'incertitude de la feuille `Glossaire` (`TBN` = to be nominated,
`TBC` = to be confirmed, `NC` = new comer) : conservé comme **marqueur sur le nom
du releveur**, pas comme statut — c'est une qualification de la personne, pas de
la relève.

### 4.3 `CrewAccrualSnapshot` — le grand livre, figé par période

Le solde se **calcule** (§6). Ce qui se **persiste**, c'est l'instantané au moment
où l'on génère la paie — pour pouvoir rejouer un calcul contesté.

| Champ | Type | Rôle |
|---|---|---|
`id` | `Integer` PK | |
`crew_member_id` | FK, NOT NULL | |
`period` | `String(7)` | `AAAA-MM`, aligné sur `PayrollVariable.period` |
`days_by_status` | `JSON` | `{"embarque": 24, "debarque": 6, "formation": 1}` |
`coefficients_used` | `JSON` | **snapshot de la matrice consommée**, avec la provenance de chaque valeur |
`accrued_days` | `Numeric(10,2)` | crédit de la période |
`consumed_days` | `Numeric(10,2)` | débit |
`balance_after` | `Numeric(10,2)` | solde à la clôture |
`payroll_variable_id` | FK `payroll_variables`, nullable | ligne EVP produite |
`computed_at` / `computed_by_id` | | |

**Unicité** : `(crew_member_id, period)`.

> 🔑 `coefficients_used` est **la** garantie d'auditabilité. Yasmin annonce que la
> matrice va évoluer : sans snapshot, un solde de congés contesté six mois plus
> tard serait irreproductible. Motif déjà appliqué par le MRV
> (`QualityCheckResult.details`).

### 4.4 `CrewParameter` — la matrice paramétrable

Décalque de `ValidationRuleThreshold`, adapté à la clé `(poste, manning)`.

| Champ | Type |
|---|---|
`id` | `Integer` PK |
`parameter_name` | `String(80)` — ex. `contract_days`, `accrual_coefficient` |
`role_on_board` | `String(60)`, **nullable** — `NULL` = toutes fonctions |
`manning` | `String(10)`, **nullable** — `NULL` = toutes sociétés |
`value` | `Numeric(15,6)` |
`unit` | `String(20)`, nullable |
`provisional` | `Boolean` — valeur non calibrée |
`note` | `Text` |
`updated_by_id` / `updated_at` | |

**Unicité** : `(parameter_name, role_on_board, manning)`.

`Numeric(15,6)` et non `Integer` : les coefficients d'acquisition sont décimaux
(0,9 / 0,3 / 0,2). Une seule table pour les durées **et** les coefficients.

### 4.5 Vocabulaire canonique des postes

Quatre vocabulaires coexistent dans les classeurs (anglais complet, abréviations,
français, codes étoilés) plus l'énumération `CREW_ROLES` de MyTOWT.

✅ **LIVRÉ le 2026-08-03.** ⚠️ **Correction de cette spec** : le tableau ci-dessous
proposait des valeurs canoniques **en anglais** (`master`, `chief_officer`…). C'est
faux — l'enum canonique du projet est **en français** (`CREW_ROLES` :
`capitaine`, `second`, `chef_mecanicien`…, avec `cook` pour seule exception).
L'implémentation a donc **étendu l'existant** plutôt que d'introduire un cinquième
vocabulaire. Table réelle : `crew_compliance.ROLE_SYNONYMS`, résolution par
`parse_role_token`.

| Canonique | Alias reconnus |
|---|---|
`master` | Master · MASTER · Capitaine · `MASTER*` |
`chief_officer` | Chief Officer · CHOFF · Second Capitaine · `CO*` |
`mate` | Mate · MATE · Lieutenant Pont · `MATE*` |
`chief_engineer` | Chief Engineer · CHENG · Chef Mécanicien · `CE*` |
`bosun` | BOSUN · **BOSCO** · `BOSUN*` |
`ab1` / `ab2` | AB1 · AB 1 · Matelot · `AB1*` |
`fitter` | Fitter · Fitter/Oiler · `FITTER*` |
`cook` | Cook · Cuisinier · `COOK*` |
`deck_cadet` | Deck Cadet · CADET · Élève |
`electrotech` | Electrotech · ELECT · Electrical Engineering Officer Assistant |

Deux marqueurs à porter, confirmés par Yasmin (« doublure et l'étoile, ça veut dire
obligatoire ») :

- `is_mandatory` — poste requis pour l'appareillage (l'astérisque) ;
- `requires_understudy` — doublure obligatoire (le suffixe `Db`, écrit en clair
  dans ATLANTIS : « BOUCHER Pierrick, **doublure** LE RAT Matthieu »).

⚠️ **Lecture à confirmer** : rien ne prouve dans les classeurs que `*` et `Db`
signifient la même chose ; la réponse les a groupés sous « obligatoire ». Les
modéliser **séparément** coûte deux booléens et évite de figer une confusion.

---

## 5. Résolution des paramètres — la cascade

### 5.1 Le contrat

```python
async def get_crew_parameter(
    db: AsyncSession,
    parameter_name: str,
    *,
    role_on_board: str | None = None,
    manning: str | None = None,
) -> CrewParameterValue | None:
    """(poste, manning) → (manning) → (poste) → défaut codé.

    Renvoie la valeur ET SA PROVENANCE. Fail-closed : toute erreur DB retombe
    sur les défauts codés, jamais sur une absence silencieuse.
    """
```

`CrewParameterValue` porte `value`, `unit`, `provisional` et **`source`**
(`"role_manning"` | `"manning"` | `"role"` | `"coded_default"`), exactement comme
`ThresholdValue`.

### 5.2 Matrice initiale des durées

| | **TOWT** | **PMS** |
|---|---|---|
Hors élève | **60 j** | **90 j** |
`deck_cadet` | **90 j** | ⏳ **non fournie** |

⛔ **La cellule PMS/élève reste vide.** La résolution retombera sur
`(manning=PMS)` = 90 j avec `source="manning"`, et **l'interface devra le
signaler** — « valeur générique, pas de règle spécifique élève/PMS ». Jamais la
présenter comme voulue.

C'est la même discipline que le statut Schengen `indetermine` livré le 2026-07-30 :
*une valeur par défaut ne doit pas se faire passer pour une décision.*

### 5.3 Coefficients d'acquisition

| Statut | TOWT | PMS |
|---|---|---|
`embarque` | **0,9** | **1,0** |
`embarque_autre` | 0,9 | 1,0 |
`debarque` | **−1** | 0 |
`formation` | 0,6 | — |
`accident_travail` | 0,2 | — |
`teletravail` | 0,9 | — |
`embarque_cadet` | **0,3** | — |
`conduite` / `dispo` | 0 | — |
**`detache_a_terre`** | **0,675** | ⏳ *(accord TOWT uniquement)* |

Le personnel **au Vietnam** est en position **`embarque`** (tranché le
2026-08-03) : pas de statut dédié. Le modèle doit donc admettre un embarquement
**sans navire ni leg** compté comme embarqué ⇒ **ne jamais déduire le statut de la
présence d'un `vessel_id`**.

`embarque_cadet` (0,3) est un **statut**, pas une fonction : un élève embarqué
relève de ce statut, pas de `embarque`. À ne pas confondre avec la durée de contrat
d'un élève (90 j TOWT), qui est un paramètre distinct.

---

## 6. Le calcul d'acquisition

### 6.1 ⚠️ Deux conventions de comptage — l'erreur à ne pas commettre

| Quantité | Convention | Pourquoi |
|---|---|---|
**Jours à bord** (contrat) | **exclusive** — `fin − début`, jour d'embarquement = 0 | Cohérent avec « contrat 60 j ⇒ fin = début + 60 ». C'est ce qu'Excel calcule |
**Jours de présence Schengen** | **inclusive** | La règle 90/180 compte le jour d'entrée **et** celui de sortie |
**Jours d'acquisition** | ✅ **ni l'une ni l'autre — la question se dissout** | L'accord d'entreprise compte en **« jour travaillé »**, pas en durée entre deux dates. On classe donc chaque **jour calendaire** dans **exactement une position** et on applique son taux. Aucune ambiguïté de borne : le jour du voyage est en position `conduite`, celui à bord en position `embarqué` |

**Contrainte de nommage, non négociable** : toute fonction de comptage dit dans son
**nom** et sa **docstring** quelle convention elle applique.

```
elapsed_days_on_board(...)      # exclusive — contrat
schengen_presence_days(...)     # inclusive — réglementaire
accrual_days_by_status(...)     # convention explicitée dans la docstring
```

Confondre les deux casse **soit un contrat, soit une conformité réglementaire**.
L'écart mesuré sur un cas réel (DELANNOY, ANEMOS) est de 62 contre 63 jours.

### 6.2 Classement des jours

Pour un marin et une période, construire un **ensemble de jours calendaires par
statut**, sans recouvrement possible :

1. jours couverts par une relève réalisée → `embarque` (ou `embarque_cadet` si le
   poste est `deck_cadet`) ;
2. jours couverts par un congé/absence (`CrewLeave`, `HrAbsence`) → statut dérivé
   du type ;
3. jours restants → `debarque`.

**Un jour appartient à exactement un statut.** En cas de conflit — un congé
chevauchant un embarquement — c'est une **anomalie à signaler**, pas à arbitrer en
silence : elle traduit une incohérence de saisie qui aurait un effet direct sur la
paie.

**Source des jours embarqués** : réutiliser `embarked_days_by_member`, qui construit
déjà une **union d'ensembles de jours** sur les deux registres et ne double plus
depuis le 2026-07-30. Ne pas réécrire ce calcul.

### 6.3 Production de l'EVP

Pour chaque marin ayant un `Employee` rattaché (`Employee.crew_member_id`) :

```
solde_période = Σ (jours_du_statut × coefficient(statut, manning))
```

Puis une ligne `PayrollVariable` avec **`source="crew_accrual"`** — distinct du
défaut `"manual"`, de sorte qu'un calcul ne soit jamais confondu avec une saisie
humaine — et `status="draft"` jusqu'à validation.

⚠️ **Un marin sans `Employee` rattaché ne produit aucun EVP** et doit apparaître
dans une **liste d'exclusion visible**. Silencieusement omis, c'est un marin non
payé de ses congés.

---

## 7. Transmission PAF et note d'escale

L'agent d'escale **ne décide rien** : il organise les RDV PAF à partir de ce que
l'Armement transmet. Le lot doit produire cette transmission, pas la lui faire
saisir.

### 7.1 Données exigées (bloc 2 des feuilles navires)

Symétrique — **montants et descendants**, la description orale ne mentionnait que
les descendants mais la PAF contrôle les deux sens :

| Champ | Source |
|---|---|
Poste | `CrewRotationPlan.role_on_board` (libellé canonique) |
Nom, prénom | `CrewMember` |
Nationalité | `CrewMember.nationality` |
N° passeport / titre de séjour / pièce d'identité | `CrewMember` |
ETA (montants) / ETD (descendants) | `CrewRotationPlan` + `Leg` |
**N° de vol ou de train** | `CrewTicket.reference` / `carrier` / `mode` |
**Heure de départ / d'arrivée** | `CrewTicket.departure_at` |

### 7.2 Réparer la liste PAF existante

`/crew/border-police/{vessel_id}` produit déjà un PDF bilingue FR/EN, mais :

- il lit **uniquement** `CrewAssignment` rattachées à un leg
  (`crew_router.py:1166`) — donc **ni Marad, ni les affectations à un lieu**. En
  production la liste est **probablement incomplète** ;
- il **ignore** `CrewTicket`, donc pas de n° de vol ni d'heure — les deux champs
  que l'Armement transmet justement.

⇒ Rebrancher sur la **même union de registres** que le compteur de jours, et joindre
`CrewTicket`. Les pièces existent, mal assemblées.

### 7.3 Génération de la note d'escale

Cible évoquée par Yasmin : « à terme une structure *event driven* où, à partir des
données déclarées, on génère un doc similaire au template — un doc plus propre
même ». Le template de référence est
`2026_S29_Port Call Preparation-ARTEMIS-Voyage 2BGPFR6`.

**À traiter dans un lot ultérieur** : la note d'escale agrège bien plus que
l'équipage (opérations, dockers, prestataires). Le présent lot doit se contenter de
**produire le bloc équipage exportable**, pour ne pas ouvrir un second chantier.

---

## 8. Écrans et permissions

| Écran | Route | Permission | Contenu |
|---|---|---|---|
Plan de relèves par navire | `/crew/rotations` | `crew:C` / `crew:M` | Bloc 1 : équipage à bord, dates dues, releveurs, statuts |
Simulation | `/crew/rotations/simulation` | `crew:M` | What-if **isolé**, aucune écriture sur le plan réel |
Réconciliation plan ↔ Marad | `/crew/rotations/ecarts` | `crew:C` | Écarts entre le plan et `marad_crew_schedules` |
Soldes de congés | `/crew/accrual` | `crew:C` · `rh:C` | Solde par marin, détail par statut, provenance des coefficients |
Génération EVP | `/crew/accrual/{period}/generate` | `rh:M` | Produit les `PayrollVariable` — **liste d'exclusion visible** |
Paramétrage matrice | `/admin/crew-parameters` | `crew:S` | Durées + coefficients, avec `provisional` et note |
Transmission PAF | `/crew/rotations/{id}/paf` | `crew:C` | Bloc 2 exportable (PDF + Excel) |

**Sur les permissions** : l'Armement a `crew:CMS` et seulement `escale:C`. Ce lot
vit donc **entièrement dans le module `crew`** — c'est cohérent avec l'organisation
réelle, et ça évite le décalage relevé au RAF R10 (le service qui décide ne pouvait
pas saisir). Le paramétrage en `crew:S` réserve la matrice à un cercle restreint,
puisqu'elle a un effet sur la paie.

**Isolation de la simulation** : motif déjà éprouvé par les scénarios de planning
(`/planning/scenarios`, qui n'écrit **jamais** sur `legs`). Le décalquer.

---

## 9. Journalisation

| Événement | `activity_logs` | Détail |
|---|---|---|
Création / modification d'une relève | ✅ | avant/après sur les dates |
**Override de durée** | ✅ | valeur, valeur par défaut écartée, **justification** |
Modification de la matrice | ✅ | ancienne et nouvelle valeur, `provisional` |
Génération d'EVP | ✅ | période, nombre de lignes, **marins exclus** |
Annulation d'une relève | ✅ | motif |

L'override et la modification de matrice sont les deux événements à effet **paie** :
ils doivent être reconstituables sans ambiguïté.

---

## 10. Tests exigés

Au-delà de la couverture usuelle, quatre familles **non négociables** :

1. **Les deux conventions de comptage ne se mélangent pas** — un test qui échoue si
   `elapsed_days_on_board` devient inclusif ou si `schengen_presence_days` devient
   exclusif. C'est le défaut le plus coûteux et le moins visible.
2. **La cascade résout dans le bon ordre**, et `source` dit la vérité : cellule
   spécifique → manning → poste → défaut codé. Test explicite du cas **PMS/élève
   vide** ⇒ retombe sur 90 j **avec `source="manning"`**, pas `"role_manning"`.
3. **Override sans justification refusé** — au niveau **base** (contrainte) et
   route, pas seulement formulaire.
4. **Reproductibilité d'audit** : recalculer une période après modification de la
   matrice doit donner le **même** résultat que le snapshot, puisque le snapshot
   porte les coefficients consommés.

Plus, conformément à la règle établie cette semaine : **tout nouveau test doit
échouer sur le code d'avant**, et aucun ne doit pouvoir passer **à vide**.

---

## 11. Séquencement et estimation

| Étape | Charge | Dépendance |
|---|---|---|
Décision d'architecture §3 (option A/B) | — | **Yasmin** |
Migration : `CrewRotationPlan`, `CrewParameter`, `CrewAccrualSnapshot` | 1 j | **fusion du lot 3 (Julien)** |
Cascade `get_crew_parameter` + écran de paramétrage | 1,5 j | ci-dessus |
Classement des jours par statut + détection d'anomalies | 1,5 j | ci-dessus |
Calcul d'acquisition + snapshot + EVP | 2 j | ci-dessus |
Écrans plan + simulation isolée | 2,5 j | ci-dessus |
Réconciliation plan ↔ Marad | 1 j | option A |
Transmission PAF (réparation + n° de vol + heures) | 1,5 j | ci-dessus |
Vocabulaire canonique + alias | 0,5 j | — |

**Total ≈ 11,5 jours.** Lot **structurant**, à effet paie : il mérite la revue de
Julien autant que le workflow BL.

**Livrable sans attendre la fusion Alembic** : le vocabulaire canonique et les alias
(0,5 j), qui ne portent aucune migration.

---

## 12. Risques et points ouverts

| # | Risque / point | Portée |
|---|---|---|
R-A | ✅ **LEVÉ 2026-08-03** — option A retenue : MyTOWT planifie et simule, Marad reste le registre, écran de réconciliation des écarts. Synchro Marad inchangée (lecture seule) | — |
R-B | ✅ **LEVÉ 2026-08-03** par les accords d'entreprise du 2024-03-22 : l'unité est le **jour travaillé**, donc un classement de chaque jour dans une position unique — l'article 10 rend cette exclusivité **obligatoire en droit**. La question inclusive/exclusive ne se pose plus | — |
R-H | ✅ **REQUALIFIÉ 2026-08-03** — « télétravail différent de détachement » (Yasmin). Le `télétravail` à 0,9 est donc **conforme** (l'accord fixe 0,9 pour tout *jour travaillé*, et télétravailler c'est travailler). Ce qui manque est un **statut** : `detache_a_terre` à **0,675 j/j**, absent du tableur comme de MyTOWT. **À créer dans la matrice** — exigible sur le fondement de l'accord, ce n'est pas une divergence d'interprétation | 🟠 à créer avec la matrice |
R-J | ⚠️ **Reprise de données à évaluer** : qui est aujourd'hui détaché à terre, et sous quel statut figure-t-il dans l'Excel ? S'il est classé `télétravail`/`embarqué` il est **surcrédité de 0,225 j/j** ; s'il est classé `débarqué` il est **débité de 1** au lieu d'être crédité de 0,675 — soit un écart de 1,675 j/j. Des soldes déjà transmis à Silae peuvent être affectés | 🟠 question aux RH |
R-I | ⚠️ **Le taux de 0,9 inclut déjà les congés payés** (3 j/mois), les repos hebdomadaires, les jours fériés et les heures supplémentaires. **Ne jamais ajouter de CP par-dessus** le résultat du grand livre — ce serait un double paiement | 🟠 discipline d'implémentation |
R-C | **Valeur PMS/élève absente** — la structure l'accueille, mais l'écran doit le signaler | 🟡 non bloquant |
R-D | **Trois registres d'embarquement** — la règle d'or de `CLAUDE.md` devient critique. Tout nouvel indicateur doit dire de quel registre il parle | 🟠 discipline permanente |
R-E | **`*` et `Db` groupés sous « obligatoire »** — modélisés séparément par précaution | 🟡 à confirmer |
R-F | **Suite Postgres-free** (RAF R5) — un lot à effet paie manipulant `Numeric` et des dates mérite des tests sur Postgres réel | 🟠 à traiter avant ce lot |
R-G | **Chevauchement congé / embarquement** — signalé comme anomalie, jamais arbitré en silence | 🟡 par conception |

> **R-F mérite une attention particulière** : `Numeric(15,6)` et les
> `DateTime(timezone=True)` se comportent différemment sous SQLite et sous
> Postgres. Trois bugs de cette famille ont déjà été rencontrés cette semaine
> (`voyage_track`, `get_by_token`, et le typage de `ensure_utc`). Sur un calcul qui
> part en paie, la suite Postgres-free n'est plus une limite acceptable.
