# État du chantier — note de reprise pour Julien

> **Rédigé le 2026-08-03** par Yasmin (assistée). À lire au retour de congés.
>
> **Rien n'a été fusionné sur `main`.** Tu es la seule personne pouvant valider une
> fusion : sept branches attendent, prêtes et testées, dans un ordre précis.
>
> Documents de référence : `07-ordre-pr-et-merge.md` (ordre et mécanique),
> `DEVELOPMENT_JOURNAL_2026-07-27_2026-08-17.md` (journal détaillé, jour par jour),
> `PLAN_UPGRADE_PHASE2_2026-08.md` (plan et RAF).

---

## 1. En trois phrases

Sur la période du 27/07 au 03/08, le filet de tests a été réparé et **a
immédiatement trouvé des bugs réels** ; plusieurs indicateurs qui affirmaient des
choses fausses ont été corrigés ; et deux lots métier (workflow BL, relèves
d'équipage) ont été spécifiés à partir des processus réels recueillis auprès de
l'Armement.

**Un seul point te concerne en priorité** : la fusion Alembic (lot 3), qui débloque
tout ce qui porte une migration.

**Aucune décision irréversible n'a été prise sans toi.**

---

## 2. 🔴 Ce qu'il faut décider en premier

### La fusion des deux `head` Alembic

`alembic upgrade head` **échouait sur `main`** : deux chaînes de migration avaient
divergé sans jamais être rebasées (`20260716_0112` MRV / `20260720_0107` rapports
générés). Conséquence, la production utilisant Alembic exclusivement : **tout
déploiement était bloqué**, et toute nouvelle migration exigeait de préciser sa
tête cible.

Correctif proposé : branche **`fix/alembic-merge-heads`**, une migration de
**fusion pure** — `20260730_0113_merge_heads.py`, **aucun DDL**, `upgrade()` et
`downgrade()` vides. Son seul rôle est de raccorder les deux chaînes.

Pourquoi c'est sûr : les deux chaînes touchent des tables **disjointes**
(`nav_event_noon` d'un côté, `generated_reports` de l'autre), leur ordre
d'application relatif est donc indifférent.

Vérifié : `alembic heads` renvoie **une seule tête**, `alembic history` affiche le
`(mergepoint)`, le fichier est importable et ses deux fonctions exécutables.

**C'est le point bloquant du chantier** : les lots workflow BL, relèves d'équipage
et J9 en dépendent tous.

---

## 3. Ordre de fusion — impératif, pas indicatif

```
1. chore/ci-integration-tests    (PR #149, brouillon)  ← le filet d'abord
2. docs/decouverte-fonctionnelle                       ← doc seule, parallélisable
3. fix/alembic-merge-heads                             ← 🔴 TA VALIDATION
4. feat/ops-quickwins                                  ← sans migration
5. fix/crew-indicators-honest                          ← dérive du lot 1
6. feat/bl-workflow                                    ← dérive de 1 + 3
7. feat/crew-rotations                                 ← dérive de 5 + 3
```

⚠️ **Deux règles à ne pas contourner** :

1. **Rejouer la suite complète après *chaque* fusion**, pas une seule fois à la
   fin. Sept fusions d'affilée sans revérification est la façon la plus sûre de
   casser `main` — d'autant que la **protection de branche est absente** (RAF R3,
   à mettre en place : Yasmin n'a pas les droits).
2. **Les lots 6 et 7 empilent délibérément** les lots dont ils dérivent. Ce n'est
   pas un accident : `alembic revision` exige une tête unique, et une migration
   créée sur `main` aurait été rattachée à l'une des deux têtes divergentes, donc
   **à refaire**. Leur PR affichera les commits de leurs parents jusqu'à ce que
   ceux-ci soient fusionnés.

---

## 4. Ce que le filet de tests a trouvé — l'argument principal

La CI ne lançait que `tests/unit` (84 fichiers). **`tests/integration` (110
fichiers) et `tests/regression` (4 fichiers) n'étaient jamais exécutés.** 29 échecs
y vivaient invisibles.

En les activant, **trois défauts réels** sont apparus, plus **quatre gardes qui
affichaient vert sans rien garder** :

| Ce qui a été trouvé | Nature |
|---|---|
`voyage_track.leg_window` comparait des dates naïves et aware | **bug latent**, pas limité aux tests : la convention du projet prévoit des saisies naïves |
Une régression introduite pendant la correction de ce bug | détectée **uniquement** par un re-run complet |
Le double comptage des jours en mer | **bug réel affiché à l'écran** (cf. §5) |
Suites `integration` + `regression` jamais exécutées | garde décoratif |
Mypy annoncé « baseline 142 » — **434 en réalité** | dérive de 3× masquée par `continue-on-error` |
Gitleaks **n'a jamais scanné une seule PR** | `GITHUB_TOKEN` devenu obligatoire ; l'échec était avalé |
Gitleaks réparé mais **clone superficiel** ⇒ rapport vide | « 0 détection » signifiait « rien n'a été scanné » |

Corrections apportées à la CI : suites activées, libs système WeasyPrint,
**cliquet bloquant anti-dérive du typage** (plafond posé à la valeur réelle, toute
erreur *nouvelle* fait échouer la CI), gitleaks réparé avec `fetch-depth: 0` **et**
une étape bloquante qui exige la **preuve** qu'un scan a eu lieu.

✅ **Résultat du premier run réel : 2015 passés, 1 ignoré.** Et **le dépôt est
propre** — 22 commits scannés, aucun secret. C'était la première fois que ce dépôt
était réellement scanné.

> 🧭 **Règle retenue de tout ça** : *un contrôle qu'on ne fait pas échouer
> volontairement au moins une fois n'est pas un contrôle.* Elle est inscrite au
> Quality Gate (§10 du plan) et appliquée depuis : chaque nouveau test doit
> échouer sur le code d'avant, et aucun ne doit pouvoir passer **à vide**.

---

## 5. Corrections métier livrées

### Le compteur de jours en mer doublait

`embarked_days_by_member` **additionnait** les jours de deux registres qui
décrivent parfois la même période — les relèves importées de Marad et les
embarquements saisis en escale. Sa docstring supposait explicitement le second
vide, ce qui est faux.

⇒ Dès qu'une escale était saisie pour un embarquement déjà connu de Marad, **les
jours en mer du marin doublaient**. Affiché sur `/crew`.

Reconstruit sur une **union d'ensembles de jours calendaires**. Prouvé par des
tests qui donnent 20 au lieu de 10 sur l'ancien code, et 21 au lieu de 15 en
recouvrement partiel.

**C'est le chiffre dont dépendra la planification des relèves** — un planificateur
qui double-compte serait pire qu'Excel.

### Le statut Schengen affirmait « conforme » sans rien savoir

Le statut retombait sur `compliant` dès que le décompte tombait à zéro — y compris
quand des embarquements existaient **hors de portée du calcul** (plannings Marad,
ou affectation sans voyage). Deux chemins menaient à une affirmation de conformité
sans aucune donnée.

Nouveau statut **`indetermine`**, affiché « Non calculé — voir Marad ».

Trois décisions à connaître :
- un **dépassement établi prime** sur l'incertitude ;
- `indetermine` **n'est pas une alerte** (c'est une absence d'information, et Marad
  notifie déjà l'Armement en amont) ;
- un marin sans embarquement **nulle part** reste `compliant` — là, zéro est vrai.

⚠️ **Piège évité** : les trois templates avaient un `{% else %}` affichant
« Non-compliant ». Un nouveau statut y serait apparu comme une **alerte** — on
aurait remplacé une fausse réassurance par une fausse alarme.

### Traçabilité du portail expéditeur

Les 8 routes mutantes de `/p/{token}` n'écrivaient **rien** dans `activity_logs`,
le journal consulté depuis `/admin/activity-logs`. Deux d'entre elles — la
soumission de la packing list et l'envoi d'un message — n'étaient tracées **nulle
part**.

C'est pourtant cette piste qu'un P&I club réclame à l'ouverture d'un dossier.
Corrigé, sans jamais journaliser le token (vérifié par analyse de l'arbre
syntaxique, pas par recherche textuelle).

### Notify party absent du portail

Les cinq colonnes `notify_*` existaient en base **et étaient déjà auditées**, mais
n'étaient exposées que dans le formulaire staff. **Tout BL issu d'une packing list
remplie par un expéditeur sortait sans notify party**, sans que rien ne le signale.
Exposées, dans les 5 langues.

### Vocabulaire des postes unifié

Les classeurs de l'Armement emploient **quatre** vocabulaires pour les mêmes
fonctions (`Chief Engineer` / `CHENG` / `Chef Mécanicien` / `CE*`). Table d'alias
étendue, avec lecture des marqueurs `*` (poste obligatoire) et `Db` (doublure).

---

## 6. Deux lots spécifiés, prêts à coder

### Workflow BL — `feat/bl-workflow`

`docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md`. Cycle draft → validation client →
signature commandant → BL final, avec gel à la signature.

**Livré** : journalisation du portail, notify party. **Reste ≈ 9,5 j**, dépend de
la fusion Alembic.

Points métier tranchés par Yasmin : date *shipped on board* = **dernier jour des
opérations** (modifiable sous justification, avec journal) · **toujours 3
originaux** · **registre de remise** des originaux (téléchargement horodaté,
confirmation client, **et repli Opérations** avec date/heure/moyen/pièce jointe si
le BL part en papier) · signature **au choix** unitaire ou groupée.

⛔ **Un retrait a été refusé** : supprimer le « rail booking » comme la spec le
prévoyait aurait privé les clients de **toute** possibilité d'obtenir un
connaissement — le rail packing list n'a aucune route côté client, et l'interface
client expose un bouton visible. Le retrait a été **reséquencé dans le lot**, après
création des routes de remplacement.

### Relèves d'équipage — `feat/crew-rotations`

`docs/strategy/SPEC_LOT_RELEVES_EQUIPAGE.md` + `REFERENCE_METIER_RELEVES_EQUIPAGE.md`
(analyse des deux classeurs Excel de l'Armement, traités comme **référence métier
opposable**).

**La découverte structurante** : le moteur de la simulation n'est pas un
`début + 60`, c'est un **grand livre d'acquisition de congés** — chaque jour
embarqué crédite 0,9 j, chaque jour à terre en débite 1, avec des coefficients par
statut. Et ces coefficients sont **« fixés par la société et transmis à Silae pour
la paie »**.

⇒ **Le lot n'est pas un outil de pilotage, c'est un calcul de droits.** Une erreur
ne produit pas un écran faux mais un **bulletin faux**.

Bonne nouvelle : la chaîne est **déjà plombée sauf son maillon central**
(`PayrollVariable`, `SilaeExportBatch`, et le pont `Employee.crew_member_id`
existent).

**Livré** : vocabulaire canonique. **Reste ≈ 11 j**, dépend de la fusion Alembic.

Architecture retenue par Yasmin : **MyTOWT planifie et simule, Marad reste le
registre** (synchro inchangée, en lecture seule), avec un écran de réconciliation
des écarts. La double saisie est assumée.

---

## 7. Points ouverts qui te concernent

| # | Point | Pourquoi toi |
|---|---|---|
1 | **Fusion Alembic** (§2) | Touche l'historique de schéma |
2 | **Protection de branche sur `main`** absente (RAF R3) | Yasmin n'a pas les droits admin. Un incident de merge direct a déjà cassé `main` par le passé |
3 | **La suite de tests est Postgres-free** (RAF R5) | Tout tourne sur SQLite en mémoire : ni `TIMESTAMP WITH TIME ZONE`, ni `Numeric`, ni les migrations ne sont couverts. **Trois bugs de cette famille sont apparus cette seule semaine.** Le lot relèves manipulant des décimaux qui partent en paie, ce n'est plus une limite acceptable |
4 | **Le hook `alembic` du harnais crée des migrations parasites** (RAF R8) | Une migration vide et anonyme avait été posée sur la migration de fusion. Non commitée, écartée — mais si elle l'avait été, toute migration future se serait enchaînée depuis une révision anonyme |
5 | **`feature/qhse-foundation` : deux défauts détruisant des données** | Un filtre par mot-clé qui quarantaine sans persister la perte, et un `rollback()` dans une boucle d'import qui annule des lignes tout en les comptant comme importées. **Ne pas fusionner en l'état.** Correctif de quelques heures |
6 | `alembic upgrade head --sql` inutilisable | Une migration préexistante lit des données ⇒ impossible de prévisualiser le DDL d'un déploiement |

---

## 8. Sauvegardes et réversibilité

| Élément | État |
|---|---|
Tag `pre-upgrade-2026-08` | ✅ poussé sur `origin` |
`pg_dump` de la base de dev | ✅ hors dépôt, **restauration testée** (135 tables, comptages vérifiés) |
Branches | ✅ **toutes poussées** — plus aucun travail uniquement en local |
Branches supprimées | 3, après vérification que chaque commit survivait ailleurs. SHA consignés dans `07-ordre-pr-et-merge.md` |

---

## 9. Comment lire le reste

- **Le détail jour par jour**, y compris les erreurs commises et leurs
  corrections : `DEVELOPMENT_JOURNAL_2026-07-27_2026-08-17.md`.
- **L'ordre et la mécanique des PR** : `07-ordre-pr-et-merge.md` (§1 bis explique
  la stratégie d'attente, §2 bis l'état de chaque branche).
- **Le reste à faire**, avec pour chaque item ce qu'il bloque réellement :
  `PLAN_UPGRADE_PHASE2_2026-08.md` §12.
- **Les invariants à connaître avant de toucher au module équipage** :
  `CLAUDE.md`, section « Équipage — deux registres d'embarquement ». La règle d'or
  y est : *tout indicateur d'équipage doit dire de quel registre il parle.*

Le journal consigne aussi les **erreurs de l'assistant** et leurs corrections —
notamment deux outils de vérification défectueux et une conception qui aurait fait
planter chaque mutation du portail, attrapée par les tests avant tout commit. C'est
volontaire : une note de reprise qui ne mentionne que les succès n'est pas
vérifiable.
