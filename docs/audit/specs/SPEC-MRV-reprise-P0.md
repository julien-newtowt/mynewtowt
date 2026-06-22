# SPEC d'implémentation — Reprise P0 du module MRV (émissions UE)

**Module :** MRV — Monitoring, Reporting, Verification (Règlement UE 2015/757 — ingestion DNV Veracity)
**Périmètre :** tickets P0 — MRV‑01 (export DNV CSV 18 colonnes + correctif IMO), MRV‑02 (Carbon Report PDF + blocage qualité), MRV‑03 (édition/suppression d'event) ; + P1 couplés par l'arbitrage A1 — MRV‑04 (4 compteurs DO + ME/AE + ROB calculé), MRV‑05 (contrôle qualité multi‑règles bloquant), MRV‑06 (UI paramètres MRV), MRV‑07 (position DMS + auto‑remplissage GPS).
**Arbitrage applicable :** **A1 = HYBRIDE.** On **conserve** la synchro auto V3 (noon report / SOF → `MRVEvent`, idempotente) **et** on réintroduit les 4 compteurs DO de la V2, le calcul ME/AE, le ROB calculé et le contrôle qualité multi‑règles bloquant. Source primaire machine = **compteurs DO** ; le noon report reste **complément + cross‑check** (le ROB déclaré du noon est confronté au ROB calculé).
**Statut :** spécification prête à coder. Respecte la *Definition of Done* du backlog (Kairos/Manrope, CSP‑strict, `require_permission`, `db.flush()`, `services.activity.record()`, i18n 5 langues, migration Alembic, tests). **Enjeu réglementaire** : aucun gain V3 ne doit régresser (cf. §13).

---

## 0. État réel du code V3 (vérifié)

| Élément | Fichier (n° ligne) | Constat |
|---|---|---|
| Modèle event | `app/models/mrv.py` (l.22‑46) | `MRVEvent` **appauvri** : `id, leg_id, event_kind, recorded_at, fuel_type, fuel_volume_l, fuel_mass_t, rob_l, distance_nm, time_at_sea_h, cargo_carried_t, notes, noon_report_id (unique), sof_event_id (unique), quality_status, quality_notes, created_at`. **Absent** : les 4 compteurs DO, `me/ae/total_consumption`, `rob_calculated`, position DMS, `bunkering_qty/date`, `cargo_mrv_t`. |
| Modèle param | `app/models/mrv.py` (l.49‑59) | `MRVParameter(name, value Numeric(12,4), unit, description, updated_at)`. **Lu seulement** (aucune UI d'écriture). |
| Router | `app/routers/mrv_router.py` | `GET /mrv` (l.34), `POST /mrv/legs/{leg_id}/events` (l.85, **create seul**), `GET /mrv/export/dnv.csv` (l.132), `GET /mrv/export/carbon-report.txt` (l.155, **.txt 4 lignes**), `GET /mrv/legs/{leg_id}/carbon` (l.198, HTML par leg). **Aucune route edit/delete, aucune route params, pas de recalc en chaîne.** |
| Export DNV | `app/services/mrv_export.py` (`to_dnv_csv`, l.35‑70) | **9 colonnes** (`vessel_imo, leg_code, event_type, occurred_at_utc, fuel_type, rob_t, consumed_t, co2_t, notes`), délimiteur `;`. **Bug IMO** : `_AdapterMRV.__init__` (router l.182) prend `vessel_imo=""` par défaut et `_decor` (l.194) ne le renseigne JAMAIS → colonne IMO **toujours vide**. `rob_t = rob_l/1000` (heuristique fausse, devrait passer par la densité). |
| Sync auto | `app/services/mrv_sync.py` | `ensure_from_noon` (l.52) + `ensure_from_sof` (l.138) **idempotentes** via `noon_report_id`/`sof_event_id`. `resolve_mdo_density` (l.34) lit `MRVParameter ilike '%mdo_density%'` → fallback `0.845`. `_apply_rob_quality` (l.93) : **1 seule règle** ROB déclaré vs calculé (±2 t), statut **`ok`/`warning` uniquement (jamais `error`)**, appliquée **aux events auto seulement**. |
| Carbon (leg) | `app/services/carbon.py` | `compute_carbon_for_leg(db, leg) -> CarbonResult` (l.98) : conso DO agrégée depuis noon reports, distance `leg.distance_nm`, cargo bookings, intensités /NM, /t, /t·nm + CO₂ évité. **À PRÉSERVER.** |
| Facteur CO₂ | `app/services/co2.py` | `get_do_co2_factor(db)` (l.40) lit `co2_variables` (`do_co2_ef`, écran `/admin/co2`) → fallback `3.206` (MEPC.391(81)). **Versionné — à PRÉSERVER.** Le `mrv_export.CO2_EMISSION_FACTOR_MDO=3.206` est une **constante figée** non versionnée (à aligner). |
| Templates | `app/templates/staff/mrv/index.html`, `carbon_report.html` | `index.html` : stats + table d'events lecture seule + form create. Liens `↓ DNV CSV`, `↓ Carbon Report` (.txt). **Pas de vue détail leg, pas de badges qualité, pas d'édition.** |
| Source IMO | `app/models/vessel.py` (l.25) | `Vessel.imo_number: str | None`. Atteint via `leg.vessel_id`. **C'est la source du correctif MRV‑01.** |
| Position GPS | `app/models/claim.py` (l.136‑145) | `VesselPosition(vessel_id, recorded_at, latitude: float, longitude: float)`. **C'est la source de l'auto‑remplissage MRV‑07.** |
| Leg | `app/models/leg.py` | `leg_code, vessel_id, departure_port_id, arrival_port_id, etd/eta/atd/ata, distance_nm, elongation_coef`. **⚠️ PAS de colonne `year`** (V2 l'avait) → l'année se dérive de `etd`. **⚠️ PAS de `*_port_locode`** (V2 les avait) → résoudre `Port` via `*_port_id` puis lire `Port.locode`/`Port.latitude`/`Port.longitude`. |
| Noon report | `app/models/noon_report.py` | `leg_id, recorded_at, fuel_consumed_24h_l, distance_24h_nm, rob_fuel_l, total_consumption_t`. |

**Référence V2 (à porter)** — `/tmp/oldver/mytowt-main/app/routers/mrv_router.py` :
`compute_consumption` (l.65), `compute_rob` (l.83), `validate_quality` (l.92, 3 règles + statut `error`), `recalculate_all_events` (l.143), `coords_from_port` (l.167) / `coords_from_decimal` (l.183) / `nearest_gps_position` (l.195, fenêtre ±6 h), export DNV **18 colonnes** (`/mrv/export/dnv-csv`, l.683‑782), Carbon Report PDF + blocage HTTP 400 (l.788‑953), `/mrv/params/save` (l.641). Modèle V2 `/tmp/oldver/mytowt-main/app/models/mrv.py` (4 compteurs l.61‑64, DMS l.75‑80, champs calculés l.86‑89, `MRV_DEFAULTS` l.41) + `emission_parameter.py`.

---

## 1. Modèle de données — changements (1 migration Alembic)

### 1.1 `MRVEvent` — colonnes à ajouter (toutes `nullable`, additif, non destructif)

```python
# app/models/mrv.py — classe MRVEvent

# --- [MRV-04 / A1] 4 compteurs DO machine (totalisateurs cumulés, litres) ---
port_me_do_counter: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # Port Main Engine
stbd_me_do_counter: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # Starboard Main Engine
fwd_gen_do_counter: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # FWD Generator
aft_gen_do_counter: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # AFT Generator

# --- [MRV-04 / A1] valeurs calculées (tonnes), persistées pour perf + export ---
me_consumption_t:    Mapped[Decimal | None] = mapped_column(Numeric(10, 4))  # ME (port+stbd)·densité
ae_consumption_t:    Mapped[Decimal | None] = mapped_column(Numeric(10, 4))  # AE (fwd+aft)·densité
total_consumption_t: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))  # ME + AE
rob_calculated_t:    Mapped[Decimal | None] = mapped_column(Numeric(10, 3))  # ROB_prev + bunker − conso

# --- [MRV-04] ROB déclaré (t) + bunkering (departure uniquement) + cargo MRV ---
rob_declared_t:    Mapped[Decimal | None] = mapped_column(Numeric(10, 3))   # ROB machine déclaré (t)
bunkering_qty_t:   Mapped[Decimal | None] = mapped_column(Numeric(10, 3))   # soutage (t)
bunkering_date:    Mapped[date | None]    = mapped_column(Date)
cargo_mrv_t:       Mapped[Decimal | None] = mapped_column(Numeric(10, 2))   # cargo MRV (déplacement − lège)

# --- [MRV-07] position DMS (deg/min entiers + hémisphère) ---
latitude_deg:  Mapped[int | None] = mapped_column(Integer)
latitude_min:  Mapped[int | None] = mapped_column(Integer)
latitude_ns:   Mapped[str | None] = mapped_column(String(1))   # 'N' | 'S'
longitude_deg: Mapped[int | None] = mapped_column(Integer)
longitude_min: Mapped[int | None] = mapped_column(Integer)
longitude_ew:  Mapped[str | None] = mapped_column(String(1))   # 'E' | 'W'
```

> **Conventions conservées (compat V3, ne PAS renommer) :** la PK reste `id`, l'horodatage reste
> `recorded_at` (V2 = `timestamp_utc`), le type reste `event_kind` (V2 = `event_type`), les liens
> `noon_report_id`/`sof_event_id` (uniques, idempotence) et `quality_status`/`quality_notes` sont
> **réutilisés tels quels**. On **n'ajoute pas** de FK `created_by` : la traçabilité passe par
> `activity_logs` (DoD V3). On conserve `fuel_volume_l`/`fuel_mass_t`/`rob_l`/`distance_nm`/
> `cargo_carried_t` (alimentés par la sync noon) ; ils deviennent des **champs de complément**.

### 1.2 `quality_status` — élargir l'énumération applicative
`quality_status` (`String(20)`, déjà présent) accepte désormais **`ok` | `warning` | `error` | `pending`**
(la colonne suffit, aucune migration de type). `error` devient **bloquant** (cf. MRV‑02 / MRV‑05).

### 1.3 `MRVParameter` — aucun changement de schéma
La table convient (`name` unique, `value Numeric`, `unit`, `description`). On standardise **3 noms** de
paramètres (lus/écrits par MRV‑06), avec valeurs par défaut V2 :

| `name` | défaut | `unit` | rôle |
|---|---|---|---|
| `avg_mdo_density` | `0.845` | `t/m³` | densité MDO → conversion compteurs L → t et noon L → t |
| `mdo_admissible_deviation` | `2.0` | `t` | seuil ROB déclaré vs calculé → `error` au‑delà |
| `co2_emission_factor` | `3.206` | `t CO₂/t fuel` | **fallback** si `co2_variables.do_co2_ef` absent (cf. §13) |

> `resolve_mdo_density` (`mrv_sync.py`) matche déjà `ilike '%mdo_density%'` → compatible `avg_mdo_density`.

### 1.4 Migration Alembic
- 1 révision : `ALTER TABLE mrv_events ADD COLUMN ...` pour les **18 colonnes** ci‑dessus (4 compteurs +
  4 calculés + `rob_declared_t`/`bunkering_qty_t`/`bunkering_date`/`cargo_mrv_t` + 6 DMS). Toutes
  nullables → **migration sûre, additive, sans reprise de données** (base V3 récente).
- `upgrade()` : seed des 3 `MRVParameter` (`avg_mdo_density`, `mdo_admissible_deviation`,
  `co2_emission_factor`) en `INSERT ... ON CONFLICT (name) DO NOTHING`.
- `downgrade()` : `DROP COLUMN` symétrique (les paramètres seedés peuvent rester).

---

## 2. MRV‑04 (A1) — Compteurs DO + calcul conso (ME/AE) + ROB calculé

**Cible :** nouveau service `app/services/mrv_quality.py` (logique métier réutilisable, hors router),
modèle (§1.1). On **ne met pas** la logique dans le router (DoD : `services/` réutilisables).

### 2.1 Fonctions (portage V2, typées Decimal)
```python
# app/services/mrv_quality.py
from decimal import Decimal

def compute_consumption(ev, prev, density: Decimal) -> dict[str, Decimal | None]:
    """ME = (Δport_me + Δstbd_me)·densité ; AE = (Δfwd_gen + Δaft_gen)·densité (en tonnes).

    Compteurs en litres ; densité t/m³ → /1000. Premier event du leg : conso None."""
    if prev is None:
        return {"me": None, "ae": None, "total": None}
    def d(cur, pre):
        return ((Decimal(cur or 0) - Decimal(pre or 0)) * density / Decimal("1000"))
    me = d(ev.port_me_do_counter, prev.port_me_do_counter) + d(ev.stbd_me_do_counter, prev.stbd_me_do_counter)
    ae = d(ev.fwd_gen_do_counter, prev.fwd_gen_do_counter) + d(ev.aft_gen_do_counter, prev.aft_gen_do_counter)
    me, ae = me.quantize(Decimal("0.0001")), ae.quantize(Decimal("0.0001"))
    return {"me": me, "ae": ae, "total": (me + ae).quantize(Decimal("0.0001"))}

def compute_rob(prev, ev, total_consumption: Decimal | None) -> Decimal | None:
    """ROB calculé (t) = ROB_calculé_précédent + bunkering − conso. Ancrage = rob_declared_t."""
    if prev is None:
        return ev.rob_declared_t
    base = prev.rob_calculated_t if prev.rob_calculated_t is not None else prev.rob_declared_t
    if base is None:
        return None
    bunker = ev.bunkering_qty_t or Decimal("0")
    cons = total_consumption or Decimal("0")
    return (Decimal(base) + bunker - cons).quantize(Decimal("0.001"))
```

### 2.2 Recalcul en chaîne (idempotent, ordonné)
```python
async def recalculate_leg(db, leg_id: int, params: dict) -> None:
    """Recalcule conso/ROB/qualité de TOUS les events d'un leg, dans l'ordre chronologique.

    Appelée après chaque create/edit/delete et après sync noon/SOF (cf. §6 hybride)."""
    events = (await db.execute(
        select(MRVEvent).where(MRVEvent.leg_id == leg_id)
        .order_by(MRVEvent.recorded_at.asc(), MRVEvent.id.asc())
    )).scalars().all()
    density = Decimal(str(params["avg_mdo_density"]))
    prev = None
    for ev in events:
        cons = compute_consumption(ev, prev, density)
        ev.me_consumption_t = cons["me"]
        ev.ae_consumption_t = cons["ae"]
        ev.total_consumption_t = cons["total"]
        ev.rob_calculated_t = compute_rob(prev, ev, cons["total"])
        ev.quality_status, ev.quality_notes = validate_quality(ev, prev, params)  # §3
        prev = ev
    await db.flush()
```
Helper params : `async def get_mrv_params(db) -> dict` → lit les 3 `MRVParameter`, fallback `MRV_DEFAULTS`.

- **Critère d'acceptation :** saisir 2 events avec compteurs croissants → `me/ae/total_consumption_t`
  et `rob_calculated_t` renseignés sur le 2ᵉ ; modifier le 1ᵉʳ recalcule la chaîne. **NRT : P7 #1.**
- **Persona P7 (data analyst) :** « je saisis les relevés compteurs port/stbd/gen d'une escale →
  la conso ME/AE et le ROB calculé apparaissent sans calcul manuel ».

---

## 3. MRV‑05 (A1) — Contrôle qualité multi‑règles bloquant (tous les events)

**Cible :** `app/services/mrv_quality.py` (`validate_quality`), appelé par `recalculate_leg` (§2.2) **et**
par la sync (§6) → **s'applique à TOUS les events, auto et manuels** (le `_apply_rob_quality` mono‑règle
de `mrv_sync.py` est **remplacé** par cet appel partagé).

```python
def validate_quality(ev, prev, params: dict) -> tuple[str, str]:
    """Retourne (status, notes) ∈ {ok, warning, error}. Portage V2 (3 règles)."""
    if prev is None:
        return ("ok", "Premier event du leg — pas de comparaison.")
    notes, status = [], "ok"

    # Règle 1 — compteurs DO monotones croissants (ERREUR si baisse)
    for field, label in (("port_me_do_counter","Port ME"), ("stbd_me_do_counter","Stbd ME"),
                         ("fwd_gen_do_counter","FWD Gen"), ("aft_gen_do_counter","AFT Gen")):
        cur, pre = getattr(ev, field), getattr(prev, field)
        if cur is not None and pre is not None and cur < pre:
            notes.append(f"ERREUR: compteur {label} en baisse ({pre} → {cur})"); status = "error"

    # Règle 2 — cohérence ROB déclaré vs calculé
    if ev.rob_declared_t is not None and ev.rob_calculated_t is not None:
        dev = abs(Decimal(ev.rob_declared_t) - Decimal(ev.rob_calculated_t))
        adm = Decimal(str(params["mdo_admissible_deviation"]))
        if dev > adm:
            notes.append(f"ERREUR: déviation ROB {dev:.2f} t > seuil {adm} t "
                         f"(déclaré {ev.rob_declared_t}, calculé {ev.rob_calculated_t})"); status = "error"
        elif dev > Decimal("0.5"):
            notes.append(f"ALERTE: déviation ROB {dev:.2f} t")
            status = "warning" if status != "error" else status

    # Règle 3 — cargo constant en transit (departure → arrival/anchoring)
    if prev.cargo_mrv_t is not None and ev.cargo_mrv_t is not None \
       and ev.event_kind in ("arrival", "begin_anchoring", "end_anchoring"):
        if abs(Decimal(ev.cargo_mrv_t) - Decimal(prev.cargo_mrv_t)) > Decimal("0.1"):
            notes.append(f"ALERTE: cargo modifié en transit ({prev.cargo_mrv_t} → {ev.cargo_mrv_t} t)")
            status = "warning" if status != "error" else status

    return (status, " | ".join(notes) or "Contrôles qualité OK")
```

- **Blocage :** un event `quality_status == "error"` **bloque le Carbon Report** (MRV‑02, HTTP 400) et
  est signalé dans l'UI (badge rouge). L'export DNV **n'est pas bloqué** (il doit pouvoir sortir des
  données même imparfaites pour audit), mais l'UI avertit.
- **Critère d'acceptation :** compteur en baisse → `error` ; déviation ROB > seuil → `error` ; cargo
  modifié en transit → `warning`. S'applique aussi à un event saisi manuellement. **NRT : P7 #1, #3.**

---

## 4. MRV‑03 — Édition + suppression d'un event (recalcul en chaîne)

**Cible :** `app/routers/mrv_router.py` (aucune route en V3).

```
POST   /mrv/events/{event_id}/edit     # require_permission("mrv","M")
POST   /mrv/events/{event_id}/delete   # require_permission("mrv","S")
```
- `edit` : `Form(...)` pour `recorded_at`, `event_kind`, les 4 compteurs DO, `rob_declared_t`,
  `cargo_mrv_t`, `bunkering_qty_t`/`bunkering_date` (departure seulement), DMS (6 champs),
  `distance_nm`, `notes`. Parser via helpers `pf`/`pi` (portage V2) tolérants (`,`→`.`, vide→None).
- Après mutation : `await db.flush()` → `recalculate_leg(db, ev.leg_id, params)` → `activity_record(
  action="update", module="mrv", entity_type="mrv_event", entity_id=ev.id, ...)` → `RedirectResponse(303)`
  (ou `HX-Redirect` si `hx-request`).
- `delete` : `await db.delete(ev)` → `db.flush()` → `recalculate_leg` sur le leg restant →
  `activity_record(action="delete", ...)`.
- **Garde :** 404 si event absent ; refuser (409) l'édition des champs **provenant de la sync** ? **Non**
  en P0 : un event issu d'un noon report (`noon_report_id` non nul) reste éditable (la sync est
  idempotente et ne réécrase pas un event existant — cf. §6) ; on documente que la prochaine sync ne
  régénère pas l'event (lien unique déjà présent).
