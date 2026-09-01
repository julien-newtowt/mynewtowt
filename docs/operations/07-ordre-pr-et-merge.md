# Ordre de création des PR et de fusion — phase 2 (2026-07-29 → septembre)

> Procédure à suivre pour sortir les lots de la phase 2 sans casser `main`.
> Rappels de politique (`CLAUDE.md`) : **aucune PR n'est créée sans demande
> explicite de Yasmin** ; une PR sort d'abord en **Draft**, puis en PR officielle
> sur seconde demande ; **jamais de merge ni d'approbation par l'assistant** ;
> jamais de force push ni de réécriture d'historique partagé.
>
> Documents liés : `docs/strategy/PLAN_UPGRADE_PHASE2_2026-08.md` (plan et RAF),
> `docs/DEVELOPMENT_JOURNAL_2026-07-27_2026-08-17.md` (journal).

---

## 0. ÉTAT AU 2026-09-01 — fait foi, supersède tout ce qui suit

> ⚠️ **Lire ce §0 avant le reste.** La file des 7 lots de la phase 2 est
> **entièrement fusionnée** : les sections suivantes, §0 bis compris, sont
> conservées comme **récit historique** et décrivent une file qui n'existe plus.

### La phase 2 est soldée

Julien a fusionné les 7 lots (PR #149, #154, #156 → #160) entre le 2026-08-18 et
le 2026-08-21, puis a mené son propre chantier sur `main` (PR #161 → #168) :
refonte du module commercial, audit et remédiation « vente à bord + caisse de
bord », reprise UX legacy en 3 phases. `main` est à `16ae84f`, **CI verte**,
**une seule tête Alembic** (`20260828_0135`), 144 migrations.

### Trois branches préparées, poussées le 2026-09-01, aucune PR ouverte

| Branche | Commit | Contenu | Suite complète |
|---|---|---|---|
| `docs/claude-md-socle-methode` | `7256c06` | `CLAUDE.md` scindé (socle de méthode gardé, cadrage daté de la période de congés retiré) + `PROJECT_CONTEXT.md` §7 | markdown seul |
| `feature/support-ticketing` | `0721e8c` | Module Assistance (support applicatif) + intégration de `main` (146 commits) | ✅ **3103** passés, 1 ignoré |
| `feature/dashboard-env-integration` | `2a2376e` | Dashboard Performance Environnementale v2 (5 pages) + suppression du legacy MRV + intégration de `main` (233 commits) | ✅ **3007** passés, 1 ignoré |

Les deux branches de code sont **poussables en fast-forward** — `main` y a été
intégré par une **fusion**, jamais par un rebase, donc aucun historique partagé
n'a été réécrit et aucun force push n'a été nécessaire.

> 🔎 Les suites ont été exécutées **dans le conteneur Linux**, pas sous Windows :
> WeasyPrint y trouve GTK/Pango, sans quoi ~19 tests de rendu PDF échouent pour
> une raison d'environnement et non de code. Sous Windows, lire un résultat rouge
> sur ces tests-là ne veut rien dire.

### ⚠️ Point d'ordonnancement Alembic — collision annoncée par la PR #169

La **PR #169** (`claude/practical-mayer-rphihj`, ouverte par Julien le 2026-09-01)
ajoute `20260901_0136_schedule_revision_actuals.py`, chaînée sur `20260828_0135`.

Or les deux migrations non publiées des branches ci-dessus sont **rechaînées sur
ce même `20260828_0135`** :

| Branche | Migration | `down_revision` actuel |
|---|---|---|
| `feature/support-ticketing` | `20260821_0119_support_ticketing.py` | `20260828_0135` |
| `feature/dashboard-env-integration` | `20260713_0106_drop_mrv_legacy_tables.py` | `20260828_0135` |

⇒ **Dès que #169 est fusionnée**, `main` a pour tête `20260901_0136` et chacune
de ces deux branches recrée **deux têtes**. C'est le motif structurel déjà
rencontré deux fois (§0 bis) ; il est désormais attrapé par la sentinelle
`tests/regression/test_alembic_single_head.py` avant la production.

**Geste correctif** : rechaîner le `down_revision` de ces deux migrations sur
`20260901_0136`. Une ligne par branche, plus la ligne `Revises:` du docstring.
**Ne pas poser de migration de fusion** : ces deux révisions ne sont pas
publiées sur `main`, et la règle du §7 de `PROJECT_CONTEXT.md` est explicite —
le rechaînage ne vaut que pour une révision non publiée, la fusion est réservée
aux révisions déjà absorbées par `main`.

### Recouvrements de fichiers avec la PR #169

Mesurés fichier par fichier le 2026-09-01 :

| Branche | Fichiers communs avec #169 |
|---|---|
| `docs/claude-md-socle-methode` | `CLAUDE.md`, `PROJECT_CONTEXT.md` — soit **ses 2 seuls fichiers** |
| `feature/support-ticketing` | `CLAUDE.md` (1 sur 22) |
| `feature/dashboard-env-integration` | `CLAUDE.md`, **`app/routers/captain_router.py`**, **`app/services/planning.py`** (3 sur 62) |

Les conflits sur `CLAUDE.md` sont additifs et mécaniques. Les deux fichiers de
code partagés avec `dashboard-env` méritent en revanche un regard : #169 touche
la séquence de planification, la branche touche `planning.py` pour retirer
`MRVEvent` du garde-fou `delete_leg`.

### Ordre de fusion recommandé

1. **PR #169** (déjà ouverte, chantier de Julien) — la laisser passer d'abord :
   elle est en cours de revue et fixe la tête Alembic.
2. **`docs/claude-md-socle-methode`** — markdown seul, aucun risque de
   régression. La fusionner tôt évite que les branches suivantes réintroduisent
   la section de consignes périmée à chaque intégration de `main`.
3. **`feature/support-ticketing`** — après rechaînage de sa migration sur
   `0136` et réintégration de `main`.
4. **`feature/dashboard-env-integration`** — en dernier, et **pas sans revue
   explicite** : elle porte un `DROP` de tables (`mrv_events`,
   `mrv_parameters`) et le décommissionnement de `dashboard_env_router`.
   Arbitrage de Yasmin du 2026-09-01 : le `DROP` est sans conséquence
   aujourd'hui, **aucune donnée MRV n'étant encore en base** — mais la décision
   d'architecture reste à confirmer par Julien.

### Ce qui reste ouvert du côté de Julien

Deux branches à lui, poussées et sans PR au 2026-09-01 :
`fix/stock-scientific-notation` (stock affiché en notation scientifique) et
`claude/user-message-au0tqk` (**ADR-014** — régularisation d'un écart de caisse
réservée au siège), que son propre journal donne « à arbitrer ».

---

## 0 bis. ÉTAT AU 2026-08-21 — récit historique, superseded par le §0

> ⚠️ **Section historique** — vraie au 2026-08-21, superseded par le §0.
> Les sections 1 à 12 sont conservées comme
> **récit historique** et contiennent des affirmations vraies à leur date, fausses
> aujourd'hui — notamment tout ce qui concerne le lot `fix/alembic-merge-heads`
> (supprimé le 2026-08-10) et la ligne « aucun lot ne porte de migration »
> (**fausse** : le lot BL en porte cinq).

### La validation a commencé

| | |
|---|---|
| **PR #149** (`chore/ci-integration-tests`) | ✅ **approuvée et FUSIONNÉE** par Julien le **2026-08-18** |
| **PR #154** (`docs/decouverte-fonctionnelle`) | 🟢 **sortie du brouillon par Julien** le 2026-08-19 — c'est celle en cours |

⇒ Le filet complet (198 fichiers de test) est **actif sur `main`**. Toutes les PR
suivantes sont désormais vérifiées par la suite entière, plus par `tests/unit` seul.

### Ordre à suivre — par le numéro du titre, ascendant

**Consigne transmise à Julien** : suivre le premier nombre du titre des PR.

| Titre | PR | Branche | Position réelle |
|---|---|---|---|
| `[1/7]` | #149 | `chore/ci-integration-tests` | 1 — ✅ fusionnée |
| `[2/7]` | #154 | `docs/decouverte-fonctionnelle` | 2 |
| `[4/7]` | #156 | `feat/ops-quickwins` | 3 |
| `[5/7]` | #157 | `fix/crew-indicators-honest` | 4 |
| `[6/7]` | #158 | `feat/bl-workflow` | 5 |
| `[7/7]` | #159 | `feat/crew-rotations` | 6 |

> 🕳️ **Il n'existe pas de `[3/7]`.** C'était le lot de fusion Alembic, résolu
> **sur `main`** le 2026-08-07 (`20260807_0113`) et supprimé le 2026-08-10, PR #155
> fermée. **C'est un trou dans la numérotation, pas une étape manquante** — ne pas
> l'attendre ni la chercher.
>
> Le schéma `[n/7]` est conservé **volontairement** malgré ses 6 lots : renuméroter
> cinq PR ouvertes en pleine relecture serait plus risqué que de vivre avec le trou,
> d'autant que la consigne reçue par le valideur porte sur ces numéros.

### État vérifié des quatre PR de code — 2026-08-21

Les quatre ont eu `main` intégré (**`behind=0`**) et leur CI **repassée après**.

| PR | Commit de fusion de `main` | Conflits | mypy | Suite complète (CI Ubuntu) |
|---|---|---|---|---|
| #156 | `cdcc64b8` | **2**, documentaires | 371/371 | ✅ **2015** passés, 1 ignoré |
| #157 | `8d322f4c` | 0 | 371/371 | ✅ **2023** passés, 1 ignoré |
| #158 | `4cef7bec` | 0 | 371/371 | ✅ **2247** passés, 1 ignoré |
| #159 | `6598f780` | 0 | 371/371 | ✅ **2071** passés, 1 ignoré |

**#154 n'a délibérément pas été touchée** : Julien la relit. Documentation seule,
`MERGEABLE`/`CLEAN` — son retard sur `main` est sans conséquence.

Les quatre restent **en brouillon** : la sortie de brouillon appartient à Yasmin.

### ⚠️ Corrections d'affirmations périmées

| Affirmation ailleurs dans ce document | Réalité au 2026-08-21 |
|---|---|
| « aucun lot ne porte de migration » | **FAUX** — #158 en porte **5** (`20260814_0114` → `20260817_0118`), chaînées sur `20260807_0113`, **tête unique** `20260817_0118` vérifiée |
| `fix/alembic-merge-heads` « prête, en attente de validation manager » | Branche **supprimée**, PR **#155 fermée**. Le problème est résolu sur `main` |
| `feature/qhse-foundation` « deux défauts qui détruisent des données » | **Corrigés** le 2026-08-17 (`188be0e`) — cf. ci-dessous |
| « Rien n'a été fusionné sur `main` » | **FAUX** depuis le 2026-08-18 (#149) |

### 🔗 Point d'ordonnancement Alembic — ✅ traité le 2026-08-26 (fusion `20260826_0119`)

**#158 et `feature/qhse-foundation` chaînent sur le même parent `20260807_0113`.**
Ce sont des **frères, pas une file** : `main` absorbe **l'un** des deux sans rien
faire, mais le **second** arrivera avec un parent qui n'est plus la tête et
**recréera deux têtes Alembic** — la panne exacte de juillet.

⇒ **Action, une fois #158 fusionnée** : rechaîner `20260722_0106_qhse_foundation.py`
sur la nouvelle tête (`20260817_0118`), ou poser une révision de fusion.
**C'est QHSE qui se rechaîne**, étant hors file.

Volontairement non pré-résolu : chaîner QHSE sur les migrations de BL le rendrait
infusionnable sans BL, ce qui violerait le §1 (« un lot = révocable indépendamment »).

#### Ce qui s'est réellement passé, et le correctif retenu

L'action n'a pas été faite à temps : #160 (QHSE) a été fusionnée le 2026-08-26 avec
`20260807_0113` pour parent, et le déploiement de `96a5c70` a échoué exactement comme
annoncé (`Multiple head revisions are present`, têtes `20260722_0106` et
`20260817_0118`) — snapshot restauré automatiquement, aucune perte.

**Correctif : la révision de fusion, pas le rechaînage** —
`migrations/versions/20260826_0119_merge_heads_bl_qhse.py`, sans aucun DDL
(`down_revision = ("20260817_0118", "20260722_0106")`). Le rechaînage était encore
la bonne option **tant que QHSE n'était pas publiée** ; il ne l'est plus une fois la
révision sur `main` : réécrire l'ascendance de `20260722_0106` ferait considérer
`20260814_0114` → `20260817_0118` comme **déjà appliquées** sur toute base qui porte
déjà QHSE (poste de dev, staging), dont les tables BL manqueraient **sans erreur**.
La fusion, elle, est correcte quel que soit l'état de la base. Les deux chaînes sont
**disjointes** — QHSE ne crée que des tables neuves (`qhse_*`, `deficiency_codes`),
BL ne touche que `packing_list_batches` / `bl_*` — donc l'ordre d'application entre
elles est indifférent.

**Filet pour la prochaine fois** : la panne s'est produite **deux fois** (07/08 puis
26/08) et à chaque fois en déploiement, jamais en CI — qui ne regardait pas le graphe
de migrations. La sentinelle `tests/regression/test_alembic_single_head.py` échoue
désormais en PR dès qu'une seconde tête apparaît (et dès qu'un `down_revision`
devient orphelin), sans connexion à la base.

