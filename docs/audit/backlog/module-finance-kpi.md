# Module Finance + KPI

Personas : **contrôleur de gestion / finance** + **data analyst / direction**.
Réf V2 : `app/routers/{finance_router,kpi_router}.py`, `app/models/{finance,kpi,emission_parameter,co2_variable}.py`,
`app/templates/{finance,kpi}/*`.
Cible V3 : `app/routers/{finance_router,kpi_router}.py`, `app/models/{finance,kpi,co2_variable}.py`,
`app/services/{finance_rollup,kpi,co2,carbon}.py`, `app/templates/staff/{finance,kpi,analytics}/*`.

---

## Lot 1 — P0

### [FIN-01] Restaurer le suivi Prévisionnel vs Réalisé
- **Persona :** Contrôleur de gestion · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `app/models/finance.py` (`LegFinance` forecast/actual sur 5 postes : CA, portuaire, quai, OPEX mer, opérations + résultat + marge), `finance_router.py`
- **Cible V3 :** `app/models/finance.py` (refondu mono‑valeur), `finance_router.py`, migration
- **Objectif :** le contrôle de gestion a perdu sa fonction première — V3 ne stocke qu'une marge réelle consolidée. Restaurer le couple budget/réel par poste et par leg (selon **A2**).
- **Critères d'acceptation :** double colonne prév/réel sur les postes ; résultat + marge prév/réel ; ligne TOTAL ; calcul d'écart.
- **Dépend de / Arbitrage :** **A2**.
- **Migration :** `LegFinance` forecast/actual et `quay_cost` n'ont pas de cible V3 → reprise à définir avant écrasement.
- **Test de non‑régression :** P8 #1.
- **Effort :** L

### [FIN-02] Export CSV Finance
- **Persona :** Contrôleur de gestion · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `finance_router.py` (`/finance/export/csv`, 18 colonnes prév/réel par leg)
- **Cible V3 :** `finance_router.py`
- **Objectif :** export supprimé → bloque le reporting compta récurrent.
- **Test de non‑régression :** P8 #2.
- **Dépend de :** FIN‑01 (colonnes prév/réel).
- **Effort :** S

### [FIN-03] NOx évité / SOx évité
- **Persona :** Data analyst · **Priorité :** P0 · **Lot :** 1
- **Réf V2 :** `app/models/emission_parameter.py` (facteurs NOx/SOx conv/voile) + `kpi_router.py`
- **Cible V3 :** modèle de paramètres d'émission (recréer/rebrancher), `kpi_router.py`/`app/services/kpi.py`
- **Objectif :** NOx/SOx totalement disparus (modèle `EmissionParameter` supprimé) — indicateurs réglementaires et argument commercial.
- **Critères d'acceptation :** facteurs paramétrables ; NOx/SOx évités calculés + affichés + exportés.
- **Test de non‑régression :** P8 #4.
- **Effort :** M

## Lot 2 — P1

### [FIN-04] Onglet/section Exploitation
- **Persona :** Direction · **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `kpi_router.py` (taux d'activité, écart planning ETD→ATD par leg, vitesse d'exploitation, durée moyenne par route)
- **Cible V3 :** `kpi_router.py` / `staff_dashboard_router.py` (analytics)
- **Objectif :** section disparue (seul on‑time % subsiste).
- **Effort :** M

### [FIN-05] Équivalences CO₂ (vols / containers)
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `emission_parameter.py` (`co2_per_flight_paris_nyc`, `co2_per_container_asia_eu`)
- **Cible V3 :** `app/services/carbon.py`/`co2.py`, KPI + certificat Anemos
- **Objectif :** storytelling RSE (équivalences pédagogiques) disparu du KPI/Carbon/certificat.
- **Test de non‑régression :** P8 #4.
- **Effort :** S

### [FIN-06] Détail exposition assurance en KPI
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `kpi_router.py` (provisions / indemnités / franchises sinistres + contrats)
- **Cible V3 :** `kpi_router.py`, `app/models/insurance.py`, lien claims
- **Objectif :** V3 n'agrège que `claims_cost_eur` ; restaurer le détail provision/indemnité/franchise.
- **Dépend de :** ONB‑06 (détail financier claims).
- **Test de non‑régression :** P8 #3.
- **Effort :** M

### [FIN-07] Vue KPI consolidée
- **Priorité :** P1 · **Lot :** 2
- **Réf V2 :** `kpi_router.py` (dashboard 5 onglets : Ops/Commerce/Env/Finances/Exploitation)
- **Cible V3 :** `kpi_router.py` (1 écran) + `/mrv/carbon` + `/dashboard/analytics`
- **Objectif :** l'information KPI est éclatée sans navigation unifiée. Soit restaurer les onglets, soit créer une page d'entrée data analyst agrégeant les 3 sources.
- **Effort :** M

## Lot 3 — P2

### [FIN-08] Granularité & visualisation
- Histogrammes Commerce (typologie chargeur, format palette, tranche prix) ; productivité
  dockers en KPI ; Letters of Protest ; filtre par route ; flag `accessible` du port ;
  recherche/filtre pays config ports ; réintroduire des **bar‑charts** (V3 = 100 % tables).
- **Priorité :** P2 · **Effort :** M (groupé)

---

## Risques de migration de données
- `LegFinance` forecast/actual + `quay_cost` : **sans cible V3** → définir la reprise (FIN‑01).
- `LegKPI.cargo_tons` (t, Float) → `tonnage_kg` (kg, Decimal) : **×1000**.
- `PortConfig.port_cost_total` → ventiler en `agency_fee_eur` + `pilot_fee_eur` ; `accessible` sans cible.

## Gains V3 à préserver
Auto‑alimentation KPI (bookings+SOF, verrou manuel) · Carbon Report CFOTE_09 (DO/CO₂ réels,
intensités t·nm) · rollup finance FLX‑05 · CRUD OPEX · PortConfig opérationnel (contacts/VHF/
restrictions/jours fermés) · facteurs CO₂ versionnés · coût sinistres dans la marge · Decimal · variance N‑1.