- **Critère d'acceptation :** éditer un event recalcule les dérivés de la chaîne ; suppression idem ;
  audit tracé. **NRT : P7 #1.**

---

## 5. MRV‑01 — Export DNV CSV 18 colonnes (correctif IMO)

**Cible :** `app/services/mrv_export.py` (`to_dnv_csv` réécrit) + route `GET /mrv/export/dnv.csv`.

### 5.1 Les 18 colonnes Veracity (ordre EXACT, depuis le V2 l.717‑723)
| # | En‑tête | Source |
|---|---|---|
| 1 | `IMO` | `vessel.imo_number` (via `leg.vessel_id`) — **correctif** |
| 2 | `Date_UTC` | `recorded_at.strftime("%Y-%m-%d")` |
| 3 | `Time_UTC` | `recorded_at.strftime("%H:%M")` |
| 4 | `Voyage_From` | `Port(leg.departure_port_id).locode` |
| 5 | `Voyage_To` | `Port(leg.arrival_port_id).locode` |
| 6 | `Event` | label DNV (`departure→Departure`, `arrival→Arrival`, `at_sea→At Sea`, `begin_anchoring→Begin Anchoring/Drifting`, `end_anchoring→End Anchoring/Drifting`, sinon `event_kind`) |
| 7 | `Time_Since_Previous_Report` | heures entières depuis l'event précédent (chronologique) |
| 8 | `Distance` | `int(distance_nm or 0)` |
| 9 | `Cargo_Mt` | `round(cargo_mrv_t, 1)` ou vide |
| 10 | `ME_Consumption_MDO` | `round(me_consumption_t, 4)` ou vide |
| 11 | `AE_Consumption_MDO` | `round(ae_consumption_t, 4)` ou vide |
| 12 | `MDO_ROB` | `round(rob_declared_t, 1)` ou vide |
| 13 | `Latitude_Degree` | `latitude_deg` |
| 14 | `Latitude_Minutes` | `latitude_min` |
| 15 | `Latitude_North_South` | `latitude_ns` |
| 16 | `Longitude_Degree` | `longitude_deg` |
| 17 | `Longitude_Minutes` | `longitude_min` |
| 18 | `Longitude_East_West` | `longitude_ew` |

