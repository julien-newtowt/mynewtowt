# Étude comparative — branches vs `main` & plan de rattrapage

> **Objet :** mesurer l'écart réel entre chacune des 32 branches distantes et
> `origin/main`, et définir le plan d'action de rattrapage (récupérations,
> purge, garde‑fous).
> **Date :** 2026‑07‑10. **Référence :** `origin/main` = `c443781`.
> **Branche d'étude :** `claude/branch-gap-analysis-plan-jkuqva`.
> **Méthode :** audit parallélisé multi‑agents — analyse sur pièce de chaque
> commit unique (diff hunk par hunk vs `main`, `git cherry`, dry‑run
> `git merge-tree`), inventaire exhaustif des branches, croisement avec l'état
> des Pull Requests GitHub — puis synthèse et contre‑vérifications ponctuelles.
>
> **Ce document remplace** l'étude du 2026‑06‑29 (qui ne comparait que la
> branche `claude/email-branches-audit-5uw0b2` à `main` ; sa PR #101 est mergée
> et son plan A–E est soldé — voir l'historique git de ce fichier).

---

## 0. Avertissement méthodologique — le piège du clone

C'est le **troisième** audit de branches de ce dépôt, et les trois se sont
heurtés au même genre d'artefact. À inscrire dans la procédure :

| Date | Artefact | Conclusion erronée induite |
|---|---|---|
| 2026‑06‑29 (étude v1) | `origin/main` **périmée** dans le clone local | « main très en retard, histoires non liées » |
| 2026‑06‑29 (étude transversale, jamais mergée) | même clone | « deux racines disjointes `35a0c77`/`78b6931`, 18 branches non fusionnables » |
| 2026‑07‑10 (la présente, 1ʳᵉ passe) | **clone shallow** (horizon à 147 commits) | « 10 branches divergentes de 26 à 235 commits d'avance » |

**Règle d'or avant tout audit de branches :**

```bash
git fetch --all --prune && git fetch --unshallow origin   # si clone shallow
git rev-parse --is-shallow-repository                     # doit répondre: false
```