### `feature/qhse-foundation` — hors file, plus bloquée par des défauts

| | |
|---|---|
| **Statut** | `behind=0`, 7 commits ahead, **aucune PR** |
| **Les deux défauts destructeurs** | ✅ **corrigés** (`188be0e`, 2026-08-17) : point de reprise par ligne (`begin_nested`) au lieu du `rollback()` global, et les non-conformités suspectes sont **importées et marquées** au lieu d'être supprimées |
| **Migration** | ✅ rechaînée sur `20260807_0113` (`b417227b`) — produisait **deux têtes** sinon |
| **Deux régressions trouvées à l'intégration** | ✅ corrigées : un **faux placeholder i18n** (`7e98e08d`) et une **fuite de permissions** — 3 règles de scope `qhse` administrables depuis `/mrv/parametres` sur droit `mrv:S` (`be7b553a`) |
| **⚠️ Couverture CI** | **AUCUNE.** Le workflow ne se déclenche que sur `pull_request` et sur push vers `main` ; sans PR, cette branche n'est jamais vérifiée par la CI. Validation locale uniquement : 2025 passés, 1 ignoré, 15 échecs — les 15 étant tous l'artefact WeasyPrint/GTK sous Windows |
| **Reste ouvert** | Le moteur de règles n'est **jamais appelé** pour le QHSE : RQ01-RQ03 sont seedées mais aucun code ne les exécute. C'est une fonctionnalité à finir, pas un défaut à corriger |

