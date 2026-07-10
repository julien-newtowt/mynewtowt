# Formulaires de reporting navire — référence de remplissage

> Modèles officiels **TOWT** stockés dans le dépôt et téléchargeables depuis
> l'ERP. Servent de **référence de remplissage** pour les commandants / navires.

| Formulaire | Réf. | Fichier (téléchargeable) | Téléchargement UI |
|---|---|---|---|
| **Noon Report** | CFOTE_05 | `app/static/reference/towt_noon_report_template.xlsx` | Onboard → Navigation (« 📥 Modèle ») et MRV |
| **Carbon Report** | CFOTE_09 | `app/static/reference/towt_carbon_report_template.xlsx` | MRV → en-tête (« 📥 Modèle Carbon Report ») |

URL directe : `/static/reference/towt_noon_report_template.xlsx` ·
`/static/reference/towt_carbon_report_template.xlsx`.

Chaque classeur contient deux onglets : **`Reporting form`** (le formulaire) et
**`Data`** (listes de validation : navires, types de report, fuseaux UTC,
ports, condition navire). Convention de couleur du formulaire : *Fill data* =
à remplir · *Do not fill – auto data* = calculé automatiquement.

---

## Flotte de référence (onglet `Data`)

| Navire | IMO |
|---|---|
| ANEMOS | 9982938 |
| ARTEMIS | 9983798 |
| ATLANTIS | 1094917 |
| ATLAS | 1094929 |
| ARCHIMEDES | 1094931 |
| ASTERIAS | 1094943 |
| ARLES | 1094955 |
| ATHENAIS | 1094967 |

**Types de report** : `Noon report` · `Arrival Report` · `Departure report`.
**Condition navire** : `Laden` · `Ballast` · `Partly laden`.
**Fuseaux** : `UTC-12` … `UTC+12` (saisie heure locale + fuseau ; l'UTC est
recalculé).

---

## 1. Noon Report (CFOTE_05)

Instantané de navigation 24 h, saisi quotidiennement à **12:00 UTC bord**.

### En-tête & position
- Vessel name · Voyage number · Rev · Type of report
- Date · Time (+ fuseau UTC)
- Latitude (deg / min / N-S) · Longitude (deg / min / E-W)

### Voyage
- Previous port UNCODE · Next port UNCODE · Vessel condition
- Deadweight (t) · Draft Fwd (m) · Draft Aft (m) · Trim (m)
- Time / Distance from last report (h / NM) · Speed from last report
- Time / Distance from SOSP (h / NM) · Speed from SOSP
- Distance to go (NM)
- Announced ETA · ETB · **ETA à 7,0 / 7,5 / 8,0 / 8,5 / 9,0 kt** (auto)

### Machine — par moteur (Port ME, Starboard ME, FWD Gen, AFT Gen, Port/Starboard Shaft Gen)
- Running hours (h) · DO Consumption (t) · Running hours D / D-1
- Total consumption (t) · GO Density (t/m³)
- ROB DO (t) · ROB Urée (t) · ROB FW (t) · Production FW (t)

### Météo (lignes horaires)
TWS (kt) · AWA (°) · AWS (kt) · Sea state · Sea direction (°) · Ship speed (kt)

### Voilure (lignes horaires)
J0 · FWD J1 · FWD MS · AFT J1 · AFT MS (ON/OFF) · Sail Boost · ME PS load (%) · ME SB load (%)

### Correspondance avec le modèle applicatif `NoonReport`
Depuis la migration **0038**, le `NoonReport` ERP est **aligné sur le
formulaire officiel** : champs voyage/SOSP/ETA/ROB scalaires + trois tables
filles (machine, météo, voilure). Saisie via Onboard › Navigation (sections
repliables).

| Bloc form | Modèle ERP |
|---|---|
| Date/Time (UTC), Lat/Lon | `recorded_at`, `latitude`, `longitude` |
| Type of report, Vessel condition | `report_type`, `vessel_condition` |
| Previous/Next port (UNCODE) | `previous_port`, `next_port` |
| Deadweight, Draft Fwd/Aft, Trim | `deadweight_t`, `draft_fwd_m`, `draft_aft_m`, `trim_m` |
| Since last report (time/dist/speed) | `time_since_last_h`, `distance_since_last_nm`, `speed_since_last_kn` |
| Since SOSP (time/dist/speed) | `time_since_sosp_h`, `distance_since_sosp_nm`, `speed_since_sosp_kn` |
| Distance to go | `distance_to_go_nm` |
| Announced ETA, ETB | `announced_eta`, `etb` |
| ETA 7,0 / 7,5 / 8,0 / 8,5 / 9,0 kt | `eta_70_kt` … `eta_90_kt` |
| Total consumption, GO Density | `total_consumption_t`, `go_density` |
| ROB DO / Urée / FW, Production FW | `rob_do_t`, `rob_uree_t`, `rob_fw_t`, `production_fw_t` |
| Machine (par moteur) | table `noon_report_engines` (running hours, conso DO, compteurs J/J-1) |
| Météo (relevés 4 h) | table `noon_report_weather` (TWS/AWA/AWS/état mer/dir./vitesse) |
| Voilure (relevés 4 h) | table `noon_report_sails` (J0, FWD/AFT J1/MS, boost, charge ME PS/SB) |

