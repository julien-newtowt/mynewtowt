# Audit de compatibilité `main` × PR en attente — 2026-09-03

> **Objet** : les évolutions de `main` et les développements de PR ont progressé en
> parallèle. Ce document établit si les 5 PR ouvertes sont intégrables au `main`
> actuel, et propose l'ordre et les conditions de leur déploiement sans compromettre
> le fonctionnement de l'application.
>
> **Méthode** : cellule d'audit multi-agents (4 agents spécialisés + coordination),
> fusions simulées en worktrees jetables, exécution réelle de `black`, `ruff`,
> `mypy`, `pytest`, `alembic heads`, et lecture des runs CI GitHub. **Aucune branche
> n'a été modifiée, fusionnée ou poussée.** Les contradictions entre agents ont été
> tranchées par exécution, pas par arbitrage d'opinion (cf. §6).

---

## 1. Situation

| | |
|---|---|
| `main` | `e869937` (Merge PR #181, ADR-014 reprise d'historique TOWT) |
| Base commune des 5 PR | `afa66d9` (Merge PR #169, PLN-SEQ) |
| Écart | **27 commits**, 71 fichiers, +8163 / −737 lignes |
| PR ouvertes | #170, #171, #172, #173, #174 — toutes **27 commits behind** |

Entre l'ouverture des PR (2026-09-01/02) et aujourd'hui, `main` a absorbé 8 PR
(#175 → #181) : séquence déclarative départ/arrivée, page unique « Créer un leg »
(PLN-08), référentiel de ports UN/LOCODE + hiérarchie de sources, sélecteur de ports
côté serveur, et **ADR-014** (legs `origin='towt_archive'` en lecture seule).

**Aucune des 5 PR n'a été rebasée depuis.** Les vérifications qu'elles portent dans
leur description (CI verte, chaîne Alembic rejouée, rechaînage de migration) étaient
exactes le 2026-09-01 et ne le sont plus.

---

## 2. Verdict par PR

| PR | Branche | Fusion Git | Alembic | Verdict |
|---|---|---|---|---|
| **#170** | `fix/ci-black-pln-seq` | ⚠️ conflit `captain_router.py` | — | 🔵 **Fermer sans fusion — obsolète** |
| **#171** | `docs/note-reprise-2026-09-01` | ✅ propre | — | 🟡 **Corriger avant fusion** |
| **#172** | `docs/claude-md-socle-methode` | ✅ propre | — | 🟢 **Fusionner tel quel** |
| **#173** | `feature/support-ticketing` | ✅ propre | 🔴 collision | 🟡 **Fusionner après rechaînage (1 ligne)** |
| **#174** | `feature/dashboard-env-integration` | ⚠️ conflit `planning.py` | 🔴 collision | 🟠 **Scinder — arbitrage requis sur le DROP** |

---

## 3. Bloquant préalable — **`main` est rouge aujourd'hui**

*Aucune des 5 PR ne corrige ce défaut, et il rend rouge la CI de toutes.*

**Run CI #537** sur `e869937` (2026-09-03 05:32) : `lint` **failure**, `test`
**failure**, `security` success, `build` **skipped** → aucune image Docker n'est
construite depuis `main`.

### 3.1 `lint` — `black` (Fait, reproduit)

```
would reformat app/services/admin_data.py
would reformat app/services/social_proof.py
2 files would be reformatted, 541 files would be left unchanged.
```

Les deux fichiers ont été introduits par `9ca0bfd` (ADR-014, PR #181).
**Cause racine : dérive de toolchain**, pas un oubli. Le code a été formaté avec un
`black` récent (style « hug » sur les messages d'`assert`), alors que la CI épingle
`black==24.10.0` qui exige l'ancien style. Correctif : `black app tests`, 10 lignes.

`ruff check app tests` sur `main` : **vert**. `mypy app` : **358** erreurs pour un
cliquet `MYPY_MAX=371` → marge de 13 (la note de la PR #171 annonce « 371 exactement »,
c'est périmé).

### 3.2 `test` — `anyio` non épinglé (Fait, reproduit et corrigé en test)

Le job `test` échoue en **26 secondes** (une suite complète prend ~10 min) :
c'est une **erreur de collecte**, reproduite à l'identique en local sur `main` :

```
ERROR tests/unit/test_csrf.py            - DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated…
ERROR tests/unit/test_trombinoscope_api.py
ERROR tests/integration/test_error_pages.py
ERROR tests/integration/test_marad_flgo.py
Interrupted: 4 errors during collection
3097 tests collected, 4 errors
```

**Mécanisme** : `anyio` n'est épinglé **nulle part** (`requirements.txt` /
`requirements-dev.txt`) — il flotte comme dépendance transitive. `anyio 4.15.0`
déprécie `anyio.abc.BlockingPortal`, que `starlette 0.41.3` (épinglé via
`fastapi==0.115.6`) importe encore. `pyproject.toml` pose `filterwarnings = ["error"]`
→ l'avertissement devient une erreur → la collecte s'interrompt.

**`main` est donc devenu rouge sans qu'aucune ligne de code ne change.**

**Correctif validé par exécution** (pas une hypothèse) : avec `anyio==4.6.2.post1`,
les 4 modules incollectables passent — **45 tests verts**.

```diff
  # requirements.txt
+ # Épinglé : anyio ≥ 4.7 déprécie `anyio.abc.BlockingPortal`, que
+ # starlette.testclient importe encore ; pyproject transforme les
+ # DeprecationWarning en erreurs, ce qui interrompt la collecte pytest.
+ anyio==4.6.2.post1
```

> **Recommandation** : épingler plutôt que filtrer. Un
> `ignore::DeprecationWarning:anyio.*` masquerait la dérive au lieu de la borner —
> l'inverse de la doctrine déjà en place dans ce dépôt (cliquet mypy, garde-fou
> gitleaks).

---

## 4. Bloquant transverse — collision Alembic à trois têtes

**Fait, mesuré à l'outil et par la sentinelle du dépôt.**

`main` porte une tête unique : `20260902_0138`. Or **#173 et #174 chaînent toutes
deux leur migration sur `20260901_0136`**, que `main` a depuis consommé avec `0137`
puis `0138`. Le rechaînage effectué par Yasmin le 2026-09-01 était correct à
l'époque ; les fusions #177 → #181 l'ont périmé.

```
 20260901_0136
   ├─► 20260901_0137 ─► 20260902_0138        ◄── TÊTE 1  (main)
   ├─► 20260821_0119  support_ticketing      ◄── TÊTE 2  (#173)
   └─► 20260713_0106  drop_mrv_legacy        ◄── TÊTE 3  (#174)
```

Vérifié par exécution de la sentinelle `tests/regression/test_alembic_single_head.py`
sur les arbres fusionnés :

| Arbre | Résultat |
|---|---|
| `main` seul | ✅ 2 passed |
| `main` + #173 | ❌ `plusieurs têtes (20260821_0119, 20260902_0138)` |
| `main` + #173 + #174 | ❌ `plusieurs têtes (20260713_0106, 20260821_0119, 20260902_0138)` |

**Bonne nouvelle** : la sentinelle existe sur `main`, tourne en CI
(`pytest … tests/regression`) et **bloque le défaut avant le déploiement**. Son
docstring rappelle que la panne s'est produite **deux fois en production** (07/08 et
26/08/2026), à chaque fois découverte au déploiement — c'est précisément pour cela
qu'elle a été posée. Ce serait la troisième occurrence.

**Prescription — rechaîner, ne pas fusionner les têtes.** Les deux révisions ne sont
pas publiées : le rechaînage est sûr. Une révision de fusion (`alembic merge heads`)
ne serait la bonne réponse que si elles étaient déjà sur `main`.

⚠️ **Discipline séquentielle obligatoire** : rechaîner chaque migration sur la tête
**réelle au moment de sa fusion**, jamais en avance. Rechaîner les deux PR en
parallèle sur `0138` reproduirait exactement le défaut.

---

## 5. Détail par PR

### PR #170 — `fix/ci-black-pln-seq` → 🔵 **fermer sans fusion**

**Fait** : `main` a déjà absorbé le formatage de ces 4 fichiers (commit `4387d0e`,
2026-09-02). Vérifié : `black --check` sur les 4 fichiers visés →
*« 4 files would be left unchanged »*. La PR n'a plus d'objet, et sa fusion — même
après résolution du conflit — ne changerait **pas un octet** du dépôt.

**Fait** : son unique conflit sur `app/routers/captain_router.py` n'est pas un
conflit de formatage mais un conflit **sémantique** : `main` a élargi
`except VoyageSequenceError` en `except PlanningError` pour couvrir l'erreur
ADR-014 (leg d'archive immuable). Résoudre en faveur de la PR **retirerait** cette
couverture.

**Fait** : #170 ne remet pas `main` au vert. Le rouge actuel porte sur
`admin_data.py` / `social_proof.py`, hors de son périmètre.

### PR #171 — note de reprise → 🟡 **corriger avant fusion**

Merge propre, aucun code touché. Mais le document ajoute un **§0 « qui fait foi »**
dont plusieurs affirmations sont fausses à la date de fusion :

| Affirmation du §0 | Réalité vérifiée |
|---|---|
| « `main` est à `afa66d9`, 145 migrations, tête `0136` » | `e869937`, **147 migrations**, tête `20260902_0138` |
| « les 4 branches ont `main` intégré, **behind = 0** » | **27 behind** pour les quatre |
| « `main` rouge sur lint → PR #170 débloque tout » | `main` rouge sur **2 autres fichiers** + `anyio` ; #170 ne débloque rien |
| « cliquet mypy : 371 exactement » | **358 / 371**, marge de 13 |

Le fichier `09-note-reprise-2026-09-01.md` est correctement daté et cadré comme
historique — il peut rester tel quel. Seul le §0 de `07-ordre-pr-et-merge.md` doit
être réécrit, sinon le dépôt publie un runbook faux le jour de sa fusion.

### PR #172 — scission de `CLAUDE.md` → 🟢 **fusionner tel quel**

Retire bien la section « Manager on Leave (2026-07-27 → 2026-08-17) », périmée depuis
le 17/08. **Contrôle sémantique effectué** : le `CLAUDE.md` *résultant de la fusion*
a été extrait et comparé à celui de `main` — le diff est **identique au diff du
commit**, ligne pour ligne. Aucune section ajoutée par `main` depuis `afa66d9`
(ADR-014, ports UN/LOCODE, PLN-SEQ) ne disparaît. Même contrôle sur
`PROJECT_CONTEXT.md` : rien de perdu. Le risque d'« amputation silencieuse par merge
propre » est **infirmé**, la PR ne touchant que le préambule.

### PR #173 — module Assistance → 🟡 **fusionner après rechaînage**

**Le profil le plus favorable des quatre.** Strictement additif : 3 tables, 11 routes,
1 module de permissions, 0 fichier existant restructuré, 0 conflit textuel.

Contrôles conformes : **11 routes / 11 gardes** `require_permission("support", …)` ;
couverture **9/9 rôles** sans trou ; ARC-04 respecté (l'écran `/admin/permissions`
itère `MODULES`, aucune migration de seed nécessaire) ; ordre des routes littérales
avant `/{ref}` ; **5 catalogues i18n à 1570 clés chacun**, aucune divergence ;
6 mutations → 6 `flush` + 6 `RedirectResponse(303)` + 6 `activity.record()`, zéro
`db.commit()`, zéro `<script>` inline, CSRF sur les 6 formulaires ; `ruff` propre.

**Un seul bloquant** : `down_revision` périmé (§4).

```diff
# migrations/versions/20260821_0119_support_ticketing.py
-Revises: 20260901_0136
-down_revision = "20260901_0136"
+Revises: 20260902_0138
+down_revision = "20260902_0138"
```

*Renumérotation recommandée* en `20260903_0139` : le numéro `0119` est **déjà pris**
sur `main` (`20260826_0119_merge_heads_bl_qhse.py`). Aucun impact technique — rien ne
dépend de l'ordre lexicographique des fichiers, vérifié — mais l'ordre de lecture
contredit l'ordre d'application, ce qui est précisément le terrain qui a produit trois
fois la panne de têtes multiples.

**Aucun conflit #173 ↔ #174** : `_layout.html` et les 5 catalogues i18n, partagés par
les deux PR, fusionnent automatiquement (zones disjointes).

### PR #174 — Dashboard Perf v2 + DROP legacy MRV → 🟠 **scinder**

#### Conflit `app/services/planning.py`

Un seul conflit, 6 lignes, dans `_leg_blocking_models()`. `main` a extrait l'inventaire
en fonctions dédiées et déplacé l'import `LegKPI` ; la PR retire `MRVEvent`.

**Résolution — remplacer le bloc de marqueurs par :**
```python
    from app.models.finance import LegFinance
```
Ne **pas** réintroduire `LegKPI` (il est importé dans `delete_leg`).

⚠️ **Les deux résolutions naïves sont fausses, et une seule est rattrapée par la CI :**
- côté PR tel quel (`LegFinance, LegKPI`) → `LegKPI` inutilisé → **ruff F401**, bloquant ;
- côté `main` tel quel → `from app.models.mrv import MRVEvent` sur un module supprimé →
  **`ImportError` à l'exécution**. L'import étant local à la fonction, il est invisible
  au démarrage et ne sort qu'à la **suppression d'un leg (500)**.

#### Le DROP `mrv_events` / `mrv_parameters`

- `upgrade()` : deux `drop_table`, sans `COUNT(*)`, sans archivage préalable.
- `downgrade()` **existe** et recrée les deux tables — **structure seulement**.
  Perte de contenu : **100 %**, non récupérable hors restauration de sauvegarde.
- Le « ⚠ GATE HUMAIN » de la migration n'est **qu'une docstring** : ni le code, ni la
  CI, ni le runbook ne l'appliquent.
- **Aucun ADR** ne couvre cette suppression (`docs/architecture/` s'arrête à ADR-014),
  alors que le CLAUDE.md l'exige pour toute décision technique importante.

⚠️ **Point de vigilance sur la justification.** L'arbitrage **Q1** (« MRV v2 —
démarrage à vide ») est cité à l'appui du DROP, mais **il ne le couvre pas** : Q1 porte
sur la **capture v2** (`nav_events`, `bunker_operations`), pas sur les tables **legacy
V1** `mrv_events` / `mrv_parameters`, antérieures à la refonte. La migration
`20260709_0105` les avait d'ailleurs **délibérément conservées** en archive lecture
seule. Personne n'a produit le `COUNT(*)` de production — seule la base peut le dire.

**Dérisquage constaté** : le repli `legacy_noon` du grand livre lit `noon_reports`, pas
`mrv_events` — le DROP ne le casse pas.

#### Hygiène — bonne

Aucune référence pendante en code actif après fusion (vérifié par grep exhaustif +
`compileall`) : `ALLOWED_EXPORT_TABLES` nettoyé, `purge_legs.py` mis à jour, les
12 modules de test qui importaient `_setup_leg` depuis `test_mrv_reprise` pointent tous
vers `conftest.py`. Le mode strict de `kpi_env` **ne lève pas** sur un leg d'archive
TOWT (c'est un filtre, pas une garde) — le point d'interaction le plus redouté avec
ADR-014 est sain.

#### Écarts restants

- **`/dashboard-env*` → 404** : aucune redirection 301 vers `/dashboard-perf`. Les
  favoris des Opérations tombent. Correctif : 3 lignes.
- **Documentation non synchronisée** : `PROJECT_CONTEXT.md:26,141,144` décrit toujours
  l'archive `/mrv/archive/events` comme vivante ; `app/models/sof_event.py:7` mentionne
  le mapping vers `MRVEvent`.

---

## 6. Contradictions entre agents, tranchées par exécution

| Point | Position contestée | Tranché par |
|---|---|---|
| « La double tête Alembic est invisible pour la CI » | Faux | Exécution de `test_alembic_single_head` sur les arbres fusionnés : elle échoue à 2 puis 3 têtes. Le défaut **bloque la CI**, il n'atteint pas la production silencieusement. |
| « La suite complète est verte sur l'arbre #173 fusionné (3238 passés) » | Non reproductible | La CI (#537) et la reproduction locale donnent 4 erreurs de collecte `anyio` sur `main` seul. L'écart s'explique par la version d'`anyio` résolue à l'installation — ce qui **est** l'argument pour l'épingler. |

---

## 7. Risques

| # | Risque | Cote |
|---|---|---|
| R1 | **`main` rouge — `anyio` non épinglé** : bloque la qualification de **toute** PR ; dérive externe non maîtrisée | 🔴 **Critique** |
| R2 | **DROP irréversible** sans `COUNT(*)`, sans export, sans ADR ; retour arrière = restauration globale | 🟠 **Élevé** |
| R3 | **Têtes Alembic multiples** (#173 + #174) : déploiement en échec — mais **détecté en CI** et `deploy.sh` restaure le snapshot | 🟠 **Élevé** |
| R4 | **Résolution naïve du conflit `planning.py`** → 500 sur la suppression d'un leg, non couvert par la CI | 🟠 **Élevé** |
| R5 | **`/dashboard-env*` → 404** au moment où les Opérations reprennent | 🟡 Modéré |
| R6 | **`main` rouge — `black`** (2 fichiers) | 🟡 Modéré |
| R7 | **Documentation périmée publiée** (§0 de #171, `PROJECT_CONTEXT.md` de #174) | 🟡 Modéré |
| R8 | Numérotation de migration incohérente avec l'ordre du graphe | 🟢 Faible |
| R9 | Cliquet mypy — marge de 13 (358/371) | 🟢 Faible |

---

## 8. Plan d'action

### Étape 0 — Remettre `main` au vert *(préalable absolu, PR dédiée `fix/ci-main-vert`)*

Aucune des 5 PR ne traite ces deux défauts, et **rien ne peut être qualifié avant**.

```bash
git checkout -b fix/ci-main-vert origin/main
black app tests                     # → admin_data.py + social_proof.py
# + épingler anyio==4.6.2.post1 dans requirements.txt (correctif validé : 45 tests verts)
```
**Gate** : run CI vert sur la PR, puis vérifier le run `push` sur `main`.

> À trancher séparément : faut-il **remonter le pin `black`** plutôt que reformater à
> l'ancien style ? Le code actuel est écrit dans le style d'un `black` récent ; le
> reformater à `24.10.0` sera re-cassé par le prochain contributeur dont l'outil est à
> jour. Remonter le pin reformate en revanche tout le dépôt — décision de gestion.

### Étape 1 — Fermer #170 sans fusion

Payload no-op prouvé ; son seul effet résiduel est un conflit sémantique à ne surtout
pas résoudre en sa faveur.

### Étape 2 — #172 puis #171 *(documentation)*

#172 est vérifiée sans perte : la fusionner en premier limite sa divergence.
#171 **après correction du §0** (tête Alembic, compteurs behind, diagnostic CI, ordre
de fusion, marge mypy).

### Étape 3 — #173 *(module Assistance)*

```bash
git checkout feature/support-ticketing && git merge origin/main   # propre
git mv migrations/versions/20260821_0119_support_ticketing.py \
       migrations/versions/20260903_0139_support_ticketing.py
# revision → 20260903_0139 ; down_revision → 20260902_0138
pytest tests/regression/test_alembic_single_head.py -q --no-cov   # DOIT être vert
```
**Gate** : CI verte + sentinelle Alembic passante.

### Étape 4 — #174 : **scinder en trois**

| PR | Contenu | Risque | Quand |
|---|---|---|---|
| **A — `refactor(mrv)` préparation** | `decimal_to_dms` → `app/utils/geo`, nettoyage `mrv_export`, retrait de `MRVEvent` du garde-fou `delete_leg`, relocalisation de `_setup_leg` | 🟢 nul, aucun DDL | immédiat |
| **B — `feat(dashboard-perf)`** | mode strict `kpi_env`, contrat gelé, 5 pages, exports, **+ redirection 301** | 🟢 réversible par `git revert` | après A |
| **C — `feat(mrv)!` DROP** | suppression du legacy, migration rechaînée, **ADR-015**, procédure runbook | 🟠 irréversible | après arbitrage |

**Pourquoi scinder** : PR-A absorbe à elle seule **la totalité du conflit
`planning.py`**. PR-B livre la valeur opérationnelle **sans engager la base** — le mode
strict filtre sur `LegEmissionRecord.source`, jamais sur l'existence de `mrv_events`.
En l'état, refuser le DROP revient à refuser le dashboard : un couplage artificiel
entre une livraison P1 et une décision P0 d'intégrité de données. Et 63 fichiers /
6 000 lignes mêlant un dashboard et une suppression de tables ne se revoit pas
sérieusement.

**Prérequis non négociables de PR-C :**
1. `SELECT count(*) FROM mrv_events;` / `mrv_parameters;` **sur la production**, résultat
   consigné dans la PR. Si ≠ 0 → **STOP**.
2. Export ciblé préalable **même à zéro** — coût nul, et il transforme un rollback
   global (destructeur pour la caisse, les ventes détaxées, `rate_offer_revisions`) en
   réinjection chirurgicale de deux tables :
   ```bash
   pg_dump "$DATABASE_URL" -t mrv_events -t mrv_parameters -Fc \
     > backups/mrv-legacy-$(date -u +%Y%m%dT%H%M%SZ).dump
   ```
3. **ADR-015** rédigé et validé.
4. Rechaînage sur la tête réelle **au moment de la fusion**.

> **Alternative recommandée, strictement moins risquée** :
> `ALTER TABLE mrv_events RENAME TO mrv_events_deprecated_20260903;` (idem
> `mrv_parameters`), `DROP` dans une migration ultérieure. Atteint 100 % de l'objectif
> technique — plus de rail de lecture, code applicatif supprimé, schéma nettoyé côté
> ORM — tout en gardant la donnée récupérable. Si l'analyse est juste, le coût est nul ;
> si elle a manqué quelque chose, elle sauve une déclaration réglementaire.

### Étape 5 — Déploiement

Staging d'abord (base non vide), puis production via `scripts/deploy.sh` — **jamais**
`--skip-snapshot` sur le déploiement portant le DROP : le snapshot en est le seul filet.
Fenêtre creuse : un navire est attendu à quai, et la caisse de bord comme les ventes
sont des registres append-only qu'une restauration globale détruirait.

### Re-tests obligatoires après chaque fusion

Chaque fusion invalide le merge-test des suivantes dès qu'un fichier est partagé —
et **systématiquement** dès qu'une migration est publiée.

| Fichier | #170 | #171 | #172 | #173 | #174 |
|---|:--:|:--:|:--:|:--:|:--:|
| `CLAUDE.md` | | | ✅ | ✅ | ✅ |
| `app/templates/staff/_layout.html` | | | | ✅ | ✅ |
| `app/i18n/{fr,en,es,pt_br,vi}.py` | | | | ✅ | ✅ |
| `app/main.py`, `app/models/__init__.py` | | | | ✅ | ✅ |
| `app/routers/captain_router.py` | ✅ | | | | ✅ |
| `app/services/planning.py` | | | | | ✅ |

- Après **#172** → re-tester #173 et #174 (`CLAUDE.md`).
- Après **#173** → re-tester #174 **impérativement** : 9 fichiers partagés **et** la
  tête Alembic change.

**Contrôle systématique avant chaque merge** :
`pytest tests/regression/test_alembic_single_head.py -q --no-cov`

---

## 9. Constats hors périmètre des PR

### 9.1 Un correctif opérationnel orphelin, sans PR

La branche `fix/stock-scientific-notation` (2026-09-01, **jamais fusionnée, aucune PR
ouverte**) corrige l'affichage du stock de Vente à bord en notation scientifique
(« 3E+1 » au lieu de « 30 ») — défaut constaté en mise en situation.

**Vérifié : le bug est toujours vivant sur `main`** — 8 occurrences de `.normalize()`
nu dans les gabarits de vente (`vessel.html`, `catalogue.html`, `checkout.html`,
`sale.html`, `rapport.html`, `pdf/onboard_sale_receipt.html`), et le filtre `|qty`
n'existe pas dans `app/templating.py`.

**Recommandation** : ouvrir la PR. C'est un correctif P1 visible par les Opérations,
sur le module que le bord utilise, et il est déjà écrit.

### 9.2 Branche périmée

`fix/git-stabilization` — 120 commits non fusionnés, dernier commit du **2026-06-18**.
Ligne divergente ancienne. Candidate à l'archivage (après confirmation, le CLAUDE.md
interdit la suppression de branche sans approbation).

### 9.3 Dette à surveiller

- **Cliquet mypy** : 358/371, marge de 13 (~3,5 %) au rythme de fusion actuel.
- **`alembic upgrade --sql` est inutilisable** sur ce projet (des migrations inspectent
  la base) : aucune pré-visualisation du DDL avant application — le snapshot est le
  seul filet.
- **Garde-fou CI manquant** : aucune étape n'exécute Alembic. La sentinelle de tête
  unique couvre le cas le plus fréquent, mais pas un `upgrade` réel sur base vierge.

---

## 10. Synthèse — Faits / Hypothèses / Recommandations

**Faits** — `main` porte 1 tête Alembic, #173 en crée 2, #174 en crée 3, et la
sentinelle CI l'attrape ; `main` est rouge aujourd'hui pour deux causes (black sur
2 fichiers, `anyio` non épinglé) qu'**aucune** des 5 PR ne corrige ; #170 est obsolète
(payload no-op prouvé) ; les signaux CI des 5 PR datent d'avant 27 commits et ne valent
plus rien ; #172 fusionne sans perte sémantique (vérifié sur l'arbre fusionné) ; #173
est conforme sur permissions, routage, i18n et conventions ; le mode strict de #174 ne
casse pas sur les archives TOWT ; le bug d'affichage du stock est vivant sur `main`.

**Hypothèses** — `mrv_events` / `mrv_parameters` sont vides en production (affirmé par
l'arbitrage du 2026-09-01, **non couvert par Q1**, `COUNT(*)` jamais produit) ; le pin
`anyio==4.6.2.post1` est validé sur les 4 modules cassants mais pas encore sur la suite
complète en CI.

**Recommandations** — (1) PR `fix/ci-main-vert` **préalable à tout** ; (2) fermer #170 ;
(3) ordre **#172 → #171 corrigée → #173 rechaînée → #174 scindée** ; (4) rechaîner
chaque migration au moment de sa fusion, jamais en avance ; (5) `COUNT(*)` de production
**et** export ciblé avant tout DROP, même à zéro — ou renommage plutôt que suppression ;
(6) ouvrir la PR du correctif de stock orphelin.

**Décisions qui reviennent à Yasmin / Julien** — le DROP legacy MRV (irréversible,
sans ADR) ; le maintien ou non de la redirection `/dashboard-env` ; le choix entre
reformater à `black 24.10.0` ou remonter le pin.

---

## 11. Suites du 2026-09-03 — ce qui a été fait, ce qui reste

> Ajouté en fin de journée. Les sections 1 à 10 décrivent l'état **au matin** et
> restent le compte rendu de l'audit ; cette section dit ce qu'il en est advenu.

### Ce qui a été fusionné

| PR | Objet | Issue |
|---|---|---|
| #170 → #174 | les cinq PR auditées | **toutes fusionnées** — l'ordre et les conditions recommandés n'ont pas été suivis |
| #182 | `hotfix(captain)` — `IndentationError` de la résolution de conflit de #170 | fusionnée |
| #183 | `fix(deploy)` — retour arrière applicatif réel | fusionnée |
| #184 | `fix(alembic)` — révision de fusion `20260903_0139` | fusionnée |
| #185 | `fix(ci)` — `black` + `anyio` épinglé | fusionnée |
| #186 | `fix(mrv)` — renommage au lieu du `DROP`, fusion `20260903_0140`, import mort | fusionnée |

### Cinq déploiements en échec, deux causes

| Heure UTC | Commit | Cause |
|---|---|---|
| 07:22, 07:31, 08:34 | `c31805a`, `93a6e6d` | `IndentationError` — conflit de #170 résolu en gardant les deux `except` |
| 09:28 | `1d480c6` | têtes Alembic multiples (#173 non rechaînée) |
| 10:41 | `1f082f9` | têtes Alembic multiples (#174, troisième enfant de `0136`) |

Aucune donnée perdue : les deux échecs de migration ont restauré le snapshot
sans avoir rien appliqué, et l'image n'a jamais été permutée.

### Arbitrages rendus

- **`DROP` du legacy MRV → renommage** (`*_deprecated_20260903`, PR #186). Le
  « GATE HUMAIN » de la migration n'était qu'une docstring ; le comptage en
  production n'avait jamais été fait. La suppression sèche reste à instruire.
- **Fusion et non rechaînage** pour les deux collisions Alembic : les révisions
  étaient publiées sur `main`, donc rechaîner aurait pu faire manquer des
  tables silencieusement sur une base de développement.

### Reste à faire

1. **Protection de branche sur `main`** — runbook §4.0. C'est la cause commune
   des cinq échecs : les deux défauts avaient été détectés par la CI avant le
   déploiement, et la sentinelle Alembic a signalé les quatre collisions de
   l'histoire du projet sans jamais pouvoir bloquer une fusion.
2. **`COUNT(*)` en production** sur `mrv_events_deprecated_20260903` et
   `mrv_parameters_deprecated_20260903`, consigné, avant toute suppression sèche.
3. **Marqueur de maintenance persistant** — il vit dans `/tmp` du conteneur app,
   donc détruit par `--force-recreate` et inaccessible quand l'app est morte.
   Documenté dans `rollback_app`, non corrigé.
4. **Correctif orphelin `fix/stock-scientific-notation`** — toujours sans PR ;
   le stock de Vente à bord s'affiche encore « 3E+1 » au lieu de « 30 ».
5. **Redirection `/dashboard-env` → `/dashboard-perf`** — absente : les favoris
   des Opérations tombent en 404.
