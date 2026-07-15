<!-- Livrable contractuel du chantier « refonte du reporting environnemental »
     (lot 15). LA SOURCE DE VÉRITÉ EST LE CODE : chaque valeur, formule, route,
     permission et seuil de ce document est vérifié dans les fichiers cités en
     commentaire HTML (<!-- source: ... -->). Version alignée sur les migrations
     0096 → 0105 (lots 1-14). -->
# Règles de gestion des données d'émissions — `mynewtowt` (MRV v2)

> **Document de référence** : fonctionnement et règles de gestion relatives au
> traitement des données d'émissions dans la plateforme MyTOWT. Couvre le
> périmètre livré par la refonte du reporting environnemental (architecture
> événementielle déclarative — 14 lots de code, migrations Alembic
> `0096`→`0105`). Exploitation quotidienne (crons, bascule, dépannage) :
> `docs/operations/05-mrv-evenementiel-runbook.md`.

---

## 1. Principes

### 1.1 Logique déclarative — « une seule saisie »

Le bord **déclare** deux choses, et deux seulement :

- des **événements de navigation** (`nav_events`) : Noon (quotidien en mer),
  Departure, Arrival, Begin/End Anchoring — avec leurs relevés fins (compteurs
  par moteur, météo/voilure par créneau 4 h, températures de cales) ;
  <!-- source: app/models/nav_event.py:65 (EVENT_TYPES), 323-427 (relevés) -->
- des **soutages** (`bunker_operations`) : une Bunker Delivery Note (BDN) par
  livraison, avec la répartition par cuve (`bunker_tank_allocations`).
  <!-- source: app/models/bunker.py -->

**Tout le reste est dérivé, jamais ressaisi** : distance, durée, vitesse,
consommation par deltas de compteurs, ROB chaîné, cargo MRV, rapports (Noon /
Carbon / Stopover), datasets réglementaires OVDLA/OVDBR, KPI et dashboard.
Les calculs vivent dans `services/inter_event_compute.py` (grandeurs physiques)
et `services/emission_ledger.py` (émissions). Le rattachement du soutage à son
voyage est lui-même **calculé** (voyage suivant l'escale de livraison, fenêtre
paramétrable `fenetre_rattachement_bunker_j` = 25 j), jamais saisi en dur.
<!-- source: inter_event_compute.py:1-29 ; bunker.py:13-17 ; validation_engine.py:200 -->

### 1.2 Source de vérité et immuabilité

- **Le magasin d'événements est la source de vérité.** La table
  `voyage_emission_summaries` (matérialisation par voyage) est un **cache
  recalculable, jamais une référence de calcul** : elle est régénérée à chaque
  finalisation/validation d'événement (`refresh_summary`, idempotent).
  <!-- source: voyage_emission_summary.py:1-17 ; emission_ledger.py:545-596 -->
- **Snapshots** : un rapport généré (`env_reports.payload`) et une ligne de
  dataset (`mrv_log_abstract_entries.payload`) sont des **instantanés gelés** ;
  le PDF est rendu depuis le snapshot, jamais recalculé au rendu ; une ligne
  OVDLA/OVDBR déjà figée par une vérification n'est jamais réécrite à la
  régénération. <!-- source: env_report.py:5-9 ; models/mrv_dataset.py:19-23 -->
- **Certificats jamais recalculés** : les `anemos_certificates` émis sont des
  enregistrements immuables ; l'émission d'un nouveau certificat lit le grand
  livre, les certificats existants restent tels quels (suite de non-régression
  `tests/regression/test_emission_nonregression.py`).
