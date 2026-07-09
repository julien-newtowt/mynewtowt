# Fixtures golden MRV 2025 (LOT 13)

Extraits JSON **compacts** du
`Sample_Dataset_Architecture_Evenementielle_2025.xlsx` (dossier client
« Data Quality - MRV/reference-data-2025/Reconstitution/ », NON versionné
ici — seuls ces extraits le sont). Générés par :

```bash
python scripts/import_mrv_2025.py --xlsx <chemin_du_dataset> \
    --emit-fixtures tests/fixtures/mrv_2025
```

Ne pas éditer les `.json` à la main : régénérer via la commande ci-dessus.

## Contenu

| Fichier | Contenu | Usage |
|---|---|---|
| `voyage_1CLA5.json` | Voyage ANEMOS complet Concarneau→Le Havre (mars 2025) : 1 Departure + 3 Noon + 1 Arrival, relevés compteurs/météo/voilure/cales, ROB départ/arrivée, **+ le soutage BDN 433421 non apparié FLGO** (ligne R24 du journal `Controles_Qualite`, reprise dans `qc_expected`) | test golden `compute_leg` + rejeu QC R24 (fail attendu) |
| `voyage_1EGB5.json` | Voyage ANEMOS Pointe-à-Pitre→Santo Tomás (mai-juin 2025) avec **mouillage Begin/End Anchoring** (332,83 h) + soutage BDN 258663 | test appariement mouillage + `compute_leg` |
| `bunkers_flgo.json` | 2 soutages ANEMOS **appariés** (BDN 2691 ↔ FLGO-ANEMOS-00075, écart 0,11 j ; BDN 3891 ↔ FLGO-ANEMOS-00068, écart 0,55 j) + les 2 lectures FLGO « Received » avec compartiments | test `flgo_sync.flgo_matches_for_bunker` (2/2) + rejeu QC R24 (pass attendu) |

## Format d'un fichier

```jsonc
{
  "_meta":   { "source": "...", "generated_by": "...", "voyage": "1CLA5", "note": "..." },
  "vessel":  { "dataset_vessel_id": "ANEMOS", "code": "1", "name": "Anemos", "imo_number": "9982938" },
  "ports":   [ { "locode", "name", "country", "latitude", "longitude" } ],
  "leg":     { "leg_code", "dep_locode", "arr_locode", "dep_datetime_utc", "arr_datetime_utc" },  // null pour bunkers_flgo
  "events":  [ {
      "dataset_event_id",            // traçabilité vers le xlsx (EVTnnnnn)
      "event_type",                  // noon | departure | arrival | anchoring_begin | anchoring_end
      "datetime_utc",                // ISO 8601 UTC
      "lat_decimal", "lon_decimal",  // décimaux (chaînes → Decimal)
      "position_source", "cargo_mrv_t", "status",   // "valide" (historique vérifié)
      "detail": { ... },             // champs du sous-type (rob_t, vessel_condition,
                                     // cargo_bl_t, drafts / sequence_no, duration_h…)
      "engine_readings":  [ { "engine_role", "running_hours_counter_h", "fuel_counter_l" } ],
      "weather_readings": [ { "slot_time", "tws_kn", "awa_deg", "aws_kn", "sea_state",
                              "sea_direction_deg", "ship_speed_kn" } ],
      "sail_readings":    [ { "slot_time", "j0", "fwd_j1", "fwd_ms", "aft_j1", "aft_ms",
                              "sail_boost_pct", "me_ps_load_pct", "me_sb_load_pct" } ],
      "hold_readings":    [ { "period", "zone", "temp_c", "rh_pct" } ]
  } ],
  "bunkers": [ { "bdn_number", "port_locode", "delivery_datetime_utc", "fuel_type",
                 "mass_t", "status", "allocations": [ { "tank_code", "volume_m3", "density_t_m3" } ] } ],
  "flgo_readings": [ { "action_type", "product_name", "reading_datetime", "total_volume_m3",
                       "total_rob_m3", "remarks",
                       "compartments": [ { "compartment_code", "volume_m3", "mass_t" } ] } ],
  "qc_expected": [ { "regle", "objet", "detail" } ],   // lignes du journal Controles_Qualite
  "expected":  { ... }                                  // valeurs golden (cf. ci-dessous)
}
```

