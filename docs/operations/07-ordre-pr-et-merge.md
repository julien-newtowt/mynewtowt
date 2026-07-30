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
