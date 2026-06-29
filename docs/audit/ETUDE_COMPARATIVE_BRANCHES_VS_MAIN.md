# Étude comparative — Branches vs `main` & plan d'action de rattrapage

> **Objet :** comparer l'état réel des branches du dépôt `mynewtowt` à la branche
> **`main`**, et définir un **plan d'action** pour rattraper les écarts de
> fonctionnalités.
> **Date :** 2026‑06‑29. **Méthode :** comparaison **par présence réelle de
> code** (fichiers, migrations, services, tableau de parité) entre `main` et la
> branche de travail, et non par lecture du graphe de commits (lignées
> divergentes, trompeuses).

---

## 1. Périmètre : les branches du dépôt

Le dépôt distant `juliengonde-5g/mynewtowt` ne porte que **deux branches** :

| Branche | Rôle |
|---|---|
| **`main`** | Tronc « historique » — socle V3 + vitrine/veille/analytics |
| **`claude/email-branches-audit-5uw0b2`** | Branche de travail courante — **campagne « Reprise P0/P1 » (PR #50→#100)** + modules avancés + carnet de bord/Anemos |

Les autres branches de l'historique (`claude/zealous-einstein-IA2mp`,
`claude/vigilant-gauss-wozms`, `claude/trusting-darwin-jaoBa`,
`vibe/anemos-carnet-bord-88bf9d`) sont **mergées et supprimées**.

---

## 2. Verdict : la branche de travail est **très en avance** sur `main`

La comparaison de présence réelle est sans ambiguïté :

| Indicateur | `main` | Branche de travail | Écart |
|---|---:|---:|---|
| Migrations Alembic | **29** (jusqu'à `…0022`) | **87** (jusqu'à `…0080`) | **+58** sur la branche |
| Routers | 33 | **41** | **+8 modules** sur la branche |
| Tableau de parité `test_v2_parity.py` | **absent** | présent (47 Ko) | branche only |
| Commits « Reprise … » | **0** | 50 | branche only |

> **La campagne « Reprise P0/P1 » (restauration de la parité staff V2→V3)
> n'existe PAS sur `main`.** Elle vit intégralement sur la branche de travail.
> Symétriquement, `main` ne possède **aucun routeur** que la branche n'aurait
> pas. La branche est donc, à ~99 %, un **sur-ensemble** de `main`.

### 2.1 Ce que la branche a **en plus** de `main` (à faire remonter dans `main`)

**A. Toute la campagne de Reprise P0/P1** (cf.
`RAPPORT_DEPLOIEMENT_REPRISE_P0.md` / `…_P1.md`) — parité staff V2→V3 :

- **Sécurité/intégrité** (SEC‑01..06), **Cargo/BL/Portail** (CARGO‑01..13),
  **Escale** (ESC‑01..07), **Crew** (CREW‑01..08), **MRV** (MRV‑01..07),
  **Commercial** (COM‑01..11), **Onboard/Captain** (ONB‑01..07), **Finance/KPI**
  (FIN‑01..07), **Planning** (PLN‑01/03/04), **Stowage** (STO‑01..09),
  **Admin/Dashboard** (ADM‑01..06), **Tracking** (TRK‑02/03/04), **UX/i18n**
  (UX‑01..05).
- Les **services** correspondants absents de `main` : `finance_rollup`,
  `cargo_excel`, et tout le socle de reprise.
- Le **tableau de parité exécutable** (`tests/regression/test_v2_parity.py`,
  `_PENDING` vide ⇒ parité P0 = 100 %).
- **58 migrations additives** (`…0023` → `…0080`).

**B. 8 modules/routers entiers absents de `main` :**

| Routeur (branche, absent de `main`) | Module |
|---|---|
| `rh_router.py` | **RH / SIRH sédentaires** (`/rh`) — dossiers, contrats, congés, Silae |
| `navigation_router.py` | **Navigation / Performance** (`/performance/navigation`) — météo |
| `onboard_router.py` | **Onboard** (éclatement onboard/captain) |
| `devis_router.py` | **Devis public** (`/devis`) |
| `marad_router.py` | **Sync Marad** (crew lecture seule) |
| `pwa_router.py` | **PWA** (manifest/SW, noon offline) |
| `scenario_router.py` | **Scénarios de planning** (what-if) |
| `carnet_bord_router.py` | **Carnet de bord ANEMOS** (highlights/photos) |

### 2.2 Ce que `main` a **en plus** de la branche (mince delta marketing à reporter)

`main` porte quelques **ajouts vitrine récents** postérieurs au point de fork de
la branche, **absents** de la branche de travail :

| Élément (présent sur `main`, absent sur la branche) | Nature |
|---|---|
| `app/templates/public/recrutement.html` | page **Recrutement** |
| Contenu **« passagers 2027 »** (vitrine_router + `flotte.html`) | offre passagers |
| **Kit presse / actualités** (commit `feat(vitrine): recrutement, kit presse, passagers 2027, actualités`) | contenu marketing |

> Delta **faible et circonscrit** (vitrine publique, contenu marketing), sans
> impact sur l'ERP/staff ni sur le schéma de données.

---

## 3. Lecture : un seul vrai écart structurel