- **Legacy gelé** : les `noon_reports` (signés, hash d'intégrité) et
  `mrv_events` historiques sont **gelés en écriture** depuis la bascule
  (lot 14) et conservés en **archive lecture seule** (`/mrv/archive/events`) —
  jamais mutés, jamais supprimés ; ils restent le repli `legacy_noon` du grand
  livre pour les voyages antérieurs à la capture v2.
  <!-- source: mrv_router.py:74-86 ; emission_ledger.py:224-259 -->

### 1.3 Le grand livre unique (règle d'or)

`services/emission_ledger.py` est **le seul endroit du code** où une
consommation est multipliée par un facteur d'émission
(fonction `emissions_breakdown`). Les trois moteurs historiques ont convergé :

| Consommateur | Devenir |
|---|---|
| `carbon.compute_carbon_for_leg` | **adaptateur** du ledger (même dataclass, mêmes arrondis) |
| `anemos.issue_for_booking` (branche `declared`) | lit `emission_ledger.compute_for_leg` |
| KPI / dashboard (`kpi.py`, `kpi_env.py`) | lisent l'adaptateur / les summaries |
| `co2.estimate` (1,5 / 13,7 gCO₂/t·km) | **comparateur officiel** (devis, conventionnel des certificats) — pas une émission réelle |
| `services/emissions.py` (NOx/SOx) | comparateur officiel, hors périmètre carburant |

Garde-fou automatisé : la sentinelle `tests/regression/test_factor_whitelist.py`
échoue si un fichier hors `FACTOR_WHITELIST` référence un jeton de facteur
(`3.206`, `ef_co2_kg_per_kg`, …). Étendre la whitelist exige une justification
d'architecte. <!-- source: emission_ledger.py:1-33,92-134 ; carbon.py:1-20 ; test_factor_whitelist.py:47-96 -->

---

## 2. Cycle de vie de la donnée

### 2.1 Diagramme (texte)

```
        BORD (Master)                                SIÈGE (Env. Manager / DPA)
┌──────────────────────────────┐
│ DÉCLARATION                  │
│  /onboard/events (captain:M) │
│  préremplissage Thalos/SOF   │
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐   autosave (last_saved_at), reprise
│ BROUILLON                    │   RÉSERVÉE À L'AUTEUR (D11 → 403)
│  exclu de TOUT calcul        │   dormant > 24 h → rappel Master (R19)
│                              │   dormant > 48 h → alerte siège (R19 2e seuil)
└──────────────┬───────────────┘
               ▼ finalisation
┌──────────────────────────────┐   datetime_utc CALCULÉ (local+tz) — autoritatif,
│ FINALISÉ                     │   jamais lu du payload ; moteur de règles scope
│  entre dans les calculs      │   « event » : un fail BLOQUANT refuse la
│                              │   finalisation (position manuelle justifiée R05)
└──────────────┬───────────────┘
               ▼ validation (siège, mrv:M)
┌──────────────────────────────┐
│ VALIDÉ                       │──► ligne OVDLA (événement non-Noon)
└──────────────┬───────────────┘
               ▼ génération (jamais de ressaisie)
┌──────────────────────────────┐   Noon / Carbon / Stopover : snapshot payload
│ RAPPORT GÉNÉRÉ (brouillon)   │   + liens N:N vers les événements sources
└──────────────┬───────────────┘
               ▼ validation Master (bord)
┌──────────────────────────────┐   modification post-génération = R18 :
│ VALIDÉ MASTER                │   justification OBLIGATOIRE + statut qualité
└──────────────┬───────────────┘
               ▼ validation siège (CARBON uniquement)
┌──────────────────────────────┐
│ VALIDÉ SIÈGE                 │──► datasets réglementaires OVDLA / OVDBR
└──────────────────────────────┘    (portes qualité, cf. §7.2)
```
<!-- source: event_capture.py (machine à états), env_report.py:50-60 (statuts),
     draft_reminders.py:1-18, report_generation.py -->

### 2.2 Statuts

| Objet | Statuts (code) |
|---|---|
| Événement (`nav_events.status`) | `brouillon` → `finalise` → `valide` <!-- source: nav_event.py:69 --> |
| Soutage (`bunker_operations.status`) | `brouillon` → `valide_master` <!-- source: bunker.py:48 --> |
| Rapport (`env_reports.status`) | `brouillon` → `attente_validation_master` → `valide_master` → `valide_siege` (Carbon seul) <!-- source: env_report.py:50-55 --> |
| Ligne dataset (`verification_status`) | `conform` / `corrected` / `clarified` / `under_conformity` <!-- source: models/mrv_dataset.py:44-49 --> |

Règles de transition (service `event_capture`) :

- seul un **brouillon** est modifiable, et **par son auteur uniquement**
  (garde D11 — `DraftAuthorError`, HTTP 403 à la route). Risque assumé (Q14) :
  Master indisponible ⇒ brouillon bloqué ; l'alerte R19 2ᵉ seuil prévient le
  siège ; <!-- source: event_capture.py:189-210 ; onboard_router.py:1976-1982 -->
- la **finalisation** calcule l'UTC autoritatif et exécute le moteur de règles ;
  tout `fail` **bloquant** la refuse (`EventFinalizationError`), l'événement
  reste brouillon ; <!-- source: event_capture.py:225-268 -->
- un rapport n'est **régénérable** (payload remplacé) qu'aux statuts
  `brouillon` / `attente_validation_master` ; au-delà, immuable.
  <!-- source: env_report.py:57-60 (MUTABLE_STATUSES) -->

### 2.3 Qui fait quoi — matrice réelle (rôles × modules, extraits)

La refonte n'a ajouté **ni rôle ni module** : les fonctions cibles du CDC se
projettent sur la matrice 9 rôles × 17 modules existante (ARC-04 : ajustable
par overrides en base, `/admin/permissions`).
<!-- source: app/permissions.py:73-153 (_MATRIX) -->

| Rôle | `captain` (saisie bord) | `mrv` (hub, validation, seuils) | `kpi` (dashboard p. 1) |
|---|---|---|---|
| `administrateur` | CMS | CMS | CMS |
| `operation` | CM | CM | C |
| `armement` | C | C | C |
| `technique` | CM | CM | C |
| `data_analyst` | C | CM | C |
| `marins` | C | C | C |
| `commercial` | C | — | C |
| `manager_maritime` | CMS | CM | C |
| `rh` | — | — | — |

Application par fonction (mapping Q4, décision actée) :

| Fonction CDC | Chemin réel |
|---|---|
| **Master (bord)** : déclarer/finaliser événements, soutages, valider Master | `captain:M` (`/onboard/events`, `POST /onboard/events` etc.) + garde applicative « auteur seul » sur les brouillons |
| **Environmental Manager / DPA (siège)** : revue, validation siège du Carbon, qualité, datasets | `mrv:C` (revue) / `mrv:M` (validation, génération datasets, acquittements, confirm-reset R10) |
| **Administrateur / QHSE** : seuils, paramètres dashboard, init référentiel | `mrv:S` (`/mrv/parametres`) + écrans `/admin/flotte-env`, `/admin/emission-factors` (module `admin`) |
| **Tout utilisateur** : dashboard page 1 | `kpi:C` (`/dashboard-env`) |

<!-- source: onboard_router.py:1909-1914 (captain:M) ; mrv_router.py:151-157
     (parametres mrv:S), 937-1007 (validations mrv:M) ; dashboard_env_router.py:133
     (kpi:C), 554/592 (mrv:C), 237 (mrv:S) -->

> Note : `operation`, `technique` et `manager_maritime` détiennent `mrv:M` par
> défaut — la validation siège leur est donc accessible. Resserrable **en base**
> (overrides ARC-04) sans changement de code, au choix du client.

---

## 3. Le référentiel

### 3.1 Navires, cuves, moteurs

Chaque navire porte son référentiel (initialisation idempotente par le bouton
`/admin/flotte-env`, seed `ensure_vessel_env_defaults` — un navire s'ajoute
sans code) : <!-- source: referential_env.py:89-141 ; vessel_env.py -->

- **5 cuves** (`vessel_tanks`) : codes `14` / `15` / `16` / `17` / `other`
  (numérotation machine du bord). `capacity_m3` **NULL par conception** tant
  que les plans officiels ne sont pas fournis (Q11) — le proxy « max observé
  FLGO » a été jugé impropre.
- **6 moteurs** (`vessel_engines`) avec **groupe d'agrégation MRV** :

  | Rôle | Groupe | Compté dans les totaux MRV ? |
  |---|---|---|
  | `PME`, `SME` (moteurs principaux) | `ME` | oui |
  | `FWD_GEN`, `AFT_GEN` (groupes électrogènes) | `AE` | oui |
  | `PORT_SHAFT_GEN`, `STBD_SHAFT_GEN` (lignes d'arbre) | `NULL` | **non — exclus** |

- évolution `vessels` : `lightweight_t`, `default_fuel_type` (défaut `MDO`),
  `water_density_default_t_m3` (repli 1,025 t/m³).
  <!-- source: vessel.py:115-129 ; inter_event_compute.py:61 -->

### 3.2 Facteurs d'émission multi-GES versionnés

Table `emission_factors` — **une ligne par carburant** portant 4 grandeurs,
avec **fenêtre de validité** (`valid_from` / `valid_to`) + `is_current`,
**append-only** (modifier = insérer une nouvelle ligne, l'historique n'est
jamais supprimé). Écran `/admin/emission-factors`. Résolution
(`referential_env.resolve_emission_factor`, cache 60 s) : fenêtre couvrant la
date → ligne `is_current` → **repli fail-closed** sur les constantes codées.
<!-- source: emission_factor.py ; referential_env.py:144-309 -->

Valeurs seed / repli du MDO (sourcées) :

| Grandeur | Valeur | Unité | Source |
|---|---|---|---|
| EF CO₂ (TtW) | **3,206** | kg CO₂ / kg fuel (≡ t/t) | MEPC.391(81) |
| EF CH₄ (TtW) | **0,00005** (5·10⁻⁵) | kg CH₄ / kg fuel | CFOTE_09 Rev02 |
| EF N₂O (TtW) | **0,00018** (1,8·10⁻⁴) | kg N₂O / kg fuel | CFOTE_09 Rev02 |
| Intensité WtT | **17,7** | gCO₂eq / MJ | CFOTE_09 Rev02 (FuelEU) |

<!-- source: referential_env.py:151-155 (FALLBACK_*, FALLBACK_SOURCE_REFERENCE) -->

⚠ Le sourcing **formel** de CH₄/N₂O/WtT reste à consolider avant tout usage en
communication externe (Q12, cf. §9). Le composant CO₂ du MDO conserve en outre
la chaîne de compatibilité historique : `emission_factors` →
`co2_variables.do_co2_ef` (`/admin/co2`) → constante 3,206
(`co2.get_do_co2_factor`, signature inchangée). <!-- source: co2.py:40-79 -->

### 3.3 Paramètres du dashboard

Table `dashboard_parameters` (override par navire possible), consommés par la
méthode B et le CO₂ évité du dashboard : <!-- source: validation_engine.py:235-240 -->

| Paramètre | Seed | Unité | Statut |
|---|---|---|---|
| `occupancy_rate_pct` | 70 | % | hypothèse de remplissage |
| `vessel_capacity_ref_t` | 1100 | t | capacité de référence |
| `ef_container_ship_gco2_tkm` | 16 | gCO₂/t·km | **placeholder sectoriel** (Q15) |
| `ef_airfreight_gco2_tkm` | 800 | gCO₂/t·km | **placeholder sectoriel** (Q15) |

### 3.4 Seuils des règles de validation

Table `validation_rule_thresholds` : **aucun seuil métier n'est codé en dur**.
Résolution `get_threshold` : `(règle, navire)` → `(règle, global)` → défaut
codé, **fail-closed** (toute erreur DB retombe sur le défaut), cache 60 s.
Chaque seuil consommé par une règle est **snapshotté** dans le résultat de
contrôle (auditabilité, cf. §6). `provisional=True` marque les valeurs non
confirmées métier (**21 seuils sur 27**, cf. tableau §5 — calibrage prévu après
le voyage pilote, écran `/mrv/parametres` : lecture `mrv:C`, édition `mrv:S`).
<!-- source: validation_engine.py:161-232 (THRESHOLD_SEED), 322-353 (get_threshold) ;
     validation.py:89-133 -->

---

## 4. Les calculs (formules exactes du code)

Convention d'unités (transverse, plan §2.7) : masses **t**, volumes **m³**,
compteurs carburant **litres bruts machine**, densité **t/m³** (≡ kg/L), heures
**h**, distances **nm**, positions **décimales**, temps **local+fuseau saisis →
UTC calculé stocké** (non modifiable). Colonnes suffixées (`_t`, `_m3`, `_l`,
`_h`, `_nm`, `_kn`). <!-- source: nav_event.py:34-37 ; inter_event_compute.py:22 -->

### 4.1 Consommation par deltas de compteurs (CFOTE_05)

Entre deux événements consécutifs **finalisés/validés** (brouillons exclus),
pour chaque moteur : <!-- source: inter_event_compute.py:243-287 -->

```
usage_l  = compteur_l(événement N) − compteur_l(événement N−1)
conso_t  = usage_l × 0,001 × densité          # L → m³ → t
```

- densité = seuil `densite_defaut_t_m3` (R16, défaut **0,845 t/m³**, override
  navire possible) — résolue via `get_threshold`, repli fail-closed ;
- heures moteur : même mécanique de delta sur `running_hours_counter_h`.

### 4.2 Gestion des resets de compteur (R10)

`_counter_usage(prev, cur)` : <!-- source: inter_event_compute.py:221-240 -->

| Cas | Usage retenu | Effet |
|---|---|---|
| delta ≥ 0 | delta | normal |
| delta < 0 **et** reset confirmé (`is_counter_reset` + `reset_confirmed_by` Administrateur) | **valeur aval** (compteur reparti de ~0) | `reset_applied` |
| delta < 0 non confirmé | **None** (anomalie) | conso du moteur indéterminée |

Une anomalie sur un moteur **compté** (ME/AE) rend le total du groupe et le
total de l'intervalle **indéterminés** (None) — et **casse la chaîne ROB en
aval** (§4.4) : le système ne fabrique jamais de valeur.
<!-- source: inter_event_compute.py:343-370, 430-434 -->

### 4.3 Agrégats ME / AE

`ME = PME + SME` ; `AE = FWD_GEN + AFT_GEN` ; les groupes électrogènes de
ligne d'arbre (`engine_group = NULL`) sont **exclus de tous les totaux MRV**.
<!-- source: inter_event_compute.py:64-65 ; referential_env.py:42-49 -->

### 4.4 ROB chaîné

Chaîne calculée vers l'avant, **ancrée sur le premier ROB de référence** — le
`rob_t` d'un PortCall (Departure/Arrival). **Le Noon ne porte jamais de ROB de
référence** (hiérarchie des sources R14-v2). <!-- source: inter_event_compute.py:404-444 ; nav_event.py:247-248 -->

```
ROB(evt) = ROB(précédent) − conso_totale(intervalle) + soutages(intervalle)
```

- soutages = `bunker_operations` **validés Master** dont la livraison tombe
  dans l'intervalle `(from, to]` ; <!-- source: emission_ledger.py:204-221 -->
- le ROB déclaré des PortCall suivants n'est **jamais** ré-injecté dans la
  chaîne : il est conservé à part (`rob_declared_t`) pour le contrôle croisé
  R14 (écart déclaré vs calculé).

### 4.5 Distance, durée, vitesse

```
distance_nm = haversine(position N−1, position N)     # positions décimales
duration_h  = (utc_N − utc_N−1) / 3600
speed_kn    = distance_nm / duration_h                # si durée > 0
```
<!-- source: inter_event_compute.py:293-318, 335-341 -->

### 4.6 Cargo MRV (EU 2016/1928)

`compute_cargo_mrv` sur un PortCall : <!-- source: inter_event_compute.py -->

1. `vessel_condition = "ballast"` ⇒ **cargo MRV = 0** (méthode `ballast_zero`) ;
2. sinon ⇒ **valeur saisie directement par le Master**, `cargo_mrv_t` (méthode
   `declared_fallback`) — décision CDC v0.7 du 09/07/2026 : MyTOWT n'a plus
   vocation à recalculer cette valeur par interpolation hydrostatique (G10,
   table `vessel_hydrostatics` retirée en conséquence).

### 4.7 Assiettes de consommation

| Assiette | Définition (source `events`) |
|---|---|
| **Totale voyage** | Σ des consos d'intervalle du leg |
| **Mouillage** | Σ des intervalles **entièrement contenus** dans une fenêtre Begin→End Anchoring |
| **Hors mouillage** | totale − mouillage — **c'est l'assiette d'émission** (MRV) |
| **Escale** | Arrival (leg N) → Departure (leg N+1) du même navire — formule R14b résolue pour `Consommation_escale` (ROB déclarés + soutages, G12), repli deltas de compteurs (G2) si un ROB manque ; `None` tant que le Departure suivant n'est pas finalisé |

En repli `legacy_noon` (voyage sans événements v2), tout est « hors mouillage » :
l'assiette = Σ `total_consumption_t` des noon reports (sinon Σ moteurs) —
chiffres **identiques** à l'ancien `services.carbon`.
<!-- source: emission_ledger.py:277-300, 398-437 ; report_generation.py:505-545 -->

### 4.8 Émissions multi-GES

`emissions_breakdown(conso_t, facteur)` — l'unique multiplication du code
(assiette = **hors mouillage**) : <!-- source: emission_ledger.py:92-134 -->

```
CO₂ (TtW)  [t]      = conso_t × ef_co2                    # 3,206 (sans dimension t/t)
CH₄ (TtW)  [g]      = conso_t × ef_ch4 × 1 000 000        # tonnes de GES → grammes
N₂O (TtW)  [g]      = conso_t × ef_n2o × 1 000 000
WtT [tCO₂eq]        = conso_t × 42 700 × wtt_gco2eq_per_mj / 1 000 000
                      # PCI MDO = 42 700 MJ/t ; intensité amont 17,7 gCO₂eq/MJ
```

**Le WtT (well-to-tank, FuelEU) est une grandeur DISTINCTE, jamais sommée au
CO₂ TtW.** CH₄/N₂O sont stockés et affichés **en grammes, à part** — jamais
agrégés au CO₂ en tonnes (décision Q12 : calculés et affichés distinctement).
<!-- source: emission_ledger.py:103-104,123 ; voyage_emission_summary.py:53-59 -->

### 4.9 Intensités (adaptateur Carbon, CFOTE_09)

```
co2_per_nm_kg  = co2_t × 1 000 / distance_nm            # kgCO₂ / mille
co2_per_t_kg   = co2_t × 1 000 / cargo_t                # kgCO₂ / tonne
co2_per_tnm_g  = co2_t × 1 000 000 / (cargo_t × distance_nm)   # gCO₂/t·nm (EU MRV)
```
<!-- source: carbon.py:85-89 (quantize 0.001) -->

Conversion milles → kilomètres (EF, CO₂ évité) : `1 nm = 1,852 km`.
<!-- source: co2.py:27 (NM_TO_KM) -->

### 4.10 Méthodes de facteur d'émission A / B / C

`EF (gCO₂/t·km) = co2_t × 1 000 000 / (dénominateur_t × distance_km)` — les
trois méthodes sont calculées séparément et **jamais mélangées** (le sélecteur
`method` est explicite et validé) : <!-- source: emission_ledger.py:73,327-339,453-461 -->

| Méthode | Dénominateur | Notes |
|---|---|---|
| **A** — réel commercial | `cargo_bl_t` (cargaison B/L du Departure ; legacy : Σ bookings actifs) | voyage **ballast (cargo ≤ 0) ⇒ N/A** — exclu du dénominateur ; son CO₂ reste compté dans l'agrégat de période <!-- source: kpi_env.py:14-16 --> |
| **B** — standardisé | `vessel_capacity_ref_t × occupancy_rate_pct / 100` (1100 × 70 %) | ballast **inclus** (hypothèse de remplissage fixe) <!-- source: kpi_env.py:374 --> |
| **C** — réglementaire | `cargo_mrv_t` (deadweight carried EU 2016/1928) | réel dès que le cargo MRV existe (événements) ; N/A sinon |

### 4.11 CO₂ évité (comparateurs)

Délégué au comparateur officiel `co2.estimate` (facteurs versionnés
`co2_variables`, repli constantes) — **mêmes valeurs** que l'existant :

```
km = nm × 1,852 ; tkm = km × tonnage
towt_kg   = tkm × 1,5 / 1000        conv_kg = tkm × 13,7 / 1000
avoided   = conv_kg − towt_kg
```
<!-- source: co2.py:190-215 ; emission_ledger.py:490-510 -->

Le dashboard affiche en outre des comparateurs paramétrables
(`ef_container_ship_gco2_tkm` = 16, `ef_airfreight_gco2_tkm` = 800 —
**références provisoires**, Q15).

### 4.12 Profil de propulsion (tranches 4 h)

Classification de chaque relevé voilure (`nav_event_sail_readings`, créneaux
16:00/20:00/00:00/04:00/08:00/12:00) : <!-- source: kpi_env.py:634,676-689 ; nav_event.py:94 -->

```
voile_on  = j0 ∨ fwd_j1 ∨ fwd_ms ∨ aft_j1 ∨ aft_ms
moteur_on = me_ps_load_pct > 0 ∨ me_sb_load_pct > 0
```

| Catégorie | Condition |
|---|---|
| `velique_pur` | voile ∧ ¬moteur |
| `hybride` | voile ∧ moteur |
| `mecanique` | ¬voile ∧ moteur |
| `statique` | ni voile ni moteur (isolé, jamais confondu avec « vélique ») |

Les **tranches sans relevé sont exclues du dénominateur** (jamais de valeur
fabriquée) ; la **complétude** (tranches renseignées / attendues) est affichée
à côté du profil. <!-- source: kpi_env.py:78-80,189,573-590 -->

---

## 5. Les 31 règles de gestion (R01-R26 + IR01-IR05)

Catalogue seedé en base (`validation_rules`) et exécuté par le moteur
(`validation_engine.run_rules` + `validation_rules_catalog`). Une règle qui ne
trouve pas la donnée qu'elle contrôle **s'abstient** (jamais de faux positif) ;
une exception dans une règle produit un fail « info » technique, jamais un
crash. Certaines règles **graduent leur sévérité par verdict** (colonne
« Sévérité » : défaut, puis graduations réelles).
<!-- source: validation_engine.py:42-156 (RULE_SEED), 736-832 (run_rules) ;
     validation_rules_catalog.py (implémentations) -->

**Déclencheurs** (tous via `run_rules`) : finalisation d'événement (scope
`event`) ; validation Master/siège d'un rapport (`report`) ; validation ou
correction d'un soutage (`bunker`) ; import/sync FLGO (`flgo`) ; **run
nocturne** `POST /api/mrv/quality-run` (event + voyage + inter-rapports, legs
actifs) ; cron `POST /api/mrv/draft-reminders` (R19).
<!-- source: validation_rules_catalog.py:39-46,1290-1393 -->

**Routage des alertes** (`route_alerts`, idempotent — dédup 24 h +
acquittement) : R10 et R24 → `administrateur` ; R14 **critique** et R27 →
`manager_maritime` + `administrateur` ; R19 → auteur du brouillon (1ᵉʳ seuil)
puis `manager_maritime` + `administrateur` (2ᵉ seuil). R28 (warning) n'a pas
de routage dédié — visible via le run de règles standard, comme R08/R09/R11.
<!-- source: validation_rules_catalog.py:1197-1287 ; draft_reminders.py:43 -->

Légende seuils : ° = **provisoire** (`provisional=True`, à calibrer post-pilote).

| ID | Énoncé (comportement réel) | Scope | Sévérité | Seuils consommés (seed) | Alerte |
|---|---|---|---|---|---|
| R01 | Champs obligatoires : identité navire + date présents | event | bloquant | — | — |
| R02 | Voyage rattaché (`leg_id`) ; format `leg_code` 7 car. (1 chiffre + 5 lettres + 1 chiffre) ; cohérence pays du code vs ports réels (cas `1AFRBZ6`) → warning | event | bloquant (format/absence) ; warning (pays) | — | — |
| R03 | Type d'événement présent et ∈ {noon, departure, arrival, anchoring_begin, anchoring_end} | event | bloquant | — | — |
| R04 | Date présente (bloquant) ; horodatage dans le futur au-delà de la tolérance → warning | event | bloquant / warning | `tolerance_datetime_futur_h` = 24° | — |
| R05 | Position dans les bornes physiques (lat ±90, lon ±180) ; position **manuelle** ⇒ justification obligatoire | event | bloquant | — (bornes physiques non paramétrables) | — |
| R06 | ROB de référence aux escales (Departure/Arrival **seulement**) : manquant/négatif → bloquant ; = 0 → warning ; > borne → warning. Le Noon ne porte pas de ROB | event | bloquant / warning | `borne_max_rob_t` = 300 | — |
| R07 | LOCODE des ports du voyage présents et à 5 caractères (1 évaluation/séquence) | event | warning | — | — |
| R08 | Conso négative → bloquant ; nulle sur un Noon → warning ; > cible journalière → warning ; **escale > N jours sans conso ⇒ estimation par défaut TRACÉE** (0,21 t/j) | event | warning (bloquant si négative) | `seuil_conso_ref_l_j` = 750 ; `duree_escale_alerte_conso_manquante_j` = 2° ; `conso_estimee_defaut_t_j` = 0,21° | — |
| R09 | v1 : distance déclarée vs calculée (haversine Thalos) ; v2 : horodatage d'escale vs référence ATD/ETD-ATA/ETA du leg | event | warning | `tolerance_distance_manuelle_nm` = 20° ; `tolerance_datetime_escale_h` = 6° | — |
| R10 | Monotonie des compteurs moteur : régression **non confirmée** → warning routé ; **escalade bloquante** au-delà du délai. Reset **confirmé** par l'Administrateur = nouvelle base | event | warning → bloquant (escalade) | `delai_confirmation_reset_j` = 3° | **administrateur** |
| R11 | Bornes de plausibilité paramétrées (conso journalière, ROB) | event | warning | `seuil_conso_ref_l_j` = 750 ; `borne_max_rob_t` = 300 | — |
| R12 | Copier-coller : ≥ 2 champs de mesure strictement identiques au relevé précédent | event | warning | — (constante structurelle : 2 champs) | — |
| R13 | Chronologie strictement croissante de la séquence (doublon/antériorité → cf. IR01) | event | info | — | — |
| R14 | Continuité ROB (R14a traversée / R14b escale) : ROB déclaré vs **calculé** (chaîne ancrée Departure/Arrival, jamais Noon). Écart classé conforme / mineur / majeur / critique | voyage | bloquant si critique ; warning mineur/majeur | `seuil_rob_ecart_mineur_t` = 0,5° ; `seuil_rob_ecart_majeur_t` = 2° ; `seuil_rob_ecart_critique_t` = 5° | **critique → manager_maritime + administrateur** |
| R15 | Conso voyage vs cible journalière et vs référence FLGO (`CheckConsumption`) si présente | voyage | warning | `seuil_conso_ref_l_j` = 750 ; `densite_defaut_t_m3` = 0,845 ; `seuil_rob_ecart_majeur_t` = 2° | — |
| R16 | Densité BDN dans [défaut − tol, défaut + tol] | bunker | warning | `densite_defaut_t_m3` = 0,845 ; `densite_tolerance_t_m3` = 0,015 | — |
| R17 | ROB déclaré MyTOWT vs ROB FLGO (Marad), jointure par date la plus proche ; **déclassé Info** si l'écart temporel dépasse la tolérance | voyage | warning / info (déclassement) | `tolerance_flgo_ecart_temps_h` = 120° ; `seuil_rob_ecart_mineur_t` = 0,5° ; `densite_defaut_t_m3` | — |
| R18 | Toute modification post-finalisation **justifiée** (pop-up) — une modification sans justification bloque | report | bloquant | — | — |
| R19 | Brouillon dormant : rappel Master au 1ᵉʳ seuil ; alerte siège au 2ᵉ | event | warning | `delai_rappel_brouillon_h` = 24 ; `delai_alerte_siege_brouillon_h` = 48° | **Master (auteur), puis manager_maritime + administrateur** |
| R20 | Cargo MRV ≥ cargaison B/L (voyage chargé) — **Info tant que D10 non résolu** | voyage | info | `seuil_cargo_mrv_ecart_t` = 5° | — |
| R21 | Durée déclarée depuis le dernier rapport vs écart réel entre horodatages | event | warning | `tolerance_duree_rapport_h` = 2° | — |
| R22 | Conso totale du Carbon vs Σ des Noon générés — **le Carbon n'est JAMAIS correcteur** (signale, ne modifie pas) | report | warning | `tolerance_carbon_noon_conso_t` = 1° | — |
| R23 | Σ(volume × densité) des cuves vs masse BDN (warning) ; Σ volumes vs Σ capacités cuves — **Info tant que capacités non officielles (Q11)**, spécifié Bloquant en cible | bunker | warning / info (Q11) | `tolerance_bdn_flgo_t` = 2° ; (`fenetre_rattachement_bunker_j` = 25° pour le rattachement voyage) | — |
| R24 | Chaque soutage BDN a une lecture FLGO « Received » sous N jours (cas réel : BDN 36039 non recoupé) | bunker | warning | `delai_flgo_bunkering_j` = 5° | **administrateur** |
| R25 | Cohérence FLGO, 2 volets — Σ compartiments vs total déclaré (si détail présent) ; progression cohérente entre lectures consécutives. **Signale, ne corrige jamais** | flgo | warning | `tolerance_flgo_interne_m3` = 2° | — |
| R26 | Chaînage : port d'arrivée du voyage N = port de départ du voyage N+1 (même navire) | voyage | warning | — | — |
| R27 | Voyage en cours à la bascule d'année civile (31/12 24:00 UTC) sans événement Cut-off finalisé (G1, CDC v0.7 §9.2/§14.1) | voyage | warning → bloquant (escalade) | `tolerance_cutoff_h` = 24° ; `rappel_cutoff_avant_j` = 7° | **manager_maritime + administrateur** |
| R28 | Distance haversine calculée (entre 2 Noon consécutifs) vs distance loguée par le bord (delta `distance_from_sosp_nm`) — sous-estimation systématique possible en flotte vélique, dégrade artificiellement l'EF_MRV affiché (G4, Matrice §8). Ne corrige jamais la distance utilisée pour Transport Work/EF_MRV | event | warning | `tolerance_distance_haversine_nm` = 20° | — |
| IR01 | Doublon de **jour** + type d'événement dans la séquence | event | bloquant | — | — |
| IR02 | ROB(J) ≈ ROB(J−1) − conso ± soutage : écart > critique → bloquant ; > mineur → warning ; conso inconnue ⇒ abstention (relayé par R14) | event | bloquant / warning | bornes R14 (`mineur` 0,5° / `critique` 5°) ; `densite_defaut_t_m3` | — |
| IR03 | ROB strictement **figé** sur ≥ N relevés consécutifs malgré une conso (cas réel : figé 4 j puis saut −7,6 t) ; conso totalement inconnue = suspect aussi | event | warning | `ir03_min_reports_figes` = 3° ; `ir03_conso_min_t` = 0,05° | — |
| IR04 | Compteur carburant **régressant** sans reset documenté (`is_counter_reset`). Distinct de R10 : IR04 accepte un reset documenté, R10 exige la **confirmation** Administrateur | event | bloquant | — | — |
| IR05 | Position strictement figée sur ≥ N relevés consécutifs **en mer** (Noon) malgré la marche | event | warning | `ir05_min_reports_figes` = 3° | — |

Récapitulatif des seuils : **30 lignes** seedées, dont **24 provisoires** (°)
et 6 confirmées (750 L/j ×2, 24 h, 0,845, 0,015, 300 t).
<!-- source: validation_engine.py -->

### 5.1 Taxonomie qualité transverse

Quatre statuts, portés par `env_field_modifications.resulting_quality_status`
et par le `verification_status` des lignes de dataset :
<!-- source: env_report.py:63-91 ; models/mrv_dataset.py:44-49 -->

| Statut | Sens | Effet |
|---|---|---|
| `conform` | donnée conforme | — |
| `corrected` | corrigée (avec justification R18) | tracé |
| `clarified` | clarifiée (explication sans correction) | tracé |
| `under_conformity` | non-conformité ouverte | **BLOQUE la consolidation dataset** : l'événement/soutage est exclu de l'OVDLA/OVDBR + alerte admin |

Le statut d'un rapport est le **pire** statut de ses modifications
(`worst_quality_status` — `under_conformity` domine).

---

## 6. Traçabilité & audit

- **`quality_check_results`** — journal d'anomalies **append-only** : une ligne
  par verdict de règle (`pass`/`fail`), avec `run_id`, sévérité appliquée,
  message, et surtout le **snapshot des seuils consommés**
  (`details.thresholds_used` : valeur, unité, provenance vessel/global/défaut,
  marqueur provisoire) — un contrôle est **rejouable et opposable** même si le
  seuil a changé depuis. L'**acquittement** (`acknowledged_at/by`, écran
  `/mrv/qualite`, `mrv:M`) stoppe la re-notification ; la ligne reste.
  <!-- source: validation.py:169-222 ; validation_engine.py:263-274,808-822 -->
