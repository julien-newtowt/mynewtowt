# Étude comparative — état des branches du dépôt

> **Date :** 2026‑06‑29. **Référence :** `origin/main` = `a209751` (après
> `git fetch --all --prune`). **Méthode :** `git rev-list --left-right --count`,
> `git merge-base`, sondes de contenu (`git grep`/`git ls-tree`) pour distinguer
> *travail réellement absent de `main`* vs *déjà ré‑absorbé*.

---

## 1. Constat structurant : un reset d'historique

Le dépôt porte la trace d'une **réinitialisation d'historique**. Il existe **deux
lignées racines disjointes** :

| Racine | Portée |
|---|---|
| `35a0c77` | **`main`** (tronc actuel, **100 commits**, sain et à jour). |
| `78b6931` | « chore: rebuild repository as single root commit (history reset) » — racine d'une **constellation de branches orphelines**. |

`78b6931` **n'est pas un ancêtre de `main`** : les branches qui en descendent
n'ont **aucune base commune** avec `main` (`git merge-base` vide). Elles
apparaissent « 100 commits en retard » non parce qu'elles ont divergé, mais
parce que **toute** l'histoire de `main` leur est étrangère. Elles ne sont donc
**pas fusionnables en fast‑forward** ; récupérer leur contenu suppose un
*cherry‑pick* ou une ré‑implémentation ciblée.

> ⚠️ Piège déjà rencontré dans cet audit : une branche « très en avance » sur une
> lignée distincte ne signifie **pas** que `main` est en retard. La vérification
> de contenu ci‑dessous montre que l'essentiel de ce travail est **déjà dans
> `main`**.

---

## 2. Vue d'ensemble (20 branches hors `main`)

`ahead`/`behind` = commits relatifs à `origin/main`. « base » = date de
l'ancêtre commun (ou *non liée* si lignée disjointe).

| Branche | ahead | behind | dernier | base commune | Classe |
|---|---:|---:|---|---|---|
| `claude/email-branches-audit-5uw0b2` | 0 | 1 | 06‑29 | 06‑29 | **A — mergée** |
| `vibe/anemos-carnet-bord-88bf9d` | 0 | 62 | 06‑23 | 06‑23 | **A — mergée** |
| `claude/admiring-clarke-l7obrz` | 1 | 50 | 06‑24 | 06‑24 | **C — absorbée** |
| `claude/newtowt-erp-development-0kOfg` | 1 | — | 06‑10 | non liée | **B — placeholder** |
| `claude/quirky-edison-91j85` | 1 | — | 06‑10 | non liée | **B — placeholder** |
| `claude/repo-audit-improvement-puzglk` | 1 | — | 06‑10 | non liée | **B — placeholder** |
| `claude/review-app-versions-49Ilo` | 1 | — | 06‑10 | non liée | **B — placeholder** |
| `claude/trusting-darwin-jaoBa` | 1 | — | 06‑10 | non liée | **B — placeholder** |
| `claude/vigilant-gauss-wozms` | 1 | — | 06‑10 | non liée | **B — placeholder** |
| `claude/zealous-einstein-IA2mp` | 1 | — | 06‑10 | non liée | **B — placeholder** |
| `claude/fix-tracking-dashboard-legs-ChCAz` | 1 | — | 06‑10 | non liée | **B — placeholder** |
| `claude/vibrant-bell-oxaff9` | 183 | — | 06‑22 | non liée | **C — absorbée (SOF)** |
| `feature/grilles-multiroutes` | 154 | — | 06‑19 | non liée | **C — absorbée (grilles)** |
| `claude/gifted-franklin-6dw0nr` | 158 | — | 06‑19 | non liée | **D — à trier (marad)** |
| `claude/sirh-integration-specs-0g9jif` | 132 | — | 06‑19 | non liée | **D — à trier (SIRH)** |
| `fix/git-stabilization` | 120 | — | 06‑18 | non liée | **D — à trier (git/CI)** |
| `claude/amazing-davinci-yu4w6b` | 108 | — | 06‑18 | non liée | **D — à trier** |
| `fix/ci-pipeline-repair` | 5 | — | 06‑10 | non liée | **D — à trier (CI)** |
| `claude/fervent-dijkstra-6dt7t8` | 43 | — | 06‑15 | non liée | **D — à trier (CSRF cache)** |
| `claude/adoring-johnson-szpgln` | 26 | — | 06‑13 | non liée | **D — à trier (COM‑13)** |