### Toujours ouvert

- 🟠 **Protection de branche absente sur `main`** (RAF R3) — vérifié le 2026-08-21,
  l'API répond 404. Avec plusieurs fusions à la suite et aucun garde-fou, la règle
  « rejouer la suite complète après **chaque** fusion » n'a aucun filet de
  rattrapage. **Julien s'en occupe** (Yasmin n'est pas admin du dépôt).
- 🟡 **Marge nulle sur le cliquet mypy** : les quatre PR arrivent à **371 = plafond
  exactement**. La prochaine branche qui ajoute **une seule** erreur de typage fera
  échouer son lint. Ce n'est pas un défaut de ces lots — la marge vient de `main`.

---

## 1. Principe directeur

**Un lot = une branche = une PR = un objectif.** Chaque lot doit être
**mergeable et révocable indépendamment** : si un lot pose problème après
fusion, on doit pouvoir le retirer sans emporter les autres.

Corollaire pratique : **ne jamais empiler un lot sur un lot non mergé** sans
raison. Si c'est inévitable (le lot B a besoin du lot A pour être validé), le
signaler dans la PR et fusionner dans l'ordre.

---

## 1 bis. 🔴 Contrainte structurante — un seul valideur, absent jusqu'au 17 août

**Julien est la seule personne compétente pour valider une fusion vers `main`**
(confirmé par Yasmin le 2026-08-03). Ce n'est pas une question de droits GitHub :
c'est la seule personne en mesure de juger techniquement un merge. Il est en
congés **jusqu'au 2026-08-17**.