- **`env_field_modifications`** (R18) — toute donnée pré-remplie ou générée
  puis modifiée est tracée : champ, valeur initiale, valeur corrigée,
  **justification obligatoire** (`justification_text NOT NULL`), auteur,
  horodatage UTC, statut qualité résultant. Double rattachement nullable
  (rapport **ou** événement — la position manuelle R05 s'y range aussi).
  <!-- source: env_report.py:192-225 -->
- **`activity_logs`** — chaque écriture MRV passe par
  `services.activity.record()` (table append-only, viewer
  `/admin/activity-logs`) : création/finalisation d'événement, validation,
  changement de seuil, génération de dataset, confirmation de reset…
- **Signatures & immuabilité** : les `noon_reports` legacy conservent leur
  signature commandant (hash + verrouillage) ; gelés en écriture depuis la
  bascule, consultables en archive (`/mrv/archive/events`, `mrv:C`, lecture
  seule — CRUD retiré, sync éteinte). <!-- source: mrv_router.py:74-86 -->
- **Snapshots de restitution** : payload des rapports (`env_reports.payload`)
  et des lignes de dataset gelés (cf. §1.2) ; `voyage_emission_summaries`
  référence la ligne `emission_factors` appliquée (`factors_ref`) et l'origine
  du calcul (`source` = `events`/`legacy_noon`) + `computed_at`.
  <!-- source: voyage_emission_summary.py:71-79 -->

---

## 7. Sorties

### 7.1 Rapports générés (`env_reports`)

Trois documents générés depuis les événements — **jamais ressaisis** ; PDF
WeasyPrint rendu depuis le snapshot : <!-- source: report_generation.py -->

| Rapport | Généré depuis | Assiette / contenu | Validation |
|---|---|---|---|
| **Noon** (CFOTE_05-like) | un NoonEvent + l'événement précédent | champs de l'événement + intervalle dérivé (distance, durée, vitesse, conso ME/AE/total, soutage, anomalie compteur) | Master |
| **Carbon** (CFOTE_09 v2) | **tous** les événements du leg | totaux voyage, assiette d'émission **hors mouillage**, multi-GES (CO₂ + CH₄/N₂O en g + WtT distinct), intensités, cargo B/L + MRV | Master **puis siège** (`valide_siege`) — R22 signale, ne corrige jamais |
| **Stopover** (nouveau) | Arrival (leg N) → Departure (leg N+1), même navire — rattaché au voyage d'arrivée | durée d'escale, **conso escale par deltas de compteurs**, soutages de l'escale, ROB théorique = ROB arrivée − conso + soutages, **écart vs ROB départ classé** {conforme, mineur, majeur, critique} via les bornes R14 | Master |
<!-- source: report_generation.py:284-357 (noon), 505-545 + 623-640 (stopover) -->

### 7.2 Datasets réglementaires OVDLA / OVDBR

Écran `/mrv/datasets` ; export `.xlsx` (openpyxl) et `.csv`. Remplacent
**intégralement** le CSV DNV 18 colonnes (Q3, retiré au lot 14 ; le 9 col.
mort purgé dès le lot 10). <!-- source: mrv_router.py:1471-1620 ; services/mrv_dataset.py -->

**OVDLA** (*Log Abstract*) — 20 colonnes, en-têtes exacts des échantillons DNV :
`IMO, Date_UTC, Time_UTC, Event, Time_Since_Previous_Report, Distance,
Latitude_North_South, Latitude_Degree, Latitude_Minutes, Longitude_East_West,
Longitude_Degree, Longitude_Minutes, Voyage_From, Voyage_To, Cargo_Mt,
ME_Consumption_MDO, AE_Consumption_MDO, MDO_ROB, Source_System, Last_Updated`.
<!-- source: mrv_dataset.py:72-93 -->

- **1 ligne par événement validé** de type Departure / Arrival / Begin|End
  Anchoring — **les Noon ne produisent pas de ligne** (Q10) : leurs mesures
  sont agrégées dans les deltas ;
- valeurs en **deltas** depuis la ligne précédente (`Time_Since_Previous_Report`,
  `Distance`, consos ME/AE) ; ROB et cargo absolus ; positions **DMS** (converties
  depuis les décimales, `decimal_to_dms`) ;
- `Source_System = "MyTOWT"` (Q10 — l'ancien outil émettait « OVDAdmin ») ;
- une ligne synthétique **« Period last event »** clôt la période de reporting
  pour un voyage encore ouvert (présente à l'export seul, non persistée).
<!-- source: mrv_dataset.py:108-119,446-471,524 ; models/mrv_dataset.py:52 -->

**OVDBR** (*Bunker Report*) — 9 colonnes : `IMO, BDN_Number,
Bunker_Delivery_Date, Bunker_Delivery_Time, Bunker_Port, Fuel_Type, Mass,
Source_System, Last_Updated` — **1 ligne par soutage validé Master**.
<!-- source: mrv_dataset.py:96-106 -->

**Portes de consolidation** (une ligne exclue est listée avec son motif à
l'aperçu, et l'exclusion `under_conformity` **alerte l'admin**) :

| Porte | OVDLA | OVDBR |
|---|---|---|
| Statut du sujet | événement `valide` | soutage `valide_master` |
| Qualité | rapport lié `under_conformity` ⇒ **exclu** ; entrée gelée `under_conformity` ⇒ **exclue** | idem |
<!-- source: mrv_dataset.py:23-29,255-277,350-359 -->

> Écart connu vs spécification : aucune porte « Carbon validé siège » n'est
> imposée à la génération OVDLA (cf. §9).

### 7.3 Certificats Anemos — inchangés dans leur contrat

L'émission (`anemos.issue_for_booking`) garde ses deux méthodes :
**`declared`** quand une consommation réelle existe — désormais lue du **grand
livre** (`emission_ledger.compute_for_leg`, allocation au booking pro-rata
tonnage) — sinon **`theoretical`** (forfait 1,5 g/t·km). `resolve_distance_nm`
(partagé avec le pricing booking et le BL) est **intouché** ; les certificats
émis ne sont **jamais recalculés** ; le compteur public de la landing
(Σ `co2_avoided_kg`) est inchangé (suite de non-régression gelée).
<!-- source: anemos.py:17-19,183-209 ; test_emission_nonregression.py -->

### 7.4 Dashboard Performance Environnementale (`/dashboard-env`)

| Page | Route | Permission | Contenu |
|---|---|---|---|
| 1. Vue flotte | `GET /dashboard-env` | `kpi:C` | CO₂ émis / évité (×2 comparateurs), distance, EF moyen — **sélecteur de méthode A/B/C explicite**, tendance 12 mois |
| 2. Suivi opérationnel | `GET /dashboard-env/vessels/{id}` (`kpi:C`) → `GET /dashboard-env/voyages/{leg_id}` (`mrv:C`) | `kpi:C` / `mrv:C` | drill-down navire → voyage → événements : ROB timeline (sources Departure/Arrival + marqueurs soutage), conso vs cible, répartition ME/AE, **profil de propulsion 4 h** (+ complétude), carte |
| 3. Qualité | `GET /dashboard-env/quality` | `mrv:C` | anomalies par sévérité/règle, resets en attente, soutages non recoupés, complétude |
| 4. Administration | `GET /dashboard-env/parameters` (+ `POST …/{id}/update`) | `mrv:S` | paramètres dashboard (occupancy, capacité, EF comparateurs) |
| Exports | `GET /dashboard-env/voyages/{leg_id}/export.pdf` / `.docx` | `mrv:C` | dossier voyage (WeasyPrint / python-docx) |
<!-- source: dashboard_env_router.py:125-133,233-280,501-663 -->

---

## 8. Données historiques & jeu golden

- **Décision Q1 — démarrage à vide en production.** Aucune donnée historique
  n'est importée en prod. Le dashboard se remplit au fil de la capture v2 ;
  les voyages antérieurs restent servis par le repli `legacy_noon` du ledger.
- **Dataset 2025 = tests et staging uniquement.**
  `scripts/import_mrv_2025.py` (idempotent, `--dry-run` = simulation complète
  puis ROLLBACK, `--database-url` explicite) peuple une base non-prod depuis
  `Sample_Dataset_Architecture_Evenementielle_2025.xlsx` : référentiels
  (ANEMOS/ARTEMIS, facteurs, 12 ports), **28 voyages 2025** créés clôturés
  (un leg existant n'est **jamais** modifié), les événements typés + relevés
  (statut `valide` — **148 des 672 événements**, hors périmètre 2024/2026, sont
  comptés et ignorés), **21 soutages** + allocations, lectures FLGO +
  compartiments, contrôles croisés conso. La feuille `Controles_Qualite` est
  confrontée en mémoire (jamais persistée — le journal QC est reproduit par le
  moteur de règles). Aucun module de `app/` n'importe ce script.
  <!-- source: scripts/import_mrv_2025.py:1-63 -->
- **Réconciliation DNV.** `--reconcile` compare les totaux annuels ANEMOS
  reconstruits aux valeurs du PDF officiel `EmissionReport-ANEMOS-2025`
  (tolérance **±1,5 %**). Résultat constaté à l'exécution du lot 13 (rapport de
  chantier) : **écart +0,127 %** — largement dans la tolérance.
- **Golden OVDLA/OVDBR.** `scripts/golden_ovd_2025.py` régénère les datasets
  depuis les événements importés et les compare **ligne à ligne** aux
  échantillons `08 - DNV MRV Exports`, en ne tolérant QUE `Source_System`
  (Q10 : `MyTOWT` vs `OVDAdmin`) et `Last_Updated`. C'est un **rapport
  d'écart** (code retour 0 — jamais un gate bloquant). Résultat constaté au
  lot 10/13 : **10 lignes OVDBR sur 10** reproduites à l'identique.
  <!-- source: scripts/golden_ovd_2025.py:1-31,47 -->
- **Fixtures golden versionnées** (`tests/fixtures/mrv_2025/` : `voyage_1CLA5`,
  `voyage_1EGB5`, `bunkers_flgo`) — jouées par
  `tests/integration/test_golden_2025.py` : conso du voyage golden exacte au
  centième (**ME 2,0783 t / AE 1,38009 t / total 3,45839 t**), **2/2 soutages
  appariés FLGO**, et le cas réel du **BDN 433421 non recoupé** (R24 le
  signale). <!-- source: test_golden_2025.py:100-102,182-188,191-201,230-244 -->

---

## 9. Limites connues & backlog (honnête)

1. **Capacités de cuves absentes (Q11).** `vessel_tanks.capacity_m3` est NULL
   (plans officiels à obtenir). Conséquence : le volet « capacités » de
   **R23 est en Info** (spécifié Bloquant en cible). Bascule automatique dès
   chargement des données. (Le cargo MRV n'est, lui, plus concerné : il est
   saisi directement par le Master depuis CDC v0.7, G10 — cf. §4.6.)
2. **21 seuils provisoires sur 27** (`provisional=True`) — propositions Q8 à
   calibrer après le voyage pilote (écran `/mrv/parametres`). Les sévérités ont
   été choisies prudentes en attendant.
3. **Distance OVDLA = haversine entre événements** (positions déclarées), pas la
   distance loguée réelle — sous-estimation possible en flotte vélique
   (louvoiement), jamais corrigée automatiquement mais désormais **visible**
   (R28, G4 : écart vs `distance_from_sosp_nm` loguée par le bord). Amélioration
   identifiée au lot 10 : brancher une distance journalisée (log/`voyage_track`).
4. **Sourcing CH₄/N₂O/WtT non formalisé (Q12)** : les valeurs (5·10⁻⁵ /
   1,8·10⁻⁴ / 17,7) reprennent le template CFOTE_09 et le PDF DNV, sans
   référence réglementaire tracée ligne à ligne. À sourcer avant toute
   communication externe. Idem pour les **EF comparateurs dashboard (Q15)**
   (16 / 800 gCO₂/t·km = placeholders sectoriels paramétrables).
5. **Pas d'UI d'audience des feature flags** : l'opt-out par navire
   (`mrv_v2_capture.audience.vessels_off`) se pose en SQL direct (procédure au
   runbook §5) — constat du lot 14.
6. **OVDLA sans porte « Carbon validé siège »** : la génération n'exige que des
   événements `valide` et l'absence d'`under_conformity` — à durcir si DNV
   l'exige (cf. §7.2).
7. **Brouillon auteur-seul assumé (Q14)** : aucun mécanisme de déblocage si le
   Master est indisponible — l'alerte R19 2ᵉ seuil prévient le siège.
8. **Offline PWA limité au NoonEvent (Q13)** : les autres types d'événements se
   saisissent en ligne uniquement (parité de l'acquis offline noon conservée).
9. **`flgo_voyage_consumption_refs` sans import automatisé** : la feuille
   source `CheckConsumption` du dossier client est cassée (`#REF!`) ; R15 ne
   croise cette référence que si elle est peuplée à la main / via l'import 2025.
   <!-- source: flgo.py:24-29 -->
10. **`conso_escale_t` calculée (G12)** : formule R14b résolue pour
    `Consommation_escale` (ROB déclarés + soutages), repli compteurs (G2) si
    un ROB manque. Reste `None` tant que le Departure du leg suivant n'est
    pas finalisé (escale en cours).
    <!-- source: emission_ledger.py -->

---

## Glossaire

| Terme | Définition |
|---|---|
| **AE** | Auxiliary Engines — groupes électrogènes (`FWD_GEN` + `AFT_GEN`), agrégat MRV |
| **BDN** | Bunker Delivery Note — note de livraison d'un soutage (n° unique, propriétés carburant) |
| **Brouillon** | Événement/rapport en cours de saisie — exclu de tout calcul, repris par son auteur seul |
| **Cargo B/L** | Cargaison commerciale au connaissement (Bill of Lading), en tonnes |
| **Cargo MRV** | « Deadweight carried » réglementaire (EU 2016/1928) — saisi directement par le Master (CDC v0.7, G10) |
| **CFOTE_05 / CFOTE_09** | Formulaires officiels TOWT : Noon Report / Carbon Report (cf. `docs/operations/reporting-templates.md`) |
| **DMS** | Degrés-Minutes-Secondes — format de position exigé par l'OVDLA (interne : décimal) |
| **DNV** | Société de classification / vérificateur MRV destinataire des datasets OVDLA/OVDBR |
| **EF** | Emission Factor — facteur d'émission (gCO₂/t·km pour les méthodes A/B/C ; kg/kg pour le TtW) |
| **FLGO** | Fuel/Lube/Gas-Oil — jaugeages de cuves relevés dans Marad (« Measurement » périodique, « Received » au soutage), importés en lecture seule pour rapprochement |
| **FuelEU** | Règlement FuelEU Maritime — d'où la grandeur WtT (intensité amont en gCO₂eq/MJ) |
| **Laden / Ballast** | Navire chargé / sur lest (ballast ⇒ cargo MRV = 0, EF méthode A = N/A) |
| **Leg / Voyage** | Segment port A → port B (`legs`) — le « voyage » MRV EST le Leg existant, consommé jamais recréé |
| **LOCODE** | Code port UN/LOCODE à 5 caractères (contrôlé par R07) |
| **Marad (MaraSoft)** | Logiciel ship-management source des relevés FLGO et des données crew — lecture seule |
| **ME** | Main Engines — moteurs principaux (`PME` + `SME`), agrégat MRV |
| **MEPC.391(81)** | Résolution OMI — source du facteur CO₂ du MDO (3,206) |
| **MDO** | Marine Diesel Oil — carburant unique exploité en V1 (`default_fuel_type`) |
| **MRV** | Monitoring, Reporting, Verification (règlement UE 2015/757) |
| **Noon (Event)** | Événement quotidien en mer (12:00 bord) portant les relevés fins ; ne produit pas de ligne OVDLA |
| **OVDLA** | Dataset DNV *Log Abstract* — 1 ligne par événement validé non-Noon, valeurs en deltas |
| **OVDBR** | Dataset DNV *Bunker Report* — 1 ligne par soutage validé Master |
| **PCI** | Pouvoir Calorifique Inférieur — 42 700 MJ/t pour le MDO (base énergie du WtT) |
| **PME / SME** | Port / Starboard Main Engine (moteur principal bâbord/tribord) |
| **ROB** | Remaining On Board — carburant restant (t). Référence déclarée aux escales (PortCall) uniquement ; chaîné par calcul partout ailleurs |
| **Shaft generator** | Groupe électrogène de ligne d'arbre (`*_SHAFT_GEN`) — hors totaux MRV (`engine_group NULL`) |
| **SOSP / EOSP** | Start / End Of Sea Passage |
| **Soutage** | Avitaillement en carburant (bunkering) — capturé par BDN |
| **Thalos** | Flux de positions satcom (`vessel_positions`) — préremplissage des positions (`thalos_auto`) |
| **TtW** | Tank-to-Wake — émissions de combustion (CO₂/CH₄/N₂O) |
| **Under Conformity** | Pire statut qualité — exclut la ligne de la consolidation dataset + alerte |
| **WtT** | Well-to-Tank — émissions amont du carburant (tCO₂eq, FuelEU) — **jamais sommé au TtW** |

---

*Document généré au lot 15 (documentation) du chantier de refonte — v1.0,
10 juillet 2026. Toute évolution du code fait foi ; merci de maintenir ce
document lors des changements de formule, de seuil seedé ou de workflow.*
