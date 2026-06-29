# Étude comparative — Branche de travail vs `main` & plan d'action

> **Objet :** comparer l'état réel de la branche de travail à `main`, et définir
> le plan d'action de continuité.
> **Date :** 2026‑06‑29. **Branche :** `claude/email-branches-audit-5uw0b2`.

> **⚠️ Correctif (2026‑06‑29) :** une première version de ce document concluait à
> des « histoires git non liées » et à un `main` « très en retard (29 migrations,
> 0 commit Reprise) ». **C'était faux** — artefact d'une référence
> `origin/main` **périmée** dans le clone local. Après `git fetch`, la réalité
> est celle décrite ci‑dessous : **une seule lignée**, `main` **à jour** de toute
> la reprise P0/P1.

---

## 1. Périmètre : les branches du dépôt

Deux branches distantes, **partageant la même histoire** (ancêtre commun =
`3b21fe4`, « Reprise P1 — lot 46 ») :

| Branche | État |
|---|---|
| **`main`** | Tronc à jour : V3 + vitrine/veille/analytics **+ toute la campagne Reprise P0/P1** (PR #50→#100, jusqu'au **lot 46**), **87 migrations** (`…0080`), tableau de parité `test_v2_parity.py`. |
| **`claude/email-branches-audit-5uw0b2`** | = `main` **+ 10 commits** de cette session (voir §2). |

> Il n'y a **pas** de divergence de fond : la branche est `main` plus le travail
> récent. La « promotion vers `main` » = **un merge normal de 10 commits** (PR #101).

---

## 2. Ce que la branche ajoute à `main` (le diff de la PR #101)

**33 fichiers, +1325 / −376, 10 commits.** Contenu :

| Lot | Apport |
|---|---|
| (port) | `public/passagers.html` — page **service passagers 2027** (route `/passagers` + nav + i18n `nav_passagers` ×5). |
| **47** | **ADM‑07** — écran `/admin/integrations` (état Pipedrive + test de connexion), audité, i18n ×5. |
| **48** | **MRV‑08** — vue détail leg `/mrv/legs/{id}` (events ligne‑à‑ligne + badges qualité + agrégats bunkering/cargo + report carbone). |
| **49** | **PLN‑02/05** — détail leg : réalisé délégué (ATD/ATA/statut) exposé + badge de dérive (≥ 4 h). |
| **50** | **EVO‑03** — suppression du scaffold mort `erp_scaffold_router` (collisions de routes). |
| **51** | **EVO‑01** (A5) — `/me/invoices` page explicite « facturation hors plateforme » ; **UX‑06** — `.empty-state` + icônes pages 403/404. |
| **52** | **EVO‑06** — correction `CLAUDE.md` (statuts Finance/KPI + section « décisions actées »). |
| (docs) | Document de référence + cette étude comparative. |

Chaque lot fonctionnel est accompagné d'un **test de non‑régression** (ruff vert ;
suite complète validée par la CI — base Postgres non disponible en local).

---

## 3. État de parité fonctionnelle (sur `main` et la branche)

- **Parité P0 (cœur métier staff) : 100 %** — `_PENDING` vide dans
  `test_v2_parity.py` ; les 12 parcours persona rejouables (aucun NO‑GO).
- **Évolutions P1 : intégralement livrées** — PR #50→#100 (sur `main`) + lots
  47‑49 (branche, dans la PR #101).
- **Aucun écart fonctionnel bloquant.** Le seul « écart » entre la branche et
  `main` est le travail récent de cette session (§2), destiné à être mergé.

---

## 4. Plan d'action

### Action A — **Merger la PR #101 dans `main`** — en cours
- PR normale (ancêtre commun `3b21fe4`), **fusionnable** (`mergeable_state` non
  conflictuel), 10 commits. **Mode recommandé :** merge commit (squash possible
  aussi — l'historique est linéaire). Pas d'enjeu d'« histoires non liées ».
- Migrations : aucune nouvelle migration dans ces 10 commits (lots UI/route/doc) —
  rien à appliquer côté schéma.

### Action B — **Résiduels P1** — ✅ **réalisée** (lots 47‑49)
- ADM‑07, MRV‑08, PLN‑02/05.

### Action C — **Consolidation V3‑only (Lot 4 / EVO)** — ⏳ **partielle**
- ✅ EVO‑03 (lot 50), EVO‑01 (lot 51, A5), EVO‑06 (lot 52).
- ⏳ **EVO‑02** (unifier congés `CrewLeave`/`HrAbsence` — migration de schéma),
  **EVO‑04** (veille IA — effort L + Anthropic), **EVO‑05** (PWA offline réel —
  IndexedDB). Reportés (impact schéma / dépendances / effort L).

### Action D — **Finitions P2** — ⏳ **partielle**
- ✅ UX‑06 (lot 51). ⏳ ESC‑08 (cockpit d'escale), CARGO‑14 (confort cargo).

### Action E — **Gouvernance**
- ✅ `CLAUDE.md` corrigé (EVO‑06). ⏳ matrice de tests persona au pipeline ;
  versionner le contrat d'API tracking si des flux Power Automate consomment la
  lecture GET.

---

## 5. Synthèse exécutive

- **Une seule lignée :** `main` est **à jour** de toute la reprise P0/P1
  (jusqu'au lot 46, 87 migrations, parité P0 = 100 %). La branche y ajoute **10
  commits** récents (passagers + lots 47‑52 + docs) — c'est l'objet de la PR #101.
- **Aucun écart fonctionnel bloquant** ; aucun persona en NO‑GO.
- **Reste au backlog (non bloquant) :** EVO‑02 (congés), EVO‑04 (veille IA),
  EVO‑05 (PWA offline), ESC‑08, CARGO‑14. Détail dans `docs/audit/backlog/*`.

> *La version initiale de ce document, basée sur une référence `origin/main`
> périmée, surestimait l'écart entre la branche et `main`. Corrigé après fetch.*
