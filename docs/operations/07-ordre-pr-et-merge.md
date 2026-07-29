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

## 1. Principe directeur

**Un lot = une branche = une PR = un objectif.** Chaque lot doit être
**mergeable et révocable indépendamment** : si un lot pose problème après
fusion, on doit pouvoir le retirer sans emporter les autres.

Corollaire pratique : **ne jamais empiler un lot sur un lot non mergé** sans
raison. Si c'est inévitable (le lot B a besoin du lot A pour être validé), le
signaler dans la PR et fusionner dans l'ordre.

---

## 2. Ordre recommandé

| # | Lot / branche | Contenu | Pourquoi à cette place | Risque |
|---|---|---|---|---|
| **1** | `chore/ci-integration-tests` | `voyage_track` (2 correctifs de fuseau), `ci.yml` (exécution `integration`+`regression` + libs WeasyPrint), 10 fichiers de tests périmés, `CLAUDE.md` (invariants), journal, plan | **Le filet d'abord.** Une fois mergé, **toutes** les PR suivantes sont vérifiées par la suite complète (198 fichiers au lieu de 84). C'est aussi la **première exécution réelle** du pipeline modifié — elle valide les 15 tests PDF sur Ubuntu et les paquets `apt`, aujourd'hui non vérifiés (RAF R4) | 🟢 Faible — 1 seul fichier applicatif, suite locale à 2000/15 |
| **2** | `docs/decouverte-fonctionnelle` | `PROJECT_CONTEXT.md` (§1-14 : architecture, workflows, audits, correction §7), `CLAUDE.md` (instructions temporaires), guide fonctionnel | **Documentation seule, zéro code.** Peut partir en parallèle du lot 1. Porte `PROJECT_CONTEXT.md`, que tous les autres documents référencent | 🟢 Nul — aucun code |
| **3** | Lot **fusion Alembic** (à créer) | Migration de fusion des deux `head` divergents (`20260716_0112` MRV / `20260720_0107` rapports générés) | 🔴 **Bloquant pour tout lot ultérieur portant une migration**, et pour tout déploiement (la production utilise Alembic exclusivement). Voir RAF R1. À faire valider par le manager — touche l'historique de schéma | 🟠 Modéré — historique de schéma |
| **4** | `feat/ops-quickwins` (J2) — **existe, commit `d4b3937`** | Alerte ETA en mer, nom client + `leg_code` sur la liste cargo, heures voile ×6 du Carnet de Bord | Répond à 2 demandes Opérations + 1 bug de document client. **Aucune migration** ⇒ peut passer avant le lot 3. ⚠️ **Empilée sur le lot 1** (a besoin de la suite verte pour se valider) : à rebaser sur `main` après la fusion du lot 1, ou à fusionner juste après lui | 🟢 Faible |
| **5** | Lot **workflow BL** | Draft → validation par le client titulaire du booking → signature commandant → BL final, avec journalisation. **Inclut aussi** l'unification des rails documentaires (décision D2, reportée du J2) et la journalisation des mutations du portail expéditeur. Voir `docs/strategy/SPEC_WORKFLOW_BILL_OF_LADING.md` | **Nécessite une migration** ⇒ **dépend du lot 3**. Ferme l'exposition juridique du registre BL. Le BL dégradé (sans consignataire) est aujourd'hui servi **au client** — c'est ce lot qui le remplace | 🟠 Modéré |
| **6** | Lots suivants (J3 faux verts, J9 horodatage…) | cf. plan | J9 porte une migration (`atd < ata`) ⇒ dépend aussi du lot 3 | selon lot |

### État des branches au 2026-07-29

| Branche | Commits sur `main` | Base | Contenu |
|---|---|---|---|
| `docs/decouverte-fonctionnelle` | 4 | `main` | Documentation seule |
| `chore/ci-integration-tests` | 18 | `main` | Filet CI + correctifs |
| `feat/ops-quickwins` | 19 | **`chore/ci-integration-tests`** | Les 3 quick wins (1 commit propre au lot) |
| `backup/ci-lot-avant-rebase` | — | — | Filet de secours, supprimable après fusion du lot 1 |

Les deux premières sont indépendantes et **fusionnent proprement entre elles**
(vérifié : `CLAUDE.md` s'auto-fusionne, régions disjointes).

### Lots existants à NE PAS fusionner en l'état

| Branche | Raison |
|---|---|
| `feature/qhse-foundation` | 🔴 Contient deux défauts qui **détruisent des données** : un filtre par mot-clé (`test\|essai\|demo`) qui quarantaine et n'importe jamais des non-conformités ISM légitimes sans persister la perte, et un `rollback()` dans la boucle d'import qui annule les lignes déjà insérées tout en les comptant comme importées. Correctif de quelques heures, à faire **avant** tout merge. Voir `PROJECT_CONTEXT.md` §14.7 |
| `feature/dashboard-env-integration`, `scratch/preintegration-rehearsal` | Divergentes de `main` (ahead/behind important). À arbitrer séparément, hors phase 2 |

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