> **Délimiteur** : la V2 utilise `,` (virgule). On conserve `,` (format Veracity attendu) — **ne pas**
> garder le `;` V3. Pas de colonne `co2_t` ni `notes` dans le format Veracity 18 colonnes.

### 5.2 Service réécrit
```python
def to_dnv_csv(rows: Iterable[DnvRow]) -> str:
    """rows = objets enrichis (imo, locodes, labels, compteurs/conso/ROB/DMS). 18 colonnes, délim ','.

    Time_Since_Previous_Report calculé ici en parcourant rows dans l'ordre chronologique."""
```
où `DnvRow` est un petit dataclass/adaptateur construit dans le router à partir de `MRVEvent` + `leg`
+ `vessel` + `pol`/`pod` (remplace `_AdapterMRV`/`_decor` qui ne passaient jamais l'IMO — **cause du bug**).

### 5.3 Route
```
GET /mrv/export/dnv.csv?vessel={code}&year={int}   # require_permission("mrv","C")
```
- Requête : `select(MRVEvent).join(Leg).where(extract('year', Leg.etd) == year)` (⚠️ **pas de `Leg.year`**
  en V3 → `func.extract('year', Leg.etd)`), filtre optionnel `Leg.vessel_id == vessel.id`, tri
  `recorded_at.asc()`. Eager‑load vessel + ports (ou `db.get` mémoïsé par id) pour éviter le lazy‑load.
- **Nom de fichier daté + navire** (portage V2) : `MRV_DNV{_<vessel.name>}_{year}_{YYYYMMDD}.csv`.
- **Critère d'acceptation :** 18 colonnes dans l'ordre Veracity ; **IMO renseigné** ; filtre navire/année ;
  nom de fichier daté + navire. **NRT : P7 #2.**

---

## 6. Articulation hybride compteurs ↔ noon (A1) — la sync ne casse rien

**Principe :** la sync auto V3 (`ensure_from_noon`/`ensure_from_sof`) **reste la source** des events de
phase (departure/arrival/anchoring) et du complément noon ; les **compteurs DO** sont la donnée primaire
saisie/éditée par le data analyst sur ces mêmes events (ou des events manuels intercalés).

Modifications minimales à `app/services/mrv_sync.py` :
1. `_apply_rob_quality` (mono‑règle) **n'est plus appelé** depuis `ensure_from_noon` : on **appelle à la
   place** `recalculate_leg(db, noon.leg_id, params)` **après** `db.flush()` de l'event créé, de sorte que
   `validate_quality` (3 règles) s'applique aussi aux events auto. (On peut conserver `_apply_rob_quality`
   en deprecated ou le supprimer.)
