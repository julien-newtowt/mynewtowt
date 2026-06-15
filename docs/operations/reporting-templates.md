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
| Champ form | `NoonReport` |
|---|---|
| Date/Time (UTC) | `recorded_at` |
| Latitude / Longitude | `latitude` / `longitude` |
| Ship speed / COG | `sog_avg` / `cog_avg` |
| TWS / Sea direction | `wind_speed_kn` / `wind_direction_deg` |
| Sea state | `sea_state_bf` |
| Total consumption | `fuel_consumed_24h_l` (⚠ form en **t**, modèle en **L**) |
| Distance from last report | `distance_24h_nm` |
| ROB DO | `rob_fuel_l` (⚠ form en **t**, modèle en **L**) |
| — | `visibility_nm`, `barometric_hpa` (non couverts par le form) |

> **Écarts connus** (le modèle ERP est un sous-ensemble du form officiel) :
> détail par moteur (running hours / DO par moteur), SOSP, ETA multi-vitesses,
> voilure, urée/FW ne sont pas encore stockés en base. Le form xlsx reste la
> source de saisie exhaustive ; le `NoonReport` ERP en capte l'essentiel pour
> MRV/KPI.

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

### Correspondance ERP — module **MRV** (`/mrv`)
Le Carbon Report ERP est généré par le module MRV (règlement UE 2015/757) :
- Saisie des `MRVEvent` (fuel / distance / cargo) par leg.
- Exports : **DNV CSV** (`/mrv/export/dnv.csv`) et **Carbon Report**
  (`/mrv/export/carbon-report.txt`).
- Facteurs d'émission CO₂ : cf. `app/services/co2.py` + `Co2Variable`
  (admin `/admin/co2`). Le form fixe la source **MEPC.391(81)** comme
  référence des facteurs DO (CO₂/CH₄/N₂O).

> **Écart connu** : la granularité réservoir-par-réservoir et compteur moteur
> du form n'est pas modélisée en base ; le module MRV travaille au niveau
> événement/leg. Le xlsx reste la référence de saisie terrain détaillée.

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