Toutes les grandeurs numériques sont des **chaînes** (fidélité `Decimal`),
tous les horodatages sont **ISO 8601 UTC**.

## Compteurs moteurs : cumuls SYNTHÉTIQUES (reconstruction documentée)

Le dataset source ne contient **pas** de compteurs cumulatifs (contradiction
README/données constatée : `running_hours_cumulative_h` / `fuel_counter_L`
vides sur 100 % des 3 258 lignes ; seuls les deltas périodiques « since last
report » existent). Le modèle cible stocke des compteurs **instantanés** et
recalcule les deltas (`inter_event_compute`). Les fixtures embarquent donc
des cumuls reconstruits par (leg × moteur) :

- base 0 au premier événement du leg ;
- `hours_cum += running_hours_since_last_report_h` à chaque relevé source ;
- `fuel_cum_l += do_consumption_since_last_report_t / 0,845 × 1000`
  (densité = seuil R16 par défaut, la même que `resolve_density` fail-closed) ;
- valeur **reportée telle quelle** (carry-forward) sur les événements sans
  relevé pour ce moteur — un « trou » vaut delta 0, jamais une valeur perdue ;
- `fuel_counter_l` reste `null` pour un moteur sans AUCUN delta carburant
  dans le leg (shaft generators) — rien n'est fabriqué.

Par télescopage, les deltas recalculés par `compute_leg` restituent
exactement les deltas source : Σ(conso recalculée) = Σ(deltas source).

## Bloc `expected` (valeurs golden, dérivation)

Pour les fixtures voyage :

- `conso_me_t` / `conso_ae_t` / `conso_total_t` : **télescopage de la chaîne
  de compteurs reconstruite** — `Σ par moteur (cum(dernier événement) −
  cum(premier événement)) × 0,001 × 0,845`, groupés ME (PME+SME) /
  AE (FWD_GEN+AFT_GEN), shaft generators (`engine_group NULL`) exclus
  (convention dictionnaire §2.1). C'est **exactement** la grandeur que
  `inter_event_compute.compute_leg` recalcule par intervalles.
  Nuance réelle du dataset : si le premier événement chronologique du voyage
  est un Noon **antérieur au Departure** (cas `1EGB5`, noon du 20/05 12:00
  vs départ 15:30), son propre delta « since last report » (conso antérieure
  à la chaîne, héritée du voyage précédent) n'est récupérable par AUCUN
  intervalle — il est donc exclu des attendus (pour `1CLA5`, premier
  événement = Departure sans relevé : Σ deltas source = attendu, vérifié à
  la main : ME = 0,0083+0,54+1,53 = 2,0783 t ; AE = 0,60009+0,42+0,36
  = 1,38009 t) ;
- `rob_departure_t` / `rob_arrival_declared_t` : ROB déclarés des
  PortCallEvent source (référence R14-v2) ;
- `rob_last_calculated_t` = `rob_departure_t −` conso des intervalles
  **postérieurs au Departure** (ancrage de `compute_rob_chain` au premier
  PortCall rencontré ; aucun soutage entre les événements des fixtures :
  les BDN sont livrés à quai AVANT le Departure) — c'est la valeur attendue
  du dernier point de `compute_rob_chain` ;
- `density_t_m3` : densité utilisée pour la reconstruction (0,845) ;
- `events_count` : nombre d'événements du voyage.

Particularités assumées (données source telles quelles) : dans
`voyage_1CLA5`, les noons des 8-9 mars sont datés **après** l'Arrival du
7 mars (rattachés au voyage par leur `voyage_number` dans la source) — la
chaîne d'événements les inclut, les totaux golden aussi ; dans
`voyage_1EGB5`, le premier noon précède le Departure (cf. ci-dessus).

## Loader

```python
from tests.fixtures.mrv_2025.loader import load_voyage

fixture = await load_voyage(db, "1CLA5")     # ou "1EGB5" / "bunkers_flgo"
fixture.leg, fixture.events, fixture.bunkers, fixture.flgo_readings
fixture.expected     # valeurs golden ci-dessus
fixture.qc_expected  # lignes du journal Controles_Qualite correspondantes
```

Idempotent par clés naturelles — deux fixtures partageant le navire ANEMOS
cohabitent dans la même session de test sans doublon.