> **Champs historiques conservés** : `sog_avg`, `cog_avg`, `wind_speed_kn`,
> `wind_direction_deg`, `sea_state_bf`, `visibility_nm`, `barometric_hpa`,
> `fuel_consumed_24h_l`, `distance_24h_nm`, `rob_fuel_l` — toujours utilisés
> par la synchro MRV (`services/mrv_sync.ensure_from_noon`).
>
> **Unités** : le formulaire officiel exprime le fuel en **tonnes**
> (`total_consumption_t`, `rob_do_t`) ; les champs historiques `*_l` restent en
> litres (conversion via `go_density`, ~0,86 t/m³).

---

## 2. Carbon Report (CFOTE_09)

Bilan **berth-to-berth** d'un voyage : consommation, émissions et facteurs.

### Résultats globaux
- **Port call operations** : conso ME / AE / TOTAL · Bunkering
- **Emission factor setting** (DO EF, source **MEPC.391(81)**) :
  WtT GHG intensity [gCO₂eq/MJ] · CO₂ [gCO₂/gDO] · CH₄ · N₂O
- **Voyage berth-to-berth** — DO & CO₂ : Total · Per mile · Per tonne ·
  Per tonne·mile · **EU MRV CO₂ per t·nm**
- **MDO warning** : contrôle de cohérence ROB vs transactions/conso

### Voyage data
- Vessel name + IMO · Voyage number
- Departure : port + UNCODE · date/heure (+ UTC) · berth lat/long
- Arrival : idem
- Time & Distance : Distance & Time berth-to-berth · Average speed
- Cargo Data : Cargo Quantity (B/L) · Cargo Quantity (EU MRV)

### Réservoirs & conso moteur
- Fuel tanks (14/15/16/17/Other) : Density · Last arrival ROB · Bunkering ·
  ROB Departure · ROB on Arrival
- Conso selon compteurs moteur : running hours + DO counter [L] à
  l'arrivée/au départ de chaque quai, conso pendant l'escale et pendant la
  traversée (pier-to-pier)

### Correspondance ERP — **calcul automatique par leg**
Depuis la migration **0039**, le Carbon Report est **généré automatiquement**
pour chaque leg (`app/services/carbon.py` → `compute_carbon_for_leg`) :

| Bloc form CFOTE_09 | Source ERP (auto) |
|---|---|
| Consommation DO (ME/AE/total) | somme des noon reports du leg (`total_consumption_t`, sinon moteurs) |
| Distance berth-to-berth | `leg.distance_nm` (haversine) |
| Cargo (B/L) | tonnage des bookings confirmés |
| Facteur CO₂ DO | chaîne `co2.get_do_co2_factor` : `emission_factors` daté/courant (fuel MDO, écran `/admin/emission-factors`) → repli `co2_variables.do_co2_ef` (`/admin/co2`) → constante codée **3,206** tCO₂/tDO (MEPC.391(81)). Chaque étage est lu best-effort ; toute erreur retombe sur l'étage suivant |
| CO₂ total / par mille / par tonne / par t·nm (EU MRV) | calculés et persistés dans `LegKPI` |

- **Vue par leg** : `/mrv/legs/{leg_id}/carbon` (résultats CFOTE_09 calculés) ;
  lien depuis chaque ligne de `/kpi`.
- **KPI auto-alimentés** : `/kpi` calcule le `LegKPI` de **chaque** leg à
  l'ouverture (sauf KPI verrouillé `is_manual`). CO₂ émis + évité, DO consommé,
  intensité t·nm affichés par leg.
- **Exports réglementaires (MRV v2)** : le CSV DNV 18 colonnes est **retiré**
  (lot 14, décision Q3) — remplacé par les datasets **OVDLA** et **OVDBR** déposés
  chez DNV (`/mrv/datasets`, formats `.xlsx` et `.csv`). Les anciennes routes
  `/mrv/export/dnv.csv` et `/mrv/export/carbon-report.*` n'existent plus. Procédure :
  `docs/operations/05-mrv-evenementiel-runbook.md`.

> **MRV v2 (migrations 0096-0105)** : la granularité réservoir-par-réservoir et
> compteur moteur du formulaire CFOTE_05 est désormais **modélisée** — relevés
> `nav_event_engine_readings` (compteurs en litres bruts par moteur), cuves
> `vessel_tanks`, soutages `bunker_operations`. Le Carbon Report généré
> (`report_generation.py`) et le grand livre `emission_ledger` calculent depuis
> ces événements ; le calcul agrégé depuis les noon reports legacy
> (`carbon.compute_carbon_for_leg`) reste le **repli `legacy_noon`** pour les
> voyages sans capture v2. Le xlsx reste la référence de saisie terrain.
> Fonctionnement et règles de gestion :
> `docs/strategy/REGLES_GESTION_DONNEES_EMISSIONS.md`.

---

## Notes d'intégration

- Les modèles sont des **fichiers de référence** (binaire xlsx) versionnés ;
  pour mettre à jour la révision, remplacer le fichier dans
  `app/static/reference/` (garder le même nom) et noter la `Rev` ici.
- Unités : le Noon Report officiel exprime le fuel en **tonnes**, alors que le
  `NoonReport` ERP historise en **litres** (`*_l`). Toute future synchro devra
  convertir via la densité GO (`GO Density`, ~0,86 t/m³).
- Fuseaux : saisir l'heure **locale** + le fuseau ; conserver l'**UTC** comme
  référence canonique (cf. `app/utils/timezones.py`).