**Conséquence : rien ne part sur `main` avant son retour.** Y compris la fusion
Alembic, qui débloque pourtant tout lot portant une migration.

### Stratégie retenue : tout préparer, ne rien fusionner

Attendre passivement coûterait **2,5 semaines** sur une fenêtre qui se termine en
septembre. On empile donc **délibérément**, en assumant l'exception au principe
directeur ci-dessus :

```
main
 └── fix/alembic-merge-heads          ← lot 3, tête unique Alembic (à valider en 1er)
      ├── lot workflow BL             ← porte une migration ⇒ dérive du lot 3
      ├── lot relèves d'équipage      ← portera une migration ⇒ dérive du lot 3
      └── lot J9 horodatage           ← porte une migration ⇒ dérive du lot 3
```

**Pourquoi c'est acceptable ici** : `alembic revision` exige une tête unique. Un
lot portant une migration et branché sur `main` produirait une révision rattachée
à l'une des deux têtes divergentes — donc une migration **à refaire** après la
fusion. Brancher sur le lot 3 évite ce travail perdu.

**Ce que ça impose, sans exception** :

1. **Chaque PR de lot dérivé mentionne explicitement sa dépendance** au lot 3 en
   tête de description, avec la mention « ne pas fusionner avant le lot 3 ».
2. **L'ordre de fusion du §2 devient impératif**, pas indicatif : fusionner un
   lot dérivé avant le lot 3 emporterait la migration de fusion avec lui et
   brouillerait l'historique de schéma.
3. **Après la fusion du lot 3**, chaque lot dérivé est rebasé sur `main` avant sa
   propre fusion (cf. §3.3), puis la suite complète est rejouée.
4. Les lots **sans migration** continuent de partir de `main` directement et
   restent indépendants — on n'empile que ce qui doit l'être.

### Séquence prévue au retour de Julien (2026-08-17)