2. `ensure_from_noon` continue d'alimenter `rob_l`/`fuel_volume_l`/`fuel_mass_t`/`distance_nm` (complément
   noon) ; il renseigne en plus `rob_declared_t = round(rob_l/1000 * densité, 3)` **si** le noon n'a pas de
   ROB machine direct, pour nourrir le cross‑check ROB (règle 2). L'idempotence (lien unique
   `noon_report_id`/`sof_event_id`) **est préservée** : un event déjà lié n'est jamais recréé ni écrasé.
3. `ensure_from_sof` inchangé (crée l'event de phase) + déclenche `recalculate_leg` best‑effort.

> **Best‑effort obligatoire** (donnée réglementaire) : un échec de `recalculate_leg` dans le contexte
> bord (noon/SOF) **ne doit jamais** faire échouer l'action du commandant — `try/except` + `logger`
> comme aujourd'hui.

- **Critère d'acceptation :** un noon report signé génère toujours son event (idempotent) ET déclenche le
  recalcul qualité multi‑règles ; saisir des compteurs sur cet event ne crée pas de doublon à la prochaine
  sync.

---

## 7. MRV‑02 — Carbon Report PDF (WeasyPrint) + blocage qualité

**Cible :** route `GET /mrv/export/carbon-report.pdf` (remplace le `.txt`), service
`app/services/pdf_generator.py` (`render_mrv_carbon_report`) + template `templates/pdf/mrv_carbon_report.html`.

> **PDF = WeasyPrint** (DoD V3), **pas ReportLab** (le V2 utilisait ReportLab — on le **réécrit** en
> WeasyPrint via `_render_pdf(template, ctx) -> (html, pdf)` comme tous les PDF V3 : BL, SOF, invoice…).

### 7.1 Route
```
GET /mrv/export/carbon-report.pdf?vessel={code}&year={int}   # require_permission("mrv","C")
```
```python
events = ...  # même requête que §5.3 (join Leg, extract year, filtre navire), tri recorded_at.asc()
errors = [e for e in events if e.quality_status == "error"]
if errors:
    raise HTTPException(400, f"Carbon Report bloqué : {len(errors)} event(s) en erreur qualité. "
                             "Corrigez les données avant de générer le rapport.")
factor = await get_do_co2_factor(db)         # ⚠️ facteur VERSIONNÉ /admin/co2 (pas la constante figée)
params = await get_mrv_params(db)
summary = {
  "total_me_t": sum(e.me_consumption_t or 0 for e in events),
  "total_ae_t": sum(e.ae_consumption_t or 0 for e in events),
  "total_t":    sum(e.total_consumption_t or 0 for e in events),
}
summary["total_co2_t"] = summary["total_t"] * factor
html, pdf = render_mrv_carbon_report(events=events, summary=summary, factor=factor,
                                     density=params["avg_mdo_density"], vessel=vessel, year=year)
return Response(pdf, media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="Carbon_Report{vs}_{year}.pdf"'})
```

### 7.2 Template `templates/pdf/mrv_carbon_report.html` (étend `pdf/_base.html`)
- **En‑tête** : titre « Carbon Report — MRV », navire + IMO + année.
- **Bloc résumé** (table) : Total ME (t), Total AE (t), Total MDO (t), Total CO₂ (t),
  facteur CO₂ (`{{ factor }} t CO₂/t fuel`), densité MDO (`{{ density }} t/m³`).
- **Table d'events** : Date UTC, Heure, Event, Voyage (`pol→pod`), Cargo (t), ME (t), AE (t), Total (t),
  ROB (t), CO₂ (t), Qualité (badge ok/warning/error).
- **Pied** : date de génération + mention « TOWT — MRV Carbon Report » + facteur source (versionné).
- Style **Kairos PDF** (teal `--teal`, Manrope) ; **aucun `<script>` inline**.

> **Distinction à préserver** : la vue HTML par leg (`GET /mrv/legs/{leg_id}/carbon`, `carbon_report.html`,
> intensités /NM /t /t·nm + CO₂ évité via `compute_carbon_for_leg`) **reste** — c'est un gain V3. Le PDF
> MRV‑02 est l'**agrégat annuel multi‑legs** par navire (DNV/audit), complémentaire.

- **Critère d'acceptation :** PDF (résumé ME/AE/total/CO₂/facteur/densité + table d'events) ; filtre
  navire/année ; **génération bloquée (400) si ≥1 event en erreur qualité**. **NRT : P7 #3.**