Il n'existe **pas** d'écart fonctionnel *bloquant* dispersé entre les branches.
L'écart est **structurel et asymétrique** :

```
main  ──────────●  socle V3 + vitrine/veille/analytics + (passagers/recrutement/kit presse)
                 \
fork              \
                   ●────────────────────────────●  BRANCHE
                     + Reprise P0/P1 complète       (HEAD, lot 46)
                     + 8 modules (rh, navigation,
                       onboard, devis, marad, pwa,
                       scenario, carnet-bord)
                     + 58 migrations + parité
```

⇒ **`main` est très en retard.** Le rattrapage consiste essentiellement à
**promouvoir la branche de travail vers `main`**, puis à **reporter le mince
delta marketing** de `main` resté en avance (§2.2).

---

## 4. État de parité fonctionnelle (sur la branche de travail)

- **Parité P0 (cœur métier staff) : 100 %** — `_PENDING` vide dans
  `test_v2_parity.py` ; les 12 parcours persona sont rejouables (plus aucun
  persona en NO-GO).
- **Évolutions P1 : largement livrées** (47 lots #50→#100).
- **Résiduels (non bloquants)** : quelques P1/P2 (MRV‑08 bunkering, ADM‑07 écran
  Pipedrive, PLN‑02/05, UX‑06, ESC‑08, CARGO‑14) + **Lot 4 / EVO** (consolidation
  des modules V3-only : `client_invoice` dormant, congés `CrewLeave`/`HrAbsence`
  non unifiés, `erp_scaffold` à nettoyer, veille IA, PWA offline réel). Détail
  par module dans `docs/audit/backlog/*`.

---

## 5. Plan d'action de rattrapage

> Priorisé par risque. Toute reprise respecte la *Definition of Done* du backlog
> (charte Kairos, CSP-strict, `require_permission`, `flush+303`,
> `activity.record`, i18n×5, migration additive, test de non-régression, CI).

### Action A — **Promouvoir la branche de travail vers `main`** (priorité absolue)
- **Pourquoi :** `main` ne contient ni la parité V2→V3, ni 8 modules majeurs, ni
  58 migrations. Toute la valeur récente est hors `main`.
- **Comment :**
  1. **Reporter d'abord** le delta marketing de `main` (§2.2 : recrutement,
     passagers 2027, kit presse, actualités) **sur la branche de travail**
     (cherry-pick ciblé `app/templates/public/*` + `vitrine_router.py`), afin que
     la branche devienne un **vrai sur-ensemble** de `main`.
  2. Lancer **`pytest -q`** + le tableau de parité + CI sur la branche.
  3. **Fusionner la branche dans `main`** (merge ou fast-forward selon la
     politique), en vérifiant l'application **ordonnée des 58 migrations
     additives** (`…0023`→`…0080`) en pré-prod.
  4. Re-tagger / re-générer le rapport de déploiement consolidé.
- **Effort :** M (le gros du travail est déjà fait et testé ; il s'agit
  d'intégration + report du delta marketing). **Risque de conflit :** concentré
  sur `vitrine_router.py` et `app/templates/public/*`.

### Action B — **Lever les résiduels P1** (faible volume)
- MRV‑08, PLN‑02/05, ADM‑07. **Effort :** S–M chacun.

### Action C — **Consolider les modules V3-only (Lot 4 / EVO)**
- Trancher `client_invoice` ; unifier les congés ; nettoyer `erp_scaffold` ;
  brancher la veille IA ; PWA offline réel. **Effort :** M–L (décisions métier
  partiellement actées dans `docs/audit/backlog/ARBITRAGES.md`).

### Action D — **Finitions P2** (itératif, non bloquant)
- UX‑06, ESC‑08, CARGO‑14 + finitions par module.

### Action E — **Gouvernance**
- Corriger `CLAUDE.md` (statuts inexacts — cf. document de référence §12) ;
  ajouter la matrice de tests persona au pipeline ; versionner le contrat d'API
  tracking si des flux Power Automate consomment la lecture GET.

### Séquencement
```
A (promotion branche → main)  ──► bloquant, en premier (report delta marketing puis merge)
B (résiduels P1)              ──► en parallèle, rapide
C (consolidation V3-only)     ──► après A
D (P2) + E (gouvernance)      ──► itératif
```

---

## 6. Synthèse exécutive

- **La branche de travail est ~99 % un sur-ensemble de `main`** : elle porte
  **toute la parité V2→V3 (P0 = 100 %)**, **8 modules absents de `main`**
  (RH/SIRH, Navigation, Onboard, Devis, Marad, PWA, Scénarios, Carnet de bord) et
  **+58 migrations**.
- **`main` est très en retard** ; son seul surplus est un **mince delta vitrine**
  (recrutement, passagers 2027, kit presse, actualités).
- **L'écart est structurel, pas fonctionnel** : aucun persona en NO-GO sur la
  branche. Le rattrapage = **reporter le delta marketing sur la branche, puis
  fusionner la branche dans `main`** (Action A).
- **Résiduels = P1/P2 + consolidation V3-only**, non bloquants.

> **Prochaine étape concrète :** valider la cible (`main` comme tronc) et l'ordre
> de l'Action A, puis exécuter le report du delta marketing + la fusion.