| Ordre | Lot | Action |
|---|---|---|
| 1 | `chore/ci-integration-tests` (PR #149) | Sortir du brouillon → relecture → fusion. **Le filet d'abord**, il valide tous les suivants |
| 2 | `docs/decouverte-fonctionnelle` | Doc seule, parallélisable |
| 3 | `fix/alembic-merge-heads` | ⚠️ **Validation Julien indispensable** — touche l'historique de schéma. Débloque 4, 6, 7, 8 |
| 4 | `feat/ops-quickwins` | Sans migration, peut passer avant le 3 |
| 5 | `fix/crew-indicators-honest` | Dérive du lot 1 ⇒ après lui |
| 6 | Lot workflow BL | Rebase sur `main` après le 3, puis fusion |
| 7 | Lot relèves d'équipage | Idem — **et** en attente des réponses de l'Armement |
| 8 | Lot J9 horodatage | Idem |

> ⚠️ **À ne pas oublier au retour** : rejouer la suite **complète** après *chaque*
> fusion (§3.3), et non une seule fois à la fin. Huit fusions d'affilée sans
> revérification, c'est exactement la façon de casser `main` — l'incident que la
> protection de branche absente (RAF R3) ne rattraperait pas.

---

## 2. Ordre recommandé

| # | Lot / branche | Contenu | Pourquoi à cette place | Risque |
|---|---|---|---|---|
| **1** | `chore/ci-integration-tests` — **PR [#149](https://github.com/julien-newtowt/mynewtowt/pull/149) ouverte (brouillon)** | `voyage_track` + `planning.ensure_utc` (typage), `ci.yml` (exécution `integration`+`regression` + libs WeasyPrint + gitleaks réparé + cliquet mypy), 10 fichiers de tests périmés, `CLAUDE.md` (invariants), journal, plan | **Le filet d'abord.** Une fois mergé, **toutes** les PR suivantes sont vérifiées par la suite complète (198 fichiers au lieu de 84). ✅ **CI exécutée le 2026-07-30 : 2015 passés · 1 ignoré** — les 15 échecs locaux étaient bien un artefact WeasyPrint sous Windows | 🟢 Faible — 2 fichiers applicatifs, dont un purement statique (`@overload`) |
| **2** | `docs/decouverte-fonctionnelle` | `PROJECT_CONTEXT.md` (§1-14 : architecture, workflows, audits, correction §7), `CLAUDE.md` (instructions temporaires), guide fonctionnel | **Documentation seule, zéro code.** Peut partir en parallèle du lot 1. Porte `PROJECT_CONTEXT.md`, que tous les autres documents référencent | 🟢 Nul — aucun code |
| **3** | `fix/alembic-merge-heads` — **prête, en attente de validation manager** | Migration **de fusion pure** `20260730_0113_merge_heads.py` (aucun DDL) raccordant les deux `head` divergents (`20260716_0112` MRV / `20260720_0107` rapports générés) | 🔴 **Bloquant pour tout lot ultérieur portant une migration**, et pour tout déploiement (la production utilise Alembic exclusivement). Voir RAF R1. ✅ Vérifié : **une seule tête**, `(mergepoint)` dans l'historique, `upgrade()`/`downgrade()` exécutables. Les deux chaînes touchent des tables **disjointes** ⇒ ordre d'application indifférent | 🟠 Modéré — historique de schéma. **Validation manager requise** |
| **4** | `feat/ops-quickwins` (J2) | Alerte ETA en mer, nom client + `leg_code`, heures voile ×6, rail documentaire unique | Répond à 3 demandes Opérations + 1 bug de document client. **Aucune migration** ⇒ peut passer avant le lot 3 si besoin | 🟢 Faible |
| **5** | Lot **workflow BL** | Draft → validation client → signature commandant → BL final, avec journalisation (voir `docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md`) | **Nécessite une migration** ⇒ **dépend du lot 3**. Ferme l'exposition juridique du registre BL | 🟠 Modéré |
| **6** | `fix/crew-indicators-honest` (J3) | Double comptage des jours en mer corrigé (union d'ensembles de jours) · statut Schengen `indetermine` au lieu d'un « conforme » sans données · invariant « deux registres » dans `CLAUDE.md` | **Aucune migration.** Branchée **sur le lot 1** (ses éditions de `CLAUDE.md`, du journal et du plan s'appuient sur du contenu créé par le lot 1) ⇒ à fusionner **après** lui. Périmètre réduit après recueil du processus réel : le garde-fou passeport est **abandonné**, il aurait contraint l'agent d'escale qui ne décide pas les embarquements | 🟢 Faible — 2 services, 3 templates, 8 tests ajoutés dont 4 échouant sur l'ancien code |
| **7** | Lot **relèves d'équipage** (à créer) | Simulation + décision des relèves + transmission PAF + note d'escale | ⏸️ **Bloqué en attente des fichiers Excel** (référence métier). C'est le vrai manque fonctionnel (RAF R11) : le processus vit aujourd'hui entièrement hors du logiciel | à évaluer |
| **8** | Lots suivants (J9 horodatage…) | cf. plan | J9 porte une migration (`atd < ata`) ⇒ dépend aussi du lot 3 | selon lot |

### Lots existants à NE PAS fusionner en l'état

| Branche | Raison |
|---|---|
| `feature/qhse-foundation` | 🔴 Contient deux défauts qui **détruisent des données** : un filtre par mot-clé (`test\|essai\|demo`) qui quarantaine et n'importe jamais des non-conformités ISM légitimes sans persister la perte, et un `rollback()` dans la boucle d'import qui annule les lignes déjà insérées tout en les comptant comme importées. Correctif de quelques heures, à faire **avant** tout merge. Voir `PROJECT_CONTEXT.md` §14.7.<br>ℹ️ Précisions du 2026-07-30 : **39 commits behind, 2 ahead**. Ses 2 commits ne touchent **que** les fichiers QHSE + i18n + `permissions.py` + `main.py` + `validation_engine.py` — **aucun recouvrement** avec les autres lots de la phase 2, et une fusion **ne supprimerait pas** le trombinoscope (vérifié par fusion à blanc). Le motif de blocage reste entier : ce sont les deux défauts de code, pas la divergence |
| `feature/dashboard-env-integration`, `scratch/preintegration-rehearsal` | Divergentes de `main` (ahead/behind important). À arbitrer séparément, hors phase 2 |

### Branche supprimée le 2026-08-10 — `fix/alembic-merge-heads`

| | |
|---|---|
| **SHA local** | `23ebf59` (avec `main` intégré) |
| **SHA sur `origin`** | `2313448` |
| **PR associée** | **#155, fermée** |

**Motif** : le problème qu'elle corrigeait a été résolu **en parallèle sur `main`**
le 2026-08-07, par `20260807_0113_merge_heads_mrv_crewing` — qui déclare
**exactement les mêmes parents** (`20260716_0112`, `20260720_0107`).

**Vérifié avant suppression** : les deux migrations ensemble produisaient **deux
têtes Alembic**, soit précisément la panne qu'elle devait éliminer. La fusionner
l'aurait recréée.

⚠️ **Son unique contenu propre — `migrations/versions/20260730_0113_merge_heads.py` —
ne subsistait nulle part ailleurs** (vérifié sur les six lots et sur `main`). Sa
suppression le retire donc du dépôt. C'est assumé : 46 lignes de *no-op*, rendues
redondantes, et récupérables via la référence conservée par la PR #155 fermée.

**Conséquence sur l'ordre de fusion** : le préalable de migration **disparaît**.
Aucun lot ne dépend plus d'une fusion Alembic — `main` la porte déjà.

### Ordre de fusion en vigueur au 2026-08-10

```
1. chore/ci-integration-tests    (#149)  ← le filet d'abord
2. docs/decouverte-fonctionnelle (#154)  ← indépendant
3. feat/ops-quickwins            (#156)  ← indépendant
4. fix/crew-indicators-honest    (#157)  ← porte le contenu du lot 1
5. feat/bl-workflow              (#158)  ← porte le contenu du lot 1
6. feat/crew-rotations           (#159)  ← porte le contenu des lots 1 et 4
```

**Les trois premiers sont mutuellement indépendants** : leur ordre relatif est libre.
Les trois suivants portent le contenu du lot 1 — l'ordre y reste recommandé non plus
pour une raison technique, mais pour **conserver la révocabilité lot par lot**.

> 🧭 **Leçon de la journée du 2026-08-10** : l'ordre de fusion publié le 2026-08-03
> avait été construit sur un `main` vieux d'une semaine, sans revalidation. Résultat :
> un lot devenu nuisible, un ordre périmé, trois PR en échec et une remise à niveau
> complète des six branches. **Revalider `main` AVANT de publier un ordre de fusion.**

### Branches supprimées le 2026-07-30 (avec accord de Yasmin)

Empreintes conservées ici : une branche supprimée reste récupérable
(`git reflog`, ou `git branch <nom> <sha>`) tant que le ramasse-miettes n'est
pas passé.

| Branche | SHA au moment de la suppression | Vérification faite avant |
|---|---|---|
| `feature/crewing-monthly-yearbook` | `e936675` | `git cherry main <branche>` ⇒ **0 commit absent de `main`** (fusionnée par la PR #148). Supprimée en local **et** sur `origin` |
| `feature/mrv-gaps-remediation` | `14e42f9` | **0 commit absent de `main`**, y compris pour la version `origin` qui divergeait de la copie locale (fusionnée par la PR #147). Supprimée en local **et** sur `origin` |
| `backup/ci-lot-avant-rebase` | `3ebb5f1` | Local uniquement. Filet posé avant le rebase du lot 1 ; le rebase a réussi et le lot 1 est poussé + couvert par la PR #149 |

**Sur le backup, la vérification méritait d'être poussée** — et elle a servi.
`git cherry` signalait **4 commits sans équivalent** dans le lot 1. Trois étaient
les commits de découverte, retrouvés dans `docs/decouverte-fonctionnelle`. Le
quatrième, `2c4c757`, n'avait d'équivalent **nulle part** : c'est celui qui avait
provoqué le conflit de rebase, et il avait été **scindé** en deux (le journal
vers le lot 1, la correction de `PROJECT_CONTEXT.md` §7 vers le lot découverte
sous `e48847d`). Un commit scindé ne peut correspondre à aucune empreinte.

Contrôle de contenu ligne à ligne : sur 69 lignes ajoutées au journal par
`2c4c757`, toutes survivent sauf celles **réécrites** par la résolution du
conflit (l'item « §7 à corriger » est devenu « ✅ fait, commit `e48847d` »). Les
faits substantiels sont tous présents : les deux identifiants de tête Alembic,
les deux noms de migration, la note « fusion à valider ».

> 🧭 **Leçon réutilisable** : `git cherry` ne détecte pas les commits **scindés**
> lors d'un rebase — leur empreinte ne correspond plus. Avant toute suppression
> de branche de sauvegarde, vérifier le **contenu**, pas seulement les
> empreintes.

> ⚠️ **Méthode** : évaluer un recouvrement avec `git diff main..branche` est
> **faux** pour toute branche en retard — cela remonte ce que `main` a fait
> évoluer, pas ce que la branche modifie. Toujours partir de
> `git merge-base`, puis confirmer par une fusion à blanc
> (`git merge-tree`). Cf. `PLAN_UPGRADE_PHASE2_2026-08.md` §11.

---

## 2 bis. État des branches — relevé du 2026-07-30

Inventaire factuel de **toutes** les branches locales. « Ahead » et « behind » sont
mesurés depuis la **base commune** avec `main`, jamais par `main..branche`
(cf. avertissement de méthode ci-dessus).

### Lots de la phase 2

| Branche | Ahead | Behind | GitHub | Base | Lot | Action attendue |
|---|---|---|---|---|---|---|
| `chore/ci-integration-tests` | 23 | 0 | ✅ à jour · **PR #149 (brouillon)** | `main` | **1** | Sortir du brouillon, faire relire, fusionner |
| `docs/decouverte-fonctionnelle` | 4 | 0 | ✅ à jour | `main` | **2** | PR à créer |
| `fix/alembic-merge-heads` | 1 | 0 | ✅ à jour | `main` | **3** | **Validation manager** (historique de schéma), puis PR |
| `feat/ops-quickwins` | 21 | 0 | ✅ à jour | `main` | **4** | PR à créer |
| `fix/crew-indicators-honest` | 27 | 0 | ✅ à jour | `main` | **6** | PR à créer **après** fusion du lot 1 (dont elle contient les 23 commits) |

**Au 2026-07-30, les 5 lots de la phase 2 sont tous sauvegardés sur `origin`.**
Plus aucun travail n'existe uniquement en local — 26 commits l'étaient encore le
matin (`feat/ops-quickwins` 21, `docs/decouverte-fonctionnelle` 4,
`fix/alembic-merge-heads` 1).

> Les 27 commits de `fix/crew-indicators-honest` = les 23 du lot 1 + ses 4 propres.
> C'est voulu : ses éditions de `CLAUDE.md`, du journal et du plan s'appuient sur
> du contenu créé par le lot 1. Sa PR n'affichera ses 4 commits qu'une fois le
> lot 1 fusionné.

### Hors phase 2

| Branche | Ahead | Behind | GitHub | Statut |
|---|---|---|---|---|
| `feature/qhse-foundation` | 2 | 39 | ⚠️ diverge d'`origin` | 🔴 Ne pas fusionner : deux défauts détruisant des données (cf. tableau ci-dessus) |
| `feature/dashboard-env-integration` | 16 | 39 | ⚠️ diverge d'`origin` | À arbitrer hors phase 2 |
| `scratch/preintegration-rehearsal` | 18 | 18 | ⚠️ **non poussée** | Répétition d'intégration. **Seul travail restant en local uniquement.** À arbitrer hors phase 2 |

Trois branches ont été **supprimées** le 2026-07-30 (cf. section dédiée
ci-dessous) : les deux déjà fusionnées et le filet de rebase devenu sans objet.

> ℹ️ `origin` porte aussi une dizaine de branches `claude/*` (archives de sessions
> antérieures) et `fix/git-stabilization`, sans équivalent local. Hors périmètre
> de la phase 2 ; à trier séparément.

### Ordre de fusion conseillé — version courte

```
1. chore/ci-integration-tests     (PR #149)  ← le filet d'abord
2. docs/decouverte-fonctionnelle              ← doc seule, parallélisable avec 1
3. fix/alembic-merge-heads                    ← débloque toute migration + le déploiement
4. feat/ops-quickwins                         ← sans migration, peut passer avant 3
5. fix/crew-indicators-honest                 ← après 1 (elle en dérive)
6. lot workflow BL                            ← après 3 (porte une migration)
7. lot relèves d'équipage                     ← après réception des Excel
8. J9 horodatage…                             ← après 3 (porte une migration)
```

**Deux règles qui ne se négocient pas** : rejouer la suite **complète** après
chaque fusion (§3.3), et ne jamais fusionner `fix/alembic-merge-heads` sans
validation manager. Tout lot portant une migration attend le lot 3.

### Sauvegardes hors dépôt

| Élément | État |
|---|---|
| Tag `pre-upgrade-2026-08` | ✅ poussé sur `origin` |
| `pg_dump` de la base de dev | ✅ hors dépôt, **procédure de restauration testée** (135 tables, comptages vérifiés) |

---

## 3. Mécanique, PR par PR

### 3.1 Avant de proposer une PR — obligatoire

1. **Quality Gate** (`PLAN_UPGRADE_PHASE2_2026-08.md` §10) : compilation, `ruff`,
   `black`, suite **unit + integration + regression**, absence de régression,
   documentation à jour, cohérence des migrations (`upgrade` **et** `downgrade`
   exécutés), `bandit`, `pip-audit`, `gitleaks`, aucun secret, aucun fichier
   temporaire, pas de dégradation de performance.
2. **Audit de compatibilité** (§11) : divergence vs `main`, conflits potentiels,
   impact par couche, niveau de risque unique 🟢/🟡/🟠/🔴 justifié, dette
   introduite, recommandations.
3. **Présenter les deux à Yasmin** et **attendre sa décision**. Aucune PR n'est
   créée avant.

### 3.2 Créer la PR

```bash
git push -u origin <branche>          # 1re publication de la branche
gh pr create --draft --base main --head <branche> \
  --title "<type>: <objet>" --body-file <rapport>
```

- Toujours **`--draft`** en premier. Conversion en PR officielle **seulement sur
  seconde demande explicite**.
- Le corps de la PR reprend le rapport de Quality Gate + l'audit de
  compatibilité, et suit `.github/PULL_REQUEST_TEMPLATE.md`.
- Lancer `/security-review` avant tout merge sur `main` (convention `CLAUDE.md`).

### 3.3 Après la fusion d'une PR — remettre les lots suivants à niveau

C'est l'étape la plus souvent oubliée. Dès qu'un lot est mergé dans `main` :

```bash
git checkout main && git pull            # recaler main
git checkout <lot-suivant>
git rebase main                          # ou: git merge main
# rejouer la suite complete AVANT de considerer le lot encore valide
python -m pytest tests/unit tests/integration tests/regression -q
```

**Un lot validé avant la fusion d'un autre lot n'est plus validé après.** La
suite doit être rejouée, pas supposée verte.

### 3.4 Ce que l'assistant ne fait jamais

Fusionner · approuver une PR · faire un force push · réécrire un historique déjà
poussé · supprimer une branche sans accord · travailler directement sur `main`.

---

## 4. Points de contrôle transverses

- **Protection de branche sur `main`** : absente à ce jour, et Yasmin n'est pas
  admin du dépôt (RAF R3). Un incident de merge direct a déjà cassé `main` par
  le passé. **À escalader auprès de la personne admin** — d'autant plus que
  cette période produit beaucoup de commits.
- **Point de retour** : tag annoté `pre-upgrade-2026-08` (sur `3b2a54e`) +
  `pg_dump` hors dépôt, procédure de restauration validée le 2026-07-29
  (135 tables, comptages vérifiés). Le tag est **local** — le pousser avant la
  première PR.
- **Branche de secours** : `backup/ci-lot-avant-rebase` conservée jusqu'à la
  fusion du lot 1, puis supprimable.
- **Revue par le manager** : les modifications mineures peuvent être validées
  par Yasmin ; les changements d'architecture doivent rester en attente de son
  retour (2026-08-17) autant que possible. Sont concernés : la fusion Alembic
  (lot 3), le workflow BL (lot 5), et tout lot touchant `Booking.status` ou la
  chaîne MRV.