---

## 8. MRV‑06 — UI d'édition des paramètres MRV

**Cible :** route `POST /mrv/params/save` + section dans `staff/mrv/index.html` (ou `/admin`).

```
POST /mrv/params/save                # require_permission("mrv","S")  (réglage = action sensible)
  Form: avg_mdo_density, mdo_admissible_deviation, co2_emission_factor
```
- Upsert des 3 `MRVParameter` (get‑or‑create par `name`) → `db.flush()` → `activity_record(
  action="update", entity_type="mrv_parameter", ...)` → `RedirectResponse("/mrv", 303)`.
- Après changement de densité/seuil : déclencher `recalculate_leg` n'est pas nécessaire globalement en P0
  (recalcul au prochain edit/sync) ; **option** : bouton « Recalculer tous les legs de l'année » (P2).
- **UI** : `<fieldset>` Kairos « Paramètres MRV » sur `index.html` (densité MDO, seuil déviation ROB,
  facteur CO₂), valeurs pré‑remplies depuis `get_mrv_params`. **Aucun `<script>` inline** (form standard).
- **Critère d'acceptation :** régler densité + seuil + facteur depuis l'UI, persistance + audit. **NRT : P7 #4.**

> **Articulation facteur CO₂ :** `co2_emission_factor` (MRVParameter) reste un **fallback** ; la source de
> vérité du facteur DO demeure `co2_variables.do_co2_ef` (`/admin/co2`, versionné, `get_do_co2_factor`).
> Documenter dans l'UI : « valeur de repli si non défini dans /admin/co2 ».