Une fois l'historique complet récupéré, les « écarts spectaculaires » de la
première passe (235, 222, 183, 154 commits d'avance…) se sont **tous** révélés
fictifs : ils comptaient l'union symétrique de deux historiques que l'horizon
shallow empêchait de relier.

---

## 1. Topologie réelle du dépôt

- **Racine unique** : `78b6931` — « chore: rebuild repository as single root
  commit (history reset) », 2026‑06‑10. **Tout** le dépôt en descend : `main`
  **et** les 32 branches. Il n'existe **aucune lignée disjointe** (la thèse des
  « deux racines » de l'étude transversale du 06‑29 était un artefact, cf. §0).
- **`main`** : 411 commits, tronc unique et à jour — campagne de reprise
  P0/P1 (PR #50→#111), vitrine P3–P12, **MRV v2** (14 lots, migrations
  0096‑0105) et **vente à bord** mergés le 2026‑07‑10. 112 migrations Alembic.
- **Pull Requests** : **142 PR, toutes mergées, zéro PR ouverte.** Aucune
  branche n'est le support d'un travail en vol côté GitHub.

## 2. Inventaire des 32 branches distantes

`ahead`/`behind` mesurés sur historique **complet** vs `origin/main`.

### Classe 1 — entièrement contenues dans `main` (`ahead=0`) : 24 branches

Suppression sûre : chaque commit de ces branches est déjà atteignable depuis
`main` (containment prouvé par `git rev-list --count origin/main..X` = 0,
vérifié branche par branche le 2026‑07‑10).

| Branche | Dernier commit | Contenu (repère) | PR |
|---|---|---|---|
| `claude/amazing-davinci-yu4w6b` | 06‑18 | Runbook Marad + docs crew | #18–#48 mergées |
| `claude/booking-wizard-conversion-1xiuly` | 06‑29 | Wizard booking invité | #119 mergée |
| `claude/environmental-reporting-overhaul-2fuyzr` | 07‑10 | MRV v2 + vente à bord | (merge direct) |
| `claude/fervent-dijkstra-6dt7t8` | 06‑15 | Cache‑Control + CSRF | (squash absorbé) |
| `claude/gifted-franklin-6dw0nr` | 06‑19 | Sonde Marad getSyncDetails | (squash absorbé) |
| `claude/landing-page-v1-vyah6v` | 06‑29 | Landing i18n Anemos | #112–#117 mergées |
| `claude/marad-429-error-6ckesq` | 07‑06 | Marad passeports + 429 | #134–#142 mergées |
| `claude/newtowt-impact-preuves-v1-i75s8r` | 06‑29 | /impact, /preuves, /verify | #118 mergée |
| `claude/planning-module-audit-rmpbjg` | 07‑03 | Marad Retry‑After + KPI | (merge direct) |
| `claude/project-overview-docs-i6ire4` | 07‑03 | CLAUDE.md v3.11.0 | #133 mergée |
| `claude/seo-structured-data-cleanup-i91zoh` | 06‑30 | Tests B2B2C + finitions | #120–#128 mergées |
| `claude/sirh-integration-specs-0g9jif` | 06‑19 | SIRH L6 (coffre, entretiens) | (squash absorbé) |
| `claude/vibrant-bell-oxaff9` | 06‑22 | SOF analyse + Gantt | (squash absorbé) |
| `feature/grilles-multiroutes` | 06‑19 | Grilles multi‑routes (M6) | #49 mergée |
| `feature/onboard-sales-vente-bord` | 07‑10 | Vente à bord (espèces + CB) | (merge direct) |
| `vibe/anemos-carnet-bord-88bf9d` | 06‑23 | Carnet ANEMOS P1 | #88 mergée |
| + 8 branches « snapshot » pointant sur la racine `78b6931` elle‑même (06‑10, zéro contenu) : `claude/fix-tracking-dashboard-legs-ChCAz`, `claude/newtowt-erp-development-0kOfg`, `claude/quirky-edison-91j85`, `claude/repo-audit-improvement-puzglk`, `claude/review-app-versions-49Ilo`, `claude/trusting-darwin-jaoBa`, `claude/vigilant-gauss-wozms`, `claude/zealous-einstein-IA2mp` | | | |

### Classe 2 — commits uniques mais **absorbés par réécriture** (vérifié sur pièce) : 4 branches

| Branche | Commit unique | Verdict (preuves en §3) |
|---|---|---|
| `claude/admiring-clarke-l7obrz` | `c081615` COM‑11 pin PL/BL | ABSORBÉ à l'identique (migration 0080, modèles, tests présents dans `main`) |
| `claude/adoring-johnson-szpgln` | `f947694` COM‑13 funnel | ABSORBÉ par réécriture plus large (`analytics_events` + `/dashboard/analytics/commercial`) ; gap mineur « top routes » (optionnel, cf. R3) |
| `claude/newtowt-route-detail-rrjwug` | `2130bc8` i18n « certificat » | ABSORBÉ (les 5 catalogues i18n de `main` portent la terminologie cible) |
| `fix/ci-pipeline-repair` | 4 commits CI du 06‑10 | ABSORBÉ (CI actuelle équivalente ou supérieure) ; le reformat black massif `a79bcac` est **inapplicable** après 400 commits |

### Classe 3 — contenu unique **non absorbé** : 4 branches

| Branche | Commit(s) | Nature | Décision |
|---|---|---|---|
| `claude/mynewtowt-erp-architecture-j682l3` | `07fa395` | **Fix code réel** (test de connexion Marad) | **À récupérer — R1** |
| `claude/app-reference-docs-q4vx4g` | `4efb19a` | Doc de référence stratégique (815 l.), jamais passée par une PR | **À récupérer — R2** |
| `claude/email-branches-audit-5uw0b2` | `9936dce`+`731d90c` | Étude transversale des branches du 06‑29 | **Supersédée par la présente étude** (contenu réutilisé et re‑vérifié ici) — pas de cherry‑pick |
| `fix/git-stabilization` | `190a171` | Audit racine + doctrine git + script (agent tiers) | **Rien à reprendre** (périmé, partiellement fictif, contredit la pratique réelle) ; option résiduelle : `scripts/git-cleanup.sh` seul (R4) |

## 3. Les 12 commits uniques — verdicts détaillés

1. **`c081615`** (admiring‑clarke, 06‑24) — « pin packing list to origin leg »
   (COM‑11 lot 3). `git cherry` = patch‑équivalent déjà appliqué ;
   `migrations/versions/20260624_0080_packing_list_leg_pin.py` **identique**
   dans `main` ; logique `resolve_pl_context`/`coalesce(leg_id…)` et tests
   (`test_packing_list_com11_legpin.py`) présents et même dépassés par
   CARGO‑14. → **absorbé, purge**.
2. **`f947694`** (adoring‑johnson, 06‑13) — instrumentation funnel COM‑13.
   Non patch‑équivalent, mais l'objectif est livré **plus largement** dans
   `main` : `analytics_events` (17 types), route
   `/dashboard/analytics/commercial` (funnel 7 étapes + B2B2C),
   `Booking.source_quote_reference` remplaçant la FK `quote_id` du commit ; la
   migration du commit (`…_0037_booking_quote_link.py`) est en **collision de
   numéro** avec la chaîne actuelle. Seul manque : le classement « top routes »
   (aucune occurrence dans `main`). → **absorbé par réécriture, purge** ;
   gap optionnel → R3.
3. **`07fa395`** (mynewtowt‑erp‑architecture, 07‑03) — **le test de connexion
   Marad sonde les navires au lieu de l'équipage** (vraie cible de
   l'intégration). Vérifié le 07‑10 : `app/utils/marad.py::diagnose()` de
   `main` ne calcule toujours que `vessels_count` ; aucun `crew_count` /
   `_count_records` dans `app/` ni `tests/` ; le badge admin affiche encore
   « Connexion établie — 0 navire(s) visibles », trompeur quand l'auth marche.
   Cherry‑pick mécanique **en conflit** (10 commits récents sur ce fichier,
   dont FLGO lot 7 du 07‑09). → **non absorbé, à ré‑implémenter — R1**.
4. **`2130bc8`** (newtowt‑route‑detail, 06‑29) — i18n `rd_anemos_certified`
   sans le mot « label ». Les 5 catalogues (`fr`, `en`, `es`, `pt_br`, `vi`)
   de `main` portent exactement la terminologie cible. → **absorbé, purge**.
5. **`4efb19a`** (app‑reference‑docs, 06‑29) —
   `docs/REFERENCE_STRATEGIQUE_ET_FONCTIONNELLE_NEWTOWT.md` (815 l.). **Pas un
   doublon** : document business (concurrence, personas, golden paths, modèle
   de données ~95 tables, gaps COM‑xx/ENV‑xx, roadmap, sources), complémentaire
   du `DOCUMENT_REFERENCE_CONTEXTE_APPLICATION.md` (technique) né le même jour
   sur `main`. Jamais mergé ni proposé en PR — serait perdu à la purge.
   Chiffres à rafraîchir (v3.0.0→3.11.0, 90→112 migrations, MRV v2 absent).
   → **non absorbé, à récupérer — R2**.
6. **`9936dce` + `731d90c`** (email‑branches‑audit, 06‑29) — étude
   transversale `ETUDE_COMPARATIVE_ETAT_BRANCHES.md` (20 branches, classes
   A/B/C/D + table de vérification). Contenu de valeur mais : prémisse « deux
   racines disjointes » **fausse** (artefact, cf. §0), périmètre périmé (20
   branches vs 32 aujourd'hui), et l'intégralité de ses vérifications a été
   refaite et étendue par la présente étude. → **supersédé, purge après merge
   de la présente étude**.
7. **`190a171`** (git‑stabilization, 06‑18, agent tiers) — 3 fichiers :
   `AUDIT_COMPLET_2026.md` (racine, hors convention `docs/audit/`, périmé —
   v3.0.0, avant MRV v2, doublon du corpus d'audit existant) ;
   `docs/development/git-strategy.md` (recouvre CLAUDE.md, cite une branche
   `staging` et un `CONTRIBUTING.md` **inexistants**, prescrit « éviter les
   merges » à rebours de la pratique réelle, promet des hooks locaux non
   versionnables là où la CI de `main` fait déjà mieux) ;
   `scripts/git-cleanup.sh` (script de purge de branches correct, seule pièce
   à valeur d'usage). → **rien à reprendre en l'état** ; option R4 pour le
   script seul.
8. **`a9046db`, `7270ee1`, `a79bcac`, `ab493a6`** (ci‑pipeline‑repair, 06‑10)
   — lint UP038, mypy informatif + bandit B108, reformat black (154 fichiers),
   réparation pipeline. La CI actuelle de `main` (`.github/workflows/ci.yml` :
   ruff+black+mypy informatif, pytest+coverage, bandit+pip‑audit+gitleaks)
   couvre l'équivalent ; le reformat massif est inapplicable après 400 commits.
   → **absorbé/périmé, purge**.

## 4. Plan de rattrapage

### Phase 1 — Récupérations (à exécuter **avant** toute purge)

| # | Action | Détail | Effort |
|---|---|---|---|
| **R1** | ✅ **Réalisé (2026‑07‑10)** — fix Marad ré‑implémenté (commit `531e823` sur la branche d'étude ; `07fa395` a servi de spécification, cherry‑pick impossible pour cause de conflit) | `diagnose()` sonde l'équipage après auth réussie (`crew_count`, `_count_records` tolérant aux enveloppes, schéma d'auth mémorisé → single‑shot) ; badge 3 états (équipage visible / compte vide / repli navires‑quota) ; tests unit + rendu (46 verts). | ~~M~~ |
| **R2** | ✅ **Réalisé (2026‑07‑10)** — doc de référence stratégique récupérée (cherry‑pick `771aa83` + rafraîchissement `6a6c8fa` sur la branche d'étude) | Chiffres recomptés (129 tables, 112 migrations, 43 routers…), version 3.11.0, MRV v2 + vente à bord intégrés, statuts 16.x/17.1 revus, frontière explicitée avec `DOCUMENT_REFERENCE_CONTEXTE_APPLICATION.md`. | ~~M~~ |
| R3 | *(Optionnel)* Classement « top routes » du funnel | Petit ajout ciblé à `modules_router.py::analytics_commercial` (seule idée de `f947694` sans équivalent dans `main`). | S |
| R4 | *(Optionnel)* Outil de purge de branches | Reprendre isolément `190a171:scripts/git-cleanup.sh` (retirer les références à `staging`). Aucun besoin exprimé au backlog — ne le faire que si l'hygiène de branches devient récurrente. | S |

### Phase 2 — Archivage puis purge (après validation humaine)

> **Statut 2026‑07‑10 :** exécution tentée depuis la session d'étude et
> **bloquée à juste titre** — (a) le credential de session est restreint à la
> branche de travail (HTTP 403 sur `git push origin <tags>`), (b) le garde‑fou
> d'exécution refuse une suppression de branches sans validation nommée.
> **À exécuter par un humain** (poste avec droits push complets) : les blocs de
> commandes ci‑dessous sont prêts à copier‑coller, dans cet ordre (tags
> d'abord — ils rendent la purge réversible).

La purge est **sans risque côté PR** (la seule PR ouverte est la **#143**,
portée par `claude/branch-gap-analysis-plan-jkuqva`, hors périmètre de purge).
Par prudence, poser d'abord des tags d'archive sur les 8 têtes porteuses de
commits uniques (les commits restent atteignables, la purge devient
réversible) :

```bash
git tag archive/admiring-clarke-l7obrz      origin/claude/admiring-clarke-l7obrz
git tag archive/adoring-johnson-szpgln      origin/claude/adoring-johnson-szpgln
git tag archive/app-reference-docs-q4vx4g   origin/claude/app-reference-docs-q4vx4g
git tag archive/email-branches-audit-5uw0b2 origin/claude/email-branches-audit-5uw0b2
git tag archive/mynewtowt-erp-architecture  origin/claude/mynewtowt-erp-architecture-j682l3
git tag archive/newtowt-route-detail-rrjwug origin/claude/newtowt-route-detail-rrjwug
git tag archive/ci-pipeline-repair          origin/fix/ci-pipeline-repair
git tag archive/git-stabilization           origin/fix/git-stabilization
git push origin --tags
```

Purge immédiate — les 24 branches de classe 1 + les 4 de classe 2 +
`fix/git-stabilization` (29 branches) :

```bash
git push origin --delete \
  claude/admiring-clarke-l7obrz claude/adoring-johnson-szpgln \
  claude/amazing-davinci-yu4w6b claude/booking-wizard-conversion-1xiuly \
  claude/environmental-reporting-overhaul-2fuyzr claude/fervent-dijkstra-6dt7t8 \
  claude/fix-tracking-dashboard-legs-ChCAz claude/gifted-franklin-6dw0nr \
  claude/landing-page-v1-vyah6v claude/marad-429-error-6ckesq \
  claude/newtowt-erp-development-0kOfg claude/newtowt-impact-preuves-v1-i75s8r \
  claude/newtowt-route-detail-rrjwug claude/planning-module-audit-rmpbjg \
  claude/project-overview-docs-i6ire4 claude/quirky-edison-91j85 \
  claude/repo-audit-improvement-puzglk claude/review-app-versions-49Ilo \
  claude/seo-structured-data-cleanup-i91zoh claude/sirh-integration-specs-0g9jif \
  claude/trusting-darwin-jaoBa claude/vibrant-bell-oxaff9 \
  claude/vigilant-gauss-wozms claude/zealous-einstein-IA2mp \
  feature/grilles-multiroutes feature/onboard-sales-vente-bord \
  fix/ci-pipeline-repair fix/git-stabilization \
  vibe/anemos-carnet-bord-88bf9d
```

Purge différée — les 3 branches restantes, **une fois la présente étude mergée
dans `main`** (la phase 1 — R1 + R2 — est exécutée et vit sur la même branche ;
tant qu'elle n'est pas mergée, ces 3 branches restent le seul refuge de leur
contenu) :

```bash
git push origin --delete \
  claude/mynewtowt-erp-architecture-j682l3 \
  claude/app-reference-docs-q4vx4g \
  claude/email-branches-audit-5uw0b2
```

### Phase 3 — Garde‑fous (éviter la re‑prolifération)

1. **Procédure d'audit** : historique complet obligatoire (`fetch --unshallow`
   + `--prune`) avant toute mesure d'écart — cf. §0, trois artefacts en trois
   études.
2. **GitHub** : activer *« Automatically delete head branches »* (Settings →
   General) — 142 PR mergées ont laissé leurs branches en place ; ce réglage
   solde le problème à la source.
3. **Hygiène** : les branches de session `claude/*` sont jetables par contrat —
   toute récupération passe par PR ou cherry‑pick documenté, jamais par
   conservation de la branche.
4. **Périodicité** : re‑dérouler cet inventaire (§2) trimestriellement ou après
   toute campagne multi‑branches ; le présent document fait foi et se met à
   jour en place.

### Phase 4 — Ménage connexe (constaté au passage)

- ✅ **Réalisé (2026‑07‑10)** : `pyproject.toml` et `app/config.py` alignés sur
  `version = "3.11.0"` (seul consommateur : le global Jinja `app_version` —
  aucun test ne figeait l'ancienne valeur).

## 5. Synthèse exécutive

- **`main` est un sur‑ensemble strict de 30 des 32 branches distantes.**
  L'écart réel total du dépôt tient en **12 commits uniques**, dont **10 sont
  absorbés ou périmés** (vérifiés sur pièce, preuves en §3).
- **Deux récupérations seulement** justifiaient le « rattrapage » : le fix du
  test de connexion Marad (R1) et le document de référence stratégique (R2) —
  **toutes deux exécutées le 2026‑07‑10** sur la branche d'étude (commits
  `531e823`, `771aa83`+`6a6c8fa`). Restent deux options mineures (R3, R4).
- **Purge possible de 29 branches immédiatement** (après tags d'archive), 3 de
  plus après la phase 1. Zéro PR ouverte : aucun travail en vol n'est menacé.
- **Les écarts « spectaculaires » n'existaient pas** : 235/222/183/154 commits
  « d'avance » étaient des artefacts de clone shallow. La leçon de méthode est
  désormais codifiée (§0, phase 3).