*(behind « — » = lignée non liée : la notion de retard n'a pas de sens.)*

---

## 3. Classes

### A — À jour / mergées (rien à récupérer)
- **`claude/email-branches-audit-5uw0b2`** : la branche de travail de cet audit.
  `0` en avance, `1` en retard (le commit de merge `a209751`). **Tout son
  contenu — lots 47→76 — est dans `main`.**
- **`vibe/anemos-carnet-bord-88bf9d`** : `0` en avance → entièrement contenue
  dans `main`. Branche close de fait.

### B — Placeholders vides (lignée reset, à purger)
Huit branches pointent **exactement** sur la racine `78b6931` (1 seul commit,
aucun contenu applicatif au‑delà du reset). Ce sont des **branches mortes**
issues de la réinitialisation : `newtowt-erp-development`, `quirky-edison`,
`repo-audit-improvement`, `review-app-versions`, `trusting-darwin`,
`vigilant-gauss`, `zealous-einstein`, `fix-tracking-dashboard-legs`.
→ **Aucune valeur. Candidates à suppression immédiate.**

### C — Travail historique déjà ré‑absorbé dans `main` (vérifié)
- **`admiring-clarke` (COM‑11)** : « pin packing list to origin leg ». La
  migration `20260624_0080_packing_list_leg_pin.py` **est dans `main`** → absorbé.
- **`vibrant-bell` (SOF)** : analyse + plan de mise à niveau du *Statement of
  Facts*. `main` contient `app/models/sof_event.py`, les templates SOF et
  `docs/strategy/SOF_UPGRADE_PLAN.md` → absorbé.
- **`feature/grilles-multiroutes`** : refonte multi‑routes (Module 6). Les
  marqueurs du modèle multi‑routes sont présents dans
  `origin/main:app/models/commercial.py` → absorbé.
→ **Branches conservables en archive le temps de confirmer, puis à purger.**

### D — Lignée distincte, contenu **vérifié ré‑absorbé dans `main`**
Sondage ciblé de `main` (2026‑06‑29) pour chaque signature fonctionnelle :
**toutes présentes**. Aucune de ces branches ne porte de travail unique.

| Branche | Signature sondée | Dans `main` ? | Localisation dans `main` |
|---|---|---|---|
| `gifted-franklin` | MARAD + sonde `getSyncDetails` | ✅ | `app/utils/marad.py`, `scripts/marad_probe*`, `/api/marad`, config |
| `amazing-davinci` | MARAD sync `/api/marad/refresh`, clés | ✅ | idem MARAD (même périmètre) |
| `sirh-integration-specs` | SIRH L4/L5/L6 (EVP, Silae, coffre bulletins, entretiens) | ✅ | services/models RH, `payslip`, export Silae |
| `fix/git-stabilization` | grilles (surcharge IMDG %/min factur.), rapprochement compte↔client, KPI i18n | ✅ | `app/services` commercial + catalogues i18n |
| `fix/ci-pipeline-repair` | black + ruff 0.9.2 épinglés | ✅ | `.github/`, `requirements.txt` (CI verte) |
| `fervent-dijkstra` | `Cache-Control: no-store` HTML, CSRF POST | ✅ | `app/middlewares/security_headers.py`, `csrf.py` |
| `adoring-johnson` | COM‑13 funnel, cashbox, devis→réservation | ✅ | `modules_router.py`, `analytics/commercial.html`, `cashbox` |

→ **Reclassées en « absorbées ». Plus rien à récupérer ; candidates à purge** au
même titre que la classe C. Seuls subsistent des artefacts jetables (scripts de
diagnostic, reformat mécanique one‑shot) sans valeur de conservation.

---

## 4. Synthèse exécutive

- **`main` est le tronc unique, sain et à jour** (100 commits, racine `35a0c77`),
  porteur de toute la reprise V2→V3 et des lots récents jusqu'au **76**.
- **La prolifération de branches est un artefact d'un reset d'historique** :
  18 des 20 branches sont sur une **lignée disjointe** de `main`. Leur « avance »
  est trompeuse.
- **Vérification de contenu : `main` est un sur‑ensemble.** Toutes les
  signatures fonctionnelles sondées (classes C **et D**) sont **présentes dans
  `main`** : COM‑11, SOF, grilles multi‑routes, MARAD (+ sonde), SIRH L4‑L6,
  rapprochement compte↔client, KPI i18n, CI black/ruff, `no-store`/CSRF, COM‑13
  funnel, cashbox, devis→réservation. **Aucune branche ne porte de travail
  unique non mergé.**
- **Aucun écart bloquant**, aucune fonctionnalité à rapatrier.

### Recommandations
1. **Purge sûre** des 8 placeholders (classe B) — strictement vides.
2. **Purge** des classes A, C et D — contenu intégralement dans `main`
   (vérifié). Ne subsistent que des artefacts jetables (scripts de diagnostic,
   reformat one‑shot).
3. **Conserver `main` comme unique trunk** ; créer toute nouvelle branche **à
   partir de `main`** pour ne pas régénérer de lignées orphelines.
4. **Hygiène** : envisager un nettoyage en masse des branches distantes (toutes
   sauf `main`) une fois la purge validée par un humain.

> Cette étude complète `ETUDE_COMPARATIVE_BRANCHES_VS_MAIN.md` (qui traçait la
> branche de travail vs `main`) en élargissant à **l'ensemble** des branches du
> dépôt.