---

## 9. MRV‑07 — Position DMS + auto‑remplissage GPS

**Cible :** modèle DMS (§1.1), helpers `app/services/mrv_quality.py` (ou `mrv_geo.py`), routes create/edit.

### 9.1 Helpers (portage V2, adapté aux modèles V3)
```python
def coords_from_decimal(lat: float, lon: float) -> dict:
    return {"latitude_deg": int(abs(lat)), "latitude_min": int((abs(lat) - int(abs(lat))) * 60),
            "latitude_ns": "N" if lat >= 0 else "S",
            "longitude_deg": int(abs(lon)), "longitude_min": int((abs(lon) - int(abs(lon))) * 60),
            "longitude_ew": "E" if lon >= 0 else "W"}

def coords_from_port(port) -> dict | None:        # port résolu via leg.*_port_id → Port.latitude/longitude
    if port and port.latitude is not None and port.longitude is not None:
        return coords_from_decimal(port.latitude, port.longitude)
    return None

async def nearest_gps_position(db, vessel_id: int, ts: datetime) -> dict | None:
    """VesselPosition (app/models/claim.py) la plus proche de ts dans ±6 h → DMS, sinon None."""
    window = timedelta(hours=6)
    pos = (await db.execute(
        select(VesselPosition).where(
            VesselPosition.vessel_id == vessel_id,
            VesselPosition.recorded_at >= ts - window,
            VesselPosition.recorded_at <= ts + window,
        ).order_by(func.abs(func.extract("epoch", VesselPosition.recorded_at - ts))).limit(1)
    )).scalar_one_or_none()
    return coords_from_decimal(pos.latitude, pos.longitude) if pos else None
```

### 9.2 Auto‑remplissage à la création (et édition si DMS vide)
Si l'utilisateur ne saisit pas la latitude :
1. event de phase `departure`/`arrival` → coords du **port** (`leg.departure_port_id` resp.
   `arrival_port_id` → `Port`) ;
2. sinon → `nearest_gps_position(db, leg.vessel_id, recorded_at)` (fenêtre ±6 h) ;
3. sinon → champs DMS laissés `None` (saisie manuelle possible).

- **Critère d'acceptation :** créer un event departure auto‑remplit la position du port ; un event
  intermédiaire auto‑remplit la position GPS la plus proche ; saisie manuelle DMS possible et exportée
  (colonnes 13‑18 du DNV CSV). **NRT : P7 #5.** Dépend de MRV‑01 (colonnes DMS de l'export).

---

## 10. Templates — récapitulatif

| Template | Action |
|---|---|
| `staff/mrv/index.html` | lien export → `dnv.csv` (avec `?vessel&year`) + **`carbon-report.pdf`** (remplace `.txt`) ; form create enrichi (4 compteurs DO, `rob_declared_t`, `cargo_mrv_t`, bunkering, **DMS 6 champs**) ; badges qualité (ok/warning/error) ; `<fieldset>` paramètres MRV (MRV‑06) |
| `staff/mrv/_event_form.html` | **nouveau** (fragment create/edit event, HTMX `hx-get`/`hx-post`, pré‑rempli pour l'édition) |
| `staff/mrv/leg_detail.html` | **nouveau** (optionnel P0/P2) — table d'events ligne‑à‑ligne + boutons ✎/🗑 + badges qualité + ROB calculé/déclaré |
| `staff/mrv/carbon_report.html` | **inchangé** (vue HTML par leg — gain V3 à préserver) |
| `pdf/mrv_carbon_report.html` | **nouveau** (WeasyPrint, étend `pdf/_base.html`) |

Contraintes : classes **Kairos**, police **Manrope**, **aucun `<script>` inline** (édition via HTMX
fragments + `forms.js` anti‑double‑submit / confirmation suppression). Badges qualité via `.badge` Kairos
(`badge-success`/`badge-warning`/`badge-danger`).

---

## 11. i18n

Ajouter aux 5 catalogues `app/i18n/{fr,en,es,pt_br,vi}.py` les clés des nouveaux libellés :
compteurs DO (Port ME / Stbd ME / FWD Gen / AFT Gen), conso ME/AE/total, ROB déclaré / ROB calculé,
déviation ROB, cargo MRV, soutage (qté/date), position (deg/min/N‑S/E‑W), statut qualité
(ok/avertissement/erreur), paramètres MRV (densité MDO, seuil de déviation, facteur CO₂), boutons
éditer/supprimer/recalculer, export DNV CSV, Carbon Report PDF, message de blocage qualité.

---

## 12. Tests

**Unitaires (`tests/unit`)**
- `compute_consumption` : ME/AE = Δcompteurs·densité/1000 ; premier event → None ; densité paramétrable.
- `compute_rob` : `prev_rob + bunker − conso` ; ancrage `rob_declared_t` au 1ᵉʳ event ; chaîne.
- `validate_quality` : règle 1 (compteur en baisse → `error`) ; règle 2 (déviation > seuil → `error`,
  0.5 < dév ≤ seuil → `warning`) ; règle 3 (cargo modifié en transit → `warning`) ; 1ᵉʳ event → `ok`.
- `coords_from_decimal` / `coords_from_port` : conversion DMS, hémisphères N/S/E/W.
- `to_dnv_csv` : **18 colonnes dans l'ordre exact**, en‑têtes nommés, délimiteur `,`, **IMO renseigné**,
  `Time_Since_Previous_Report` en heures.

**Intégration (`tests/integration`)**
- Create event (compteurs) → edit → delete : recalcul en chaîne vérifié à chaque étape ; audit tracé.
- `nearest_gps_position` : insérer une `VesselPosition` à +2 h → auto‑remplissage DMS ; aucune dans ±6 h → None.
- Export DNV : 200, 18 colonnes, IMO du navire du leg présent, filtre navire/année, nom de fichier daté.
- Carbon Report : 200 PDF (résumé + table) ; forcer un event `quality_status="error"` → **400 bloqué**.
- Params : `POST /mrv/params/save` met à jour densité/seuil/facteur + audit ; relu par `get_mrv_params`.
- Hybride : `ensure_from_noon` (idempotent) crée 1 event puis déclenche `recalculate_leg` (qualité 3 règles) ;
  2ᵉ appel même noon → pas de doublon.
- Préservation : `GET /mrv/legs/{leg_id}/carbon` (vue HTML intensités) inchangée ; facteur lu via
  `get_do_co2_factor` (versionné) et non la constante figée.

**Non‑régression persona** : dérouler intégralement **P7** (#1→#5) de
`docs/audit/backlog/TESTS-NON-REGRESSION-PERSONAS.md`.

---

## 13. Séquencement intra‑module & estimation

```
1. Migration + modèle (18 cols) + seed 3 MRVParameter (§1)                 [M]  ── socle
2. Service mrv_quality : compute_consumption/rob + validate_quality
   + recalculate_leg + get_mrv_params (MRV-04/05)                          [L]  ── dépend de 1
3. Edit/delete event + recalcul en chaîne (MRV-03)                         [M]  ── dépend de 2
4. Helpers géo + auto-remplissage DMS (MRV-07)                             [M]  ── dépend de 1
5. Export DNV 18 colonnes + correctif IMO (MRV-01)                         [M]  ── dépend de 1,4
6. Carbon Report PDF WeasyPrint + blocage qualité (MRV-02)                 [M]  ── dépend de 2,5
7. UI paramètres MRV (MRV-06)                                              [S]  ── dépend de 1
8. Branchement hybride sync (recalculate_leg dans mrv_sync) (§6)           [S]  ── dépend de 2
```
Chemin critique : 1 → 2 → 3/5/6. Étapes 4 et 7 parallélisables après 1‑2. **8 en dernier** (ne pas casser
la sync auto pendant le dev du reste).

---

## 14. Points de vigilance

- **Gains V3 à PRÉSERVER (réglementaire) :**
  - **Sync auto noon/SOF → MRVEvent idempotente** (liens uniques `noon_report_id`/`sof_event_id`) — ne
    jamais recréer ni écraser un event lié ; le branchement hybride (§6) **ajoute** le recalcul, ne
    remplace pas la sync.
  - **Carbon Report par leg** (`compute_carbon_for_leg`, intensités /NM, /t, **/t·nm**, CO₂ évité) et sa
    vue HTML `carbon_report.html` — **conservés** ; le PDF MRV‑02 est l'agrégat annuel complémentaire.
  - **Facteur CO₂ versionné** (`co2_variables.do_co2_ef`, `/admin/co2`, `get_do_co2_factor`) — le Carbon
    Report PDF **doit** l'utiliser ; `MRVParameter.co2_emission_factor` n'est qu'un **fallback** (ne pas
    réintroduire la constante figée `CO2_EMISSION_FACTOR_MDO` comme source de vérité).
  - Modèles XLSX officiels TOWT (liens `index.html`) et densité MDO paramétrable.
- **Articulation hybride compteurs ↔ noon** (cœur de A1) : compteurs DO = source primaire machine ; noon =
  complément + cross‑check ROB. Bien ordonner `recalculate_leg` (chronologique `recorded_at, id`) pour que
  le ROB calculé se propage correctement le long du leg.
- **Pas de `Leg.year` en V3** : tout filtre annuel passe par `func.extract('year', Leg.etd)` (ne pas copier
  `Leg.year` du V2). **Pas de `*_port_locode`** sur le leg V3 : résoudre `Port` via `*_port_id`.
- **VesselPosition vit dans `app/models/claim.py`** (et non un `vessel_position.py` dédié) — importer depuis
  là. `latitude`/`longitude` sont des `float` non‑null.
- **WeasyPrint, pas ReportLab** : réécrire le PDF V2 (ReportLab) en template Jinja + `_render_pdf` ;
  eager‑load vessel/ports avant rendu (pas de lazy‑load dans le moteur PDF). Import paresseux conservé.
- **Decimal partout** (cohérence V3) : compteurs/conso/ROB en `Decimal` ; éviter les `float` du portage V2
  pour les calculs réglementaires (quantize explicite).
- **Best‑effort côté bord** : `recalculate_leg` appelé depuis `mrv_sync` doit être encapsulé `try/except`
  + log fort — un échec MRV ne doit jamais faire échouer un noon report / SOF (donnée UE 2015/757).
- **CSP‑strict / Kairos / Manrope / `db.flush()` / `require_permission` / `activity_record`** sur chaque
  route — DoD.
- **`CLAUDE.md`** : après reprise, le statut MRV (« ✅ events fuel + exports DNV CSV + Carbon Report »)
  devra mentionner les compteurs DO + contrôle qualité bloquant + DMS restaurés.
```