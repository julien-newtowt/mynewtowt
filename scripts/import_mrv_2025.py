#!/usr/bin/env python3
"""Import du dataset événementiel 2025 (Sample_Dataset) — MRV LOT 13.

⚠️  DÉCISION CLIENT Q1 — DÉMARRAGE À VIDE EN PRODUCTION
=======================================================
Ce script sert EXCLUSIVEMENT aux **tests** et au **staging** (démos). Il
n'est branché sur AUCUN chemin de production : aucun module de ``app/`` ne
l'importe, aucun écran admin ne le déclenche, aucun cron ne l'appelle. Il
s'exécute uniquement à la main, par un développeur, contre une base choisie
explicitement par ``--database-url``. Ne JAMAIS le lancer contre la base de
production.

Objet
-----
Peuple une base mynewtowt depuis
``Sample_Dataset_Architecture_Evenementielle_2025.xlsx`` (21 feuilles,
année 2025 ANEMOS + ARTEMIS, reconstituée par le métier — cf. rapport
d'inventaire des données) :

- **Référentiels** : navires ANEMOS/ARTEMIS s'ils n'existent pas, cuves +
  moteurs via ``services.referential_env.ensure_vessel_env_defaults``
  (idempotent), facteurs d'émission par carburant (« Diesel Oil » /
  « Diesel DMA » = libellés fournisseur du MDO ; CO₂ 3,206 du dataset,
  CH₄/N₂O/WtT absents du dataset → complétés depuis les valeurs officielles
  du PDF DNV ``EmissionReport-ANEMOS-2025`` = MEPC.391(81), documenté dans
  ``source_reference``), ports manquants (12 LOCODEs, coordonnées
  approximatives embarquées ci-dessous).
- **28 voyages 2025** → ``legs`` créés **clôturés** (``status=completed`` +
  jalons ``closure_*``) s'ils n'existent pas. Un leg existant n'est JAMAIS
  modifié (il sert seulement de cible d'attachement).
- **Événements** typés (Noon / Departure / Arrival / Begin|End Anchoring)
  + relevés (moteurs / météo / voilure / cales), statut ``valide`` (données
  historiques vérifiées — l'import contourne volontairement la machine à
  états ``event_capture`` : pas de brouillon, pas de règles de finalisation,
  pas de ``validated_at`` fabriqué).
- **21 soutages** (BDN) + allocations par cuve, **lectures FLGO** +
  compartiments (via l'upsert idempotent de ``services.flgo_sync``),
  **contrôles croisés** conso par voyage (``flgo_voyage_consumption_refs``).
- La feuille ``Controles_Qualite`` est chargée EN MÉMOIRE et confrontée aux
  compteurs de l'import dans le rapport final (jamais persistée : le journal
  QC sera reproduit par le moteur de règles complet du lot 8).

Adaptations à la RÉALITÉ du dataset (constatées, documentées)
-------------------------------------------------------------
1. **EngineReading — contradiction README/données** : le README du dataset
   annonce des relevés « Carbon Départ/Arrivée (compteurs cumulatifs réels,
   R10 appliqué) ». VÉRIFIÉ FAUX : les 3 258 lignes sont TOUTES rattachées à
   des NoonEvent, ``running_hours_cumulative_h`` et ``fuel_counter_L`` sont
   vides PARTOUT ; seuls les deltas périodiques
   (``running_hours_since_last_report_h``, ``do_consumption_since_last_report_t``)
   sont renseignés. Le modèle cible (``nav_event_engine_readings``) stocke
   des compteurs INSTANTANÉS (les deltas sont calculés, jamais stockés) :
   l'import **reconstruit des compteurs cumulatifs synthétiques** par
   (leg × moteur) en accumulant les deltas — ``fuel_counter_l`` en litres via
   la densité R16 (``delta_L = delta_t / densité × 1000``, densité résolue
   par ``get_threshold``, 0,845 par défaut), base 0 au premier événement du
   leg, report de la dernière valeur (« carry-forward ») sur les événements
   sans relevé pour ce moteur. Propriétés : Σ des deltas recalculés par
   ``inter_event_compute`` = Σ des deltas source (télescopage), et un delta
   source NÉGATIF (2 cas réels : EVT00307/1DLG5, EVT00373/2FOG5) produit un
   compteur qui régresse → détecté comme anomalie R10 par le calcul, ce qui
   est le comportement attendu sur donnée sale.
2. **Événements hors périmètre** : 148 des 672 événements ne sont
   rattachables à AUCUN des 28 voyages du dataset (143 pointent des codes
   voyage 2024/2026 absents de la feuille ``Voyage`` — 1YMB4, 2VH0A4, 1MQC4,
   1QLD4, 2RZB4, 1LYE4, 2ZL54, 1YLF4, 1AYF6 — et 5 NoonEvent n'ont aucun
   voyage). ``nav_events.leg_id`` est NOT NULL (R02) et le dataset lui-même
   les documente « conservés, non rattachés à Voyage » dans son journal QC
   (lignes ``Perimetre`` + ``R02``). Ils sont donc COMPTÉS et IGNORÉS (pas de
   leg fabriqué), et le rapport final réconcilie ces skips avec le journal QC.
3. **Échelles mixtes % / fraction** (maladie documentée des sources) :
   ``sail_boost_pct`` est en % (40) mais ``me_ps_load_pct``/``me_sb_load_pct``
   et ``rh_pct`` sont en fraction (0,55 / 0,76). Convention plan §2.7 =
   stockage en % 0-100 : toute valeur 0 < v ≤ 1 de ces champs est ×100 à
   l'import (heuristique documentée, un vrai 0,5 % deviendrait 50 %).
4. **Divers** : ``product_name`` FLGO « Diesel DMA␣ » porte un espace final
   (nettoyé) ; « Partly laden » n'existe pas dans le vocabulaire cible
   laden/ballast → normalisé ``partly_laden`` (colonne non contrainte,
   signalé) ; allocations de soutage à volume 0 ignorées (dont doublons
   0 m³ sur la même cuve) ; les 5+ compartiments « other » d'un même soutage
   sont agrégés (volume Σ, densité moyenne pondérée) pour respecter
   ``UNIQUE(bunker_id, tank_id)`` ; densité d'en-tête BDN absente du dataset
   → moyenne pondérée des allocations, sinon défaut R16 0,845.

Idempotence (ré-exécution = 0 doublon)
--------------------------------------
Clés naturelles : ``leg_code`` (legs), ``(leg_id, event_type, datetime_utc)``
(événements — contrainte ``uq_nav_event_leg_type_dt``), ``bdn_number``
(soutages), ``(vessel, reading_datetime, action_type, product_name)`` (FLGO,
contrainte ``uq_flgoreading_natural_key``), ``locode`` (ports),
``(vessel, tank_code)`` / ``(vessel, engine_role)`` (référentiel),
``fuel_type`` (facteurs), ``leg_id`` (refs conso). Les relevés suivent leur
événement : événement existant ⇒ relevés non retouchés. Tout l'import tient
dans UNE transaction : ``--dry-run`` fait le travail complet puis ROLLBACK
(le rapport dry-run est donc exactement celui d'un run réel).

Usage
-----
::

    python scripts/import_mrv_2025.py \
        --database-url postgresql+asyncpg://towt:***@localhost:5432/towt_l4 \
        --xlsx ".../Sample_Dataset_Architecture_Evenementielle_2025.xlsx" \
        [--dry-run] [--vessel ANEMOS|ARTEMIS|all] [--reconcile] \
        [--emit-fixtures tests/fixtures/mrv_2025]

- ``--dry-run``   : rapport complet sans écriture (rollback final).
- ``--vessel``    : restreint l'import à un navire (défaut ``all``).
- ``--reconcile`` : après l'import (ou seul, l'import étant idempotent),
  imprime la réconciliation des totaux annuels ANEMOS 2025 (conso DO, CO₂ =
  conso × 3,206, distance) contre les attendus officiels du PDF DNV
  ``EmissionReport-ANEMOS-2025.pdf`` (constantes embarquées ci-dessous),
  cible ±1,5 %. Deux vues : (a) somme des deltas SOURCE xlsx par année
  calendaire de l'événement ; (b) recalcul PRODUCTION via
  ``inter_event_compute.compute_leg`` sur les legs importés (intervalle
  attribué à l'année de son événement de fin).
- ``--emit-fixtures DIR`` : régénère les fixtures pytest compactes
  (``voyage_1CLA5.json``, ``voyage_1EGB5.json``, ``bunkers_flgo.json``) —
  cf. ``tests/fixtures/mrv_2025/README.md``. Sans ``--database-url``, ce
  mode s'exécute seul (aucun accès base).

Procédure staging (démos) — AUCUN câblage app/admin (Q1)
--------------------------------------------------------
1. Créer la base et appliquer le schéma::

       createdb -h <host> -U <user> towt_staging   # ou CREATE DATABASE
       DATABASE_URL='postgresql+asyncpg://<user>:<pwd>@<host>:5432/towt_staging' \
           alembic upgrade head

2. Lancer l'import (d'abord en ``--dry-run``, puis réel, puis contrôle)::

       python scripts/import_mrv_2025.py --database-url '<url>' --xlsx '<xlsx>' --dry-run
       python scripts/import_mrv_2025.py --database-url '<url>' --xlsx '<xlsx>'
       python scripts/import_mrv_2025.py --database-url '<url>' --xlsx '<xlsx>' --reconcile

3. Vérifier le rapport (compteurs, écarts, réconciliation QC) ; re-lancer
   est sans risque (idempotent). Le fichier xlsx source vit dans le dossier
   client « Data Quality - MRV/reference-data-2025/Reconstitution/ » — il
   n'est PAS versionné dans ce dépôt (seules les fixtures compactes le sont).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

# Invocable en direct (``python scripts/import_mrv_2025.py``) comme en module
# (``python -m scripts.import_mrv_2025``) : la racine du dépôt doit être sur
# sys.path pour résoudre ``app.*``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ════════════════════════════════════════════════════ Constantes de référence

# Attendus officiels — PDF DNV « EmissionReport-ANEMOS-2025.pdf » (THETIS-MRV,
# reporting period 2025, statut Submitted to Verifier) : TOTALS/MRV +
# DISTANCE, TIME AND TRANSPORT WORK. Source de vérité de --reconcile.
PDF_ANEMOS_2025 = {
    "fuel_t": Decimal("196.32144"),
    "co2_t": Decimal("629.40653"),
    "ch4_t": Decimal("0.00982"),
    "n2o_t": Decimal("0.03534"),
    "distance_nm": Decimal("38071.4"),
    "time_at_sea_h": Decimal("4776.19"),
}
RECONCILE_TOLERANCE_PCT = Decimal("1.5")

# Facteur CO₂ TtW MDO (MEPC.391(81), identique dataset/CFOTE_09/PDF DNV).
EF_CO2_T_PER_T = Decimal("3.206")

# CH₄/N₂O/WtT — ABSENTS du dataset (colonnes vides, lacune documentée de
# l'inventaire) ; valeurs officielles du PDF DNV (EMISSIONS, EF Value) et de
# CFOTE_09 Rev02 (WtT MEPC.391(81)), mêmes constantes que
# ``services.referential_env.FALLBACK_*``.
EF_CH4_KG_PER_KG = Decimal("0.00005")
EF_N2O_KG_PER_KG = Decimal("0.00018")
WTT_GCO2EQ_PER_MJ = Decimal("17.7")

# Densité de repli si le seuil R16 n'est pas résolvable (aligne
# ``inter_event_compute.FALLBACK_DENSITY_T_M3``).
FALLBACK_DENSITY_T_M3 = Decimal("0.845")

# Navires du dataset → code navire mynewtowt (convention du seed existant :
# « 1 » = Anemos, « 2 » = Artemis, cf. scripts/seed_demo.py).
VESSEL_DEFS: dict[str, dict[str, str]] = {
    "ANEMOS": {"code": "1", "name": "Anemos"},
    "ARTEMIS": {"code": "2", "name": "Artemis"},
}

# Ports référencés par les 28 voyages + soutages 2025. Coordonnées
# APPROXIMATIVES (centre portuaire) — suffisantes pour l'affichage staging ;
# un port déjà présent en base n'est jamais retouché.
PORT_DEFS: dict[str, tuple[str, str, float, float]] = {
    # locode: (nom, pays ISO2, lat, lon)
    "FRCOC": ("Concarneau", "FR", 47.8710, -3.9190),
    "FRLEH": ("Le Havre", "FR", 49.4944, 0.1079),
    "FRFEC": ("Fécamp", "FR", 49.7565, 0.3712),
    "FRBES": ("Brest", "FR", 48.3830, -4.4950),
    "USNYC": ("New York", "US", 40.6759, -74.0173),
    "GPPTP": ("Pointe-à-Pitre", "GP", 16.2333, -61.5333),
    "GTSTC": ("Santo Tomás de Castilla", "GT", 15.7000, -88.6170),
    "BRSSO": ("São Sebastião", "BR", -23.7610, -45.4090),
    "CUHAV": ("La Havane", "CU", 23.1330, -82.3830),
    "CAMAT": ("Matane", "CA", 48.8500, -67.5300),
    "COSMR": ("Santa Marta", "CO", 11.2500, -74.2170),
    "PTOPO": ("Porto", "PT", 41.1500, -8.6300),
}

# Feuille Event → discriminant polymorphe nav_events.
EVENT_TYPE_MAP: dict[str, str] = {
    "NoonEvent": "noon",
    "DepartureEvent": "departure",
    "ArrivalEvent": "arrival",
    "BeginAnchoringEvent": "anchoring_begin",
    "EndAnchoringEvent": "anchoring_end",
}

# Rôles moteur agrégés dans les totaux MRV (lignes d'arbre exclues) — même
# convention que ``referential_env.ENGINE_ROLE_TO_GROUP``.
ENGINE_ROLE_TO_GROUP: dict[str, str | None] = {
    "PME": "ME",
    "SME": "ME",
    "FWD_GEN": "AE",
    "AFT_GEN": "AE",
    "PORT_SHAFT_GEN": None,
    "STBD_SHAFT_GEN": None,
}

# Champs à normaliser en % 0-100 (adaptation n°3 du docstring).
PCT_SCALE_MAX_FRACTION = Decimal("1")


# ════════════════════════════════════════════════════════ Lecture du dataset


@dataclass
class Dataset:
    """Contenu utile du classeur, feuille par feuille (dicts par ligne)."""

    vessels: list[dict]
    tanks: list[dict]
    engines: list[dict]
    emission_factors: list[dict]
    voyages: list[dict]
    events: list[dict]
    departure_details: dict[str, dict]
    arrival_details: dict[str, dict]
    begin_anchoring_details: dict[str, dict]
    end_anchoring_details: dict[str, dict]
    weather_by_event: dict[str, list[dict]]
    sail_by_event: dict[str, list[dict]]
    hold_by_event: dict[str, list[dict]]
    engine_by_event: dict[str, list[dict]]
    bunkers: list[dict]
    allocations_by_bunker: dict[str, list[dict]]
    flgo_readings: list[dict]
    flgo_compartments: dict[str, list[dict]]
    consumption_refs: list[dict]
    qc_lines: list[dict]

    @property
    def voyage_codes(self) -> set[str]:
        return {v["voyage_code"] for v in self.voyages}


def _sheet_rows(wb, name: str) -> list[dict]:
    """Feuille → liste de dicts (en-tête = 1re ligne, lignes vides ignorées)."""
    ws = wb[name]
    it = ws.iter_rows(values_only=True)
    header = next(it)
    out: list[dict] = []
    for row in it:
        if row is None or all(c is None for c in row):
            continue
        # strict=False : openpyxl peut renvoyer des lignes plus courtes/longues
        # que l'en-tête (cellules de queue vides) — c'est attendu.
        out.append(dict(zip(header, row, strict=False)))
    return out


def load_dataset(xlsx_path: Path) -> Dataset:
    import openpyxl

    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)

    def by_key(rows: list[dict], key: str) -> dict[str, dict]:
        return {r[key]: r for r in rows}

    def group_by(rows: list[dict], key: str) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            grouped[r[key]].append(r)
        return grouped

    ds = Dataset(
        vessels=_sheet_rows(wb, "Vessel"),
        tanks=_sheet_rows(wb, "Tank"),
        engines=_sheet_rows(wb, "Engine"),
        emission_factors=_sheet_rows(wb, "EmissionFactor"),
        voyages=_sheet_rows(wb, "Voyage"),
        events=_sheet_rows(wb, "Event"),
        departure_details=by_key(_sheet_rows(wb, "DepartureEvent"), "event_id"),
        arrival_details=by_key(_sheet_rows(wb, "ArrivalEvent"), "event_id"),
        begin_anchoring_details=by_key(_sheet_rows(wb, "BeginAnchoringEvent"), "event_id"),
        end_anchoring_details=by_key(_sheet_rows(wb, "EndAnchoringEvent"), "event_id"),
        weather_by_event=group_by(_sheet_rows(wb, "WeatherReading"), "noon_event_id"),
        sail_by_event=group_by(_sheet_rows(wb, "SailReading"), "noon_event_id"),
        hold_by_event=group_by(_sheet_rows(wb, "HoldReading"), "noon_event_id"),
        engine_by_event=group_by(_sheet_rows(wb, "EngineReading"), "event_id"),
        bunkers=_sheet_rows(wb, "BunkerOperation"),
        allocations_by_bunker=group_by(_sheet_rows(wb, "BunkerTankAllocation"), "bunker_id"),
        flgo_readings=_sheet_rows(wb, "FlgoReading"),
        flgo_compartments=group_by(
            _sheet_rows(wb, "FlgoTankCompartmentVolume"), "flgo_reading_id"
        ),
        consumption_refs=_sheet_rows(wb, "FlgoVoyageConsumptionRef"),
        qc_lines=_sheet_rows(wb, "Controles_Qualite"),
    )
    wb.close()
    return ds


# ═══════════════════════════════════════════════════════ Conversions communes


# Cellules non numériques rencontrées dans des colonnes numériques (donnée
# sale du dataset : « - », « % », « > », « Stb »…) — comptées pour le rapport,
# importées comme NULL (jamais fabriquées, jamais bloquantes).
_DIRTY_CELLS: dict[str, int] = defaultdict(int)


def _dec(value: Any) -> Decimal | None:
    """xlsx (float/int/str) → Decimal exact via str, None conservé.

    Une valeur non convertible (texte parasite) est comptée dans
    ``_DIRTY_CELLS`` et importée comme NULL — cf. rapport final.
    """
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        _DIRTY_CELLS[repr(value)] += 1
        return None


def _utc(value: datetime | None) -> datetime | None:
    """Datetime xlsx (naïf, convention UTC du dataset) → aware UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def _norm_slot(value: Any) -> str | None:
    """« 1600 » / 1600 / « 0000 » → « 16:00 » (format NAV_TIME_SLOTS)."""
    if value is None:
        return None
    s = str(value).strip().zfill(4)
    if len(s) != 4 or not s.isdigit():
        return None
    return f"{s[:2]}:{s[2:]}"


def _on_off(value: Any) -> bool:
    return isinstance(value, str) and value.strip().upper() == "ON"


def _pct_0_100(value: Any) -> Decimal | None:
    """Normalise l'échelle mixte fraction/pourcentage (adaptation n°3)."""
    d = _dec(value)
    if d is None:
        return None
    if Decimal("0") < d <= PCT_SCALE_MAX_FRACTION:
        return d * Decimal("100")
    return d


def _norm_condition(value: Any) -> str | None:
    """« Laden » / « Ballast » / « Partly laden » → laden/ballast/partly_laden."""
    cleaned = _clean(value)
    if cleaned is None:
        return None
    return cleaned.lower().replace(" ", "_")


def _norm_status(value: Any) -> str:
    """« Valide » (dataset, données historiques vérifiées) → ``valide``."""
    cleaned = (_clean(value) or "").lower()
    return "valide" if cleaned == "valide" else "finalise"


def _norm_position_source(value: Any) -> str | None:
    cleaned = (_clean(value) or "").lower()
    return "thalos_auto" if cleaned == "thalos_auto" else (cleaned or None)


# ══════════════════════════════════ Reconstruction des compteurs cumulatifs


@dataclass
class ReconstructedReading:
    """Relevé compteur synthétique (adaptation n°1 du docstring)."""

    engine_role: str
    running_hours_counter_h: Decimal
    fuel_counter_l: Decimal | None


def reconstruct_counters(
    ordered_event_ids: list[str],
    engine_by_event: dict[str, list[dict]],
    density: Decimal,
) -> dict[str, list[ReconstructedReading]]:
    """Compteurs cumulatifs synthétiques par événement pour UN leg.

    Pour chaque rôle moteur vu dans le leg : base 0 au premier événement,
    ``hours_cum += delta_h`` / ``fuel_cum_l += delta_t / densité × 1000`` à
    chaque relevé source, valeur reportée telle quelle (carry-forward) sur
    les événements sans relevé. ``fuel_counter_l`` reste None pour un rôle
    dont AUCUN delta carburant n'est renseigné dans le leg (jamais 0
    fabriqué). Un delta négatif fait régresser le compteur (anomalie R10
    détectée en aval par ``inter_event_compute`` — voulu).
    Renvoie ``{event_id_dataset: [ReconstructedReading, ...]}``.
    """
    roles_seen: list[str] = []
    fuel_data_roles: set[str] = set()
    for eid in ordered_event_ids:
        for row in engine_by_event.get(eid, ()):
            role = row["engine_role"]
            if role not in roles_seen:
                roles_seen.append(role)
            if row.get("do_consumption_since_last_report_t") is not None:
                fuel_data_roles.add(role)

    hours_cum: dict[str, Decimal] = {r: Decimal("0") for r in roles_seen}
    fuel_cum: dict[str, Decimal] = {r: Decimal("0") for r in roles_seen}
    out: dict[str, list[ReconstructedReading]] = {}
    for eid in ordered_event_ids:
        rows_by_role = {r["engine_role"]: r for r in engine_by_event.get(eid, ())}
        readings: list[ReconstructedReading] = []
        for role in roles_seen:
            row = rows_by_role.get(role)
            if row is not None:
                dh = _dec(row.get("running_hours_since_last_report_h"))
                if dh is not None:
                    hours_cum[role] += dh
                dt_ = _dec(row.get("do_consumption_since_last_report_t"))
                if dt_ is not None:
                    fuel_cum[role] += dt_ / density * Decimal("1000")
            readings.append(
                ReconstructedReading(
                    engine_role=role,
                    running_hours_counter_h=hours_cum[role],
                    fuel_counter_l=(fuel_cum[role] if role in fuel_data_roles else None),
                )
            )
        out[eid] = readings
    return out


# ════════════════════════════════════════════════════════ Rapport d'import


@dataclass
class Counter:
    created: int = 0
    skipped_existing: int = 0
    skipped_out_of_scope: int = 0
    skipped_duplicate: int = 0
    updated: int = 0
    errors: int = 0

    def line(self) -> str:
        parts = [f"créés={self.created}", f"ignorés(existants)={self.skipped_existing}"]
        if self.skipped_out_of_scope:
            parts.append(f"ignorés(hors périmètre)={self.skipped_out_of_scope}")
        if self.skipped_duplicate:
            parts.append(f"ignorés(doublons source)={self.skipped_duplicate}")
        if self.updated:
            parts.append(f"mis à jour={self.updated}")
        if self.errors:
            parts.append(f"erreurs={self.errors}")
        return ", ".join(parts)


@dataclass
class ImportReport:
    dry_run: bool
    vessel_filter: str
    counters: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    qc_lines: list[dict] = field(default_factory=list)
    out_of_scope_voyage_codes: dict[str, int] = field(default_factory=dict)
    events_without_voyage: int = 0

    def c(self, name: str) -> Counter:
        return self.counters[name]

    def print(self) -> None:
        mode = "DRY-RUN (aucune écriture — rollback)" if self.dry_run else "IMPORT RÉEL"
        print("\n" + "=" * 76)
        print(f"RAPPORT D'IMPORT — Sample_Dataset 2025 — {mode} — navires: {self.vessel_filter}")
        print("=" * 76)
        order = [
            "vessels", "ports", "tanks", "engines", "emission_factors", "legs",
            "events", "engine_readings", "weather_readings", "sail_readings",
            "hold_readings", "bunkers", "bunker_allocations", "flgo_readings",
            "flgo_compartments", "consumption_refs",
        ]
        labels = {
            "vessels": "Navires", "ports": "Ports", "tanks": "Cuves",
            "engines": "Moteurs", "emission_factors": "Facteurs d'émission",
            "legs": "Legs (voyages 2025)", "events": "Événements",
            "engine_readings": "Relevés compteurs (reconstruits)",
            "weather_readings": "Relevés météo", "sail_readings": "Relevés voilure",
            "hold_readings": "Relevés cales", "bunkers": "Soutages (BDN)",
            "bunker_allocations": "Allocations par cuve",
            "flgo_readings": "Lectures FLGO", "flgo_compartments": "Compartiments FLGO",
            "consumption_refs": "Refs conso FLGO/voyage",
        }
        for key in order:
            if key in self.counters:
                print(f"  {labels.get(key, key):36s} {self.counters[key].line()}")

        if self.out_of_scope_voyage_codes or self.events_without_voyage:
            print("\n  Événements hors périmètre (documentés par le journal QC du dataset) :")
            for code, n in sorted(self.out_of_scope_voyage_codes.items()):
                print(f"    - voyage {code} (absent des 28 voyages 2025) : {n} événement(s)")
            if self.events_without_voyage:
                print(f"    - sans voyage résolu (R02 source) : {self.events_without_voyage} événement(s)")

        if self.qc_lines:
            by_rule: dict[str, int] = defaultdict(int)
            for line in self.qc_lines:
                by_rule[str(line.get("regle"))] += 1
            summary = ", ".join(f"{r}×{n}" for r, n in sorted(by_rule.items()))
            print(f"\n  Journal Controles_Qualite du dataset : {len(self.qc_lines)} anomalies ({summary})")
            perimetre_codes = {
                str(line.get("objet"))
                for line in self.qc_lines
                if line.get("regle") == "Perimetre"
            }
            missing = set(self.out_of_scope_voyage_codes) - perimetre_codes
            if missing:
                print(f"    ⚠ codes hors périmètre NON couverts par le journal QC : {sorted(missing)}")
            else:
                print("    ✓ tous les codes voyage ignorés sont documentés par les lignes « Perimetre »")

        if _DIRTY_CELLS:
            total = sum(_DIRTY_CELLS.values())
            detail = ", ".join(f"{raw}×{n}" for raw, n in sorted(_DIRTY_CELLS.items()))
            print(
                f"\n  Cellules non numériques importées comme NULL (donnée sale source) : "
                f"{total} ({detail})"
            )

        if self.warnings:
            print(f"\n  Avertissements ({len(self.warnings)}) :")
            for w in self.warnings[:20]:
                print(f"    - {w}")
            if len(self.warnings) > 20:
                print(f"    … et {len(self.warnings) - 20} autres")
        if self.errors:
            print(f"\n  ERREURS ({len(self.errors)}) :")
            for e in self.errors[:20]:
                print(f"    - {e}")
        print("=" * 76)


# ════════════════════════════════════════════════════════ Phases d'import


def _selected_dataset_vessels(ds: Dataset, vessel_filter: str) -> list[dict]:
    rows = ds.vessels
    if vessel_filter != "all":
        rows = [v for v in rows if v["vessel_id"] == vessel_filter]
    return rows


async def ensure_vessels(db, ds: Dataset, report: ImportReport, vessel_filter: str) -> dict[str, Any]:
    """Navires ANEMOS/ARTEMIS — créés seulement s'ils n'existent pas.

    Résolution d'un navire existant : par nom (insensible à la casse), puis
    par IMO, puis par code. Un navire existant n'est JAMAIS modifié.
    """
    from sqlalchemy import func, select

    from app.models.vessel import Vessel

    out: dict[str, Any] = {}
    for row in _selected_dataset_vessels(ds, vessel_filter):
        ds_id = row["vessel_id"]
        defs = VESSEL_DEFS[ds_id]
        imo = str(row.get("imo") or "").strip() or None

        vessel = (
            await db.execute(
                select(Vessel).where(func.lower(Vessel.name) == defs["name"].lower())
            )
        ).scalar_one_or_none()
        if vessel is None and imo:
            vessel = (
                await db.execute(select(Vessel).where(Vessel.imo_number == imo))
            ).scalar_one_or_none()
        if vessel is None:
            vessel = (
                await db.execute(select(Vessel).where(Vessel.code == defs["code"]))
            ).scalar_one_or_none()

        if vessel is None:
            vessel = Vessel(
                code=defs["code"],
                name=defs["name"],
                imo_number=imo,
                flag="FR",
                build_status="operational",
                default_fuel_type=(_clean(row.get("default_fuel_type")) or "MDO"),
            )
            db.add(vessel)
            await db.flush()
            report.c("vessels").created += 1
        else:
            report.c("vessels").skipped_existing += 1
        out[ds_id] = vessel
    return out


async def ensure_ports(db, ds: Dataset, report: ImportReport, vessels: dict) -> dict[str, Any]:
    """Ports des voyages + soutages — créés s'ils manquent (jamais modifiés)."""
    from sqlalchemy import select

    from app.models.port import Port

    selected_vessel_ids = set(vessels)
    needed: set[str] = set()
    for v in ds.voyages:
        if v["vessel_id"] in selected_vessel_ids:
            needed.add(str(v["dep_port_unlocode"]).strip().upper())
            needed.add(str(v["arr_port_unlocode"]).strip().upper())
    for b in ds.bunkers:
        if b["vessel_id"] in selected_vessel_ids:
            needed.add(str(b["port_unlocode"]).strip().upper())

    out: dict[str, Any] = {}
    for locode in sorted(needed):
        port = (await db.execute(select(Port).where(Port.locode == locode))).scalar_one_or_none()
        if port is None:
            name, country, lat, lon = PORT_DEFS.get(
                locode, (locode, locode[:2], None, None)
            )
            if locode not in PORT_DEFS:
                report.warnings.append(
                    f"port {locode} absent du catalogue embarqué — créé sans coordonnées"
                )
            port = Port(
                locode=locode, name=name, country=country,
                latitude=lat, longitude=lon, source="user",
            )
            db.add(port)
            await db.flush()
            report.c("ports").created += 1
        else:
            report.c("ports").skipped_existing += 1
        out[locode] = port
    return out


async def ensure_referentials(db, ds: Dataset, report: ImportReport, vessels: dict) -> None:
    """Cuves + moteurs via ``ensure_vessel_env_defaults`` (idempotent, lot 1)."""
    from app.models.vessel_env import ENGINE_ROLES, TANK_CODES
    from app.services.referential_env import ensure_vessel_env_defaults

    for vessel in vessels.values():
        result = await ensure_vessel_env_defaults(db, vessel)
        report.c("tanks").created += len(result.tanks_created)
        report.c("engines").created += len(result.engines_created)
        # Comptage des existants pour un rapport honnête au re-run.
        report.c("tanks").skipped_existing += len(TANK_CODES) - len(result.tanks_created)
        report.c("engines").skipped_existing += len(ENGINE_ROLES) - len(result.engines_created)


async def ensure_emission_factors(db, ds: Dataset, report: ImportReport) -> None:
    """Facteurs par carburant — CH₄/N₂O/WtT complétés (cf. docstring module)."""
    from sqlalchemy import select

    from app.models.emission_factor import EmissionFactor

    existing_fuels = set(
        (await db.execute(select(EmissionFactor.fuel_type))).scalars().all()
    )
    for row in ds.emission_factors:
        fuel = _clean(row.get("fuel_type"))
        if not fuel:
            continue
        if fuel in existing_fuels:
            report.c("emission_factors").skipped_existing += 1
            continue
        co2 = _dec(row.get("ef_co2_kg_per_kg")) or EF_CO2_T_PER_T
        valid_from = row.get("valid_from")
        if isinstance(valid_from, datetime):
            valid_from = valid_from.date()
        elif isinstance(valid_from, str):
            valid_from = datetime.fromisoformat(valid_from).date()
        source = _clean(row.get("source_reference")) or "MEPC.391(81)"
        db.add(
            EmissionFactor(
                fuel_type=fuel,
                ef_co2_kg_per_kg=co2,
                ef_ch4_kg_per_kg=EF_CH4_KG_PER_KG,
                ef_n2o_kg_per_kg=EF_N2O_KG_PER_KG,
                wtt_gco2eq_per_mj=WTT_GCO2EQ_PER_MJ,
                source_reference=(
                    f"{source} ; CH4/N2O/WtT complétés depuis EmissionReport-ANEMOS-2025 "
                    "(DNV) + CFOTE_09 Rev02 (import lot 13)"
                )[:200],
                valid_from=valid_from,
                valid_to=row.get("valid_to"),
                is_current=True,
            )
        )
        existing_fuels.add(fuel)
        report.c("emission_factors").created += 1
    await db.flush()


async def ensure_legs(db, ds: Dataset, report: ImportReport, vessels: dict, ports: dict) -> dict[str, Any]:
    """28 voyages → legs CLÔTURÉS. Un leg existant n'est JAMAIS modifié."""
    from sqlalchemy import select

    from app.models.leg import Leg

    out: dict[str, Any] = {}
    for v in ds.voyages:
        if v["vessel_id"] not in vessels:
            continue
        code = str(v["voyage_code"]).strip()
        existing = (
            await db.execute(select(Leg).where(Leg.leg_code == code))
        ).scalar_one_or_none()
        if existing is not None:
            report.c("legs").skipped_existing += 1
            out[code] = existing
            continue

        dep_dt = _utc(v["dep_datetime_utc"])
        arr_dt = _utc(v["arr_datetime_utc"])
        if dep_dt is None or arr_dt is None:
            report.c("legs").errors += 1
            report.errors.append(f"voyage {code} : dates dep/arr manquantes — leg non créé")
            continue
        dep_port = ports[str(v["dep_port_unlocode"]).strip().upper()]
        arr_port = ports[str(v["arr_port_unlocode"]).strip().upper()]
        note = _clean(v.get("note"))
        closure_note = "Importé clôturé depuis Sample_Dataset 2025 (lot 13, staging/tests)."
        if note:
            closure_note += f"\n[Source] {note}"
        leg = Leg(
            leg_code=code,
            vessel_id=vessels[v["vessel_id"]].id,
            departure_port_id=dep_port.id,
            arrival_port_id=arr_port.id,
            etd_ref=dep_dt,
            eta_ref=arr_dt,
            etd=dep_dt,
            eta=arr_dt,
            atd=dep_dt,
            ata=arr_dt,
            status="completed",
            is_bookable=False,
            closure_submitted_at=arr_dt,
            closure_reviewed_at=arr_dt,
            closure_approved_at=arr_dt,
            closure_submitted_by="import_mrv_2025",
            closure_reviewed_by="import_mrv_2025",
            closure_notes=closure_note,
        )
        db.add(leg)
        report.c("legs").created += 1
        out[code] = leg
    await db.flush()
    return out


def _event_detail_payload(ds: Dataset, event_id: str, event_type: str) -> dict:
    """Champs du sous-type depuis la feuille de détail correspondante."""
    if event_type == "departure":
        d = ds.departure_details.get(event_id, {})
        return {
            "draft_fwd_m": _dec(d.get("draft_fwd_m")),
            "draft_aft_m": _dec(d.get("draft_aft_m")),
            "trim_m": _dec(d.get("trim_m")),
            "vessel_condition": _norm_condition(d.get("vessel_condition")),
            "cargo_bl_t": _dec(d.get("cargo_bl_t")),
            "rob_t": _dec(d.get("rob_t")),
            "etd_confirmed": _utc(d.get("etd_confirmed")),
        }
    if event_type == "arrival":
        d = ds.arrival_details.get(event_id, {})
        return {
            "draft_fwd_m": _dec(d.get("draft_fwd_m")),
            "draft_aft_m": _dec(d.get("draft_aft_m")),
            "trim_m": _dec(d.get("trim_m")),
            "vessel_condition": _norm_condition(d.get("vessel_condition")),
            "rob_t": _dec(d.get("rob_t")),
        }
    if event_type == "anchoring_begin":
        d = ds.begin_anchoring_details.get(event_id, {})
        return {
            "sequence_no": (int(d["anchoring_sequence_no"]) if d.get("anchoring_sequence_no") is not None else None),
            "reason": _clean(d.get("reason")),
        }
    if event_type == "anchoring_end":
        d = ds.end_anchoring_details.get(event_id, {})
        return {
            "sequence_no": (int(d["anchoring_sequence_no"]) if d.get("anchoring_sequence_no") is not None else None),
            "duration_h": _dec(d.get("duration_h")),
        }
    return {}


def _weather_payloads(ds: Dataset, event_id: str) -> list[dict]:
    return [
        {
            "slot_time": _norm_slot(r.get("slot_time")),
            "tws_kn": _dec(r.get("tws_kt")),
            "awa_deg": _dec(r.get("awa_deg")),
            "aws_kn": _dec(r.get("aws_kt")),
            "sea_state": (int(r["sea_state"]) if r.get("sea_state") is not None else None),
            "sea_direction_deg": _dec(r.get("sea_dir_deg")),
            "ship_speed_kn": _dec(r.get("ship_speed_kt")),
        }
        for r in ds.weather_by_event.get(event_id, ())
    ]


def _sail_payloads(ds: Dataset, event_id: str) -> list[dict]:
    return [
        {
            "slot_time": _norm_slot(r.get("slot_time")),
            "j0": _on_off(r.get("j0")),
            "fwd_j1": _on_off(r.get("fwd_j1")),
            "fwd_ms": _on_off(r.get("fwd_ms")),
            "aft_j1": _on_off(r.get("aft_j1")),
            "aft_ms": _on_off(r.get("aft_ms")),
            "sail_boost_pct": _pct_0_100(r.get("sail_boost_pct")),
            "me_ps_load_pct": _pct_0_100(r.get("me_ps_load_pct")),
            "me_sb_load_pct": _pct_0_100(r.get("me_sb_load_pct")),
        }
        for r in ds.sail_by_event.get(event_id, ())
    ]


def _hold_payloads(ds: Dataset, event_id: str) -> list[dict]:
    return [
        {
            "period": (_clean(r.get("period")) or "").lower() or None,
            "zone": _clean(r.get("zone")),
            "temp_c": _dec(r.get("temp_c")),
            "rh_pct": _pct_0_100(r.get("rh_pct")),
        }
        for r in ds.hold_by_event.get(event_id, ())
    ]


async def import_events(db, ds: Dataset, report: ImportReport, vessels: dict, legs: dict) -> None:
    """Événements + relevés, leg par leg (une passe chronologique par leg)."""
    from sqlalchemy import select

    from app.models.nav_event import (
        EVENT_CLASS_BY_TYPE,
        NavEvent,
        NavEventEngineReading,
        NavEventHoldReading,
        NavEventSailReading,
        NavEventWeatherReading,
        NoonEvent,
    )
    from app.models.vessel_env import VesselEngine
    from app.services.inter_event_compute import resolve_density

    selected_vessel_ids = set(vessels)

    # Tri des événements par leg (et comptage du hors périmètre).
    events_by_voyage: dict[str, list[dict]] = defaultdict(list)
    for ev in ds.events:
        if ev["vessel_id"] not in selected_vessel_ids:
            continue
        voyage_id = _clean(ev.get("voyage_id"))
        if voyage_id is None:
            report.events_without_voyage += 1
            report.c("events").skipped_out_of_scope += 1
            continue
        if voyage_id not in legs:
            report.out_of_scope_voyage_codes[voyage_id] = (
                report.out_of_scope_voyage_codes.get(voyage_id, 0) + 1
            )
            report.c("events").skipped_out_of_scope += 1
            continue
        events_by_voyage[voyage_id].append(ev)

    engines_by_vessel: dict[int, dict[str, VesselEngine]] = {}

    async def _engines_for(vessel) -> dict[str, VesselEngine]:
        if vessel.id not in engines_by_vessel:
            rows = (
                await db.execute(select(VesselEngine).where(VesselEngine.vessel_id == vessel.id))
            ).scalars().all()
            engines_by_vessel[vessel.id] = {e.engine_role: e for e in rows}
        return engines_by_vessel[vessel.id]

    for voyage_id in sorted(events_by_voyage):
        leg = legs[voyage_id]
        ds_vessel_id = events_by_voyage[voyage_id][0]["vessel_id"]
        vessel = vessels[ds_vessel_id]
        engines = await _engines_for(vessel)
        density = await resolve_density(db, vessel.id)

        rows = sorted(
            events_by_voyage[voyage_id],
            key=lambda e: (e["datetime_utc"], e["event_id"]),
        )
        ordered_ids = [e["event_id"] for e in rows]
        counters = reconstruct_counters(ordered_ids, ds.engine_by_event, density)

        # Clés naturelles déjà en base pour ce leg (idempotence).
        existing_keys = {
            (t, dt.replace(tzinfo=UTC) if (dt is not None and dt.tzinfo is None) else dt)
            for (t, dt) in (
                await db.execute(
                    select(NavEvent.event_type, NavEvent.datetime_utc).where(
                        NavEvent.leg_id == leg.id
                    )
                )
            ).all()
        }

        created_events: list[tuple[dict, Any]] = []  # (ligne dataset, instance ORM)
        anchoring_created: dict[tuple[str, int | None], Any] = {}
        seen_in_batch: set[tuple[str, datetime]] = set()
        for ev in rows:
            event_type = EVENT_TYPE_MAP.get(str(ev["event_type"]))
            if event_type is None:
                report.c("events").errors += 1
                report.errors.append(f"{ev['event_id']} : type inconnu {ev['event_type']!r}")
                continue
            dt_utc = _utc(ev["datetime_utc"])
            if dt_utc is None:
                report.c("events").errors += 1
                report.errors.append(f"{ev['event_id']} : datetime_utc manquant")
                continue
            key = (event_type, dt_utc)
            if key in existing_keys:
                report.c("events").skipped_existing += 1
                continue
            if key in seen_in_batch:
                # Doublon SOURCE (deux événements du même type au même instant
                # sur le même voyage — anomalie de classe IR01, constatée dans
                # le dataset). Le premier gagne (tri (datetime, event_id)) ;
                # le second est compté et signalé, jamais importé (contrainte
                # ``uq_nav_event_leg_type_dt``).
                report.c("events").skipped_duplicate += 1
                report.warnings.append(
                    f"{ev['event_id']} : doublon source (leg {voyage_id}, {event_type}, "
                    f"{dt_utc.isoformat()}) — première occurrence conservée"
                )
                continue
            seen_in_batch.add(key)

            cls = EVENT_CLASS_BY_TYPE[event_type]
            instance = cls(
                leg_id=leg.id,
                vessel_id=vessel.id,
                datetime_utc=dt_utc,
                lat_decimal=_dec(ev.get("lat_decimal")),
                lon_decimal=_dec(ev.get("lon_decimal")),
                position_source=_norm_position_source(ev.get("position_source")),
                cargo_mrv_t=_dec(ev.get("cargo_mrv_t")),
                status=_norm_status(ev.get("status")),
            )
            for field_name, value in _event_detail_payload(ds, ev["event_id"], event_type).items():
                setattr(instance, field_name, value)
            db.add(instance)
            created_events.append((ev, instance))
            if event_type in ("anchoring_begin", "anchoring_end"):
                anchoring_created[(event_type, instance.sequence_no)] = instance
            report.c("events").created += 1

        await db.flush()  # matérialise les ids avant les relevés (FK)

        # Appariement Begin↔End (paired_event_id de l'End → id du Begin).
        for (etype, seq), instance in anchoring_created.items():
            if etype == "anchoring_end":
                begin = anchoring_created.get(("anchoring_begin", seq))
                if begin is not None:
                    instance.paired_event_id = begin.id

        # Relevés — uniquement pour les événements créés dans ce run.
        for ev, instance in created_events:
            for rec in counters.get(ev["event_id"], ()):
                engine = engines.get(rec.engine_role)
                if engine is None:
                    report.c("engine_readings").errors += 1
                    report.errors.append(
                        f"{ev['event_id']} : moteur {rec.engine_role!r} absent du référentiel"
                    )
                    continue
                db.add(
                    NavEventEngineReading(
                        event_id=instance.id,
                        engine_id=engine.id,
                        running_hours_counter_h=rec.running_hours_counter_h,
                        fuel_counter_l=rec.fuel_counter_l,
                        is_counter_reset=False,
                    )
                )
                report.c("engine_readings").created += 1

            if isinstance(instance, NoonEvent):
                for payload in _weather_payloads(ds, ev["event_id"]):
                    db.add(NavEventWeatherReading(event_id=instance.id, **payload))
                    report.c("weather_readings").created += 1
                for payload in _sail_payloads(ds, ev["event_id"]):
                    db.add(NavEventSailReading(event_id=instance.id, **payload))
                    report.c("sail_readings").created += 1
                for payload in _hold_payloads(ds, ev["event_id"]):
                    db.add(NavEventHoldReading(event_id=instance.id, **payload))
                    report.c("hold_readings").created += 1
        await db.flush()


def _aggregate_allocations(rows: list[dict]) -> list[dict]:
    """Allocations par cuve — volumes 0 ignorés, cuves dupliquées agrégées.

    Le dataset ventile chaque soutage sur les 9 compartiments physiques dont
    5 retombent sur ``other`` : on agrège par ``tank_id`` dataset (volume Σ,
    densité moyenne pondérée par le volume) pour respecter
    ``UNIQUE(bunker_id, tank_id)``.
    """
    by_tank: dict[str, dict[str, Decimal]] = {}
    for r in rows:
        volume = _dec(r.get("volume_m3")) or Decimal("0")
        if volume <= 0:
            continue
        density = _dec(r.get("density_t_m3"))
        acc = by_tank.setdefault(
            r["tank_id"], {"volume": Decimal("0"), "mass": Decimal("0")}
        )
        acc["volume"] += volume
        if density is not None:
            acc["mass"] += volume * density
    out = []
    for tank_ds_id, acc in by_tank.items():
        density = (acc["mass"] / acc["volume"]) if acc["mass"] > 0 else None
        out.append({"tank_ds_id": tank_ds_id, "volume_m3": acc["volume"], "density_t_m3": density})
    return out


async def import_bunkers(db, ds: Dataset, report: ImportReport, vessels: dict, legs: dict) -> None:
    """Soutages BDN + allocations — clé naturelle ``bdn_number``."""
    from sqlalchemy import select

    from app.models.bunker import BunkerOperation, BunkerTankAllocation
    from app.models.vessel_env import VesselTank

    tanks_by_vessel: dict[int, dict[str, Any]] = {}

    async def _tanks_for(vessel) -> dict[str, Any]:
        if vessel.id not in tanks_by_vessel:
            rows = (
                await db.execute(select(VesselTank).where(VesselTank.vessel_id == vessel.id))
            ).scalars().all()
            tanks_by_vessel[vessel.id] = {t.tank_code: t for t in rows}
        return tanks_by_vessel[vessel.id]

    for row in ds.bunkers:
        if row["vessel_id"] not in vessels:
            continue
        bdn = str(row["bdn_number"]).strip()
        existing = (
            await db.execute(select(BunkerOperation).where(BunkerOperation.bdn_number == bdn))
        ).scalar_one_or_none()
        if existing is not None:
            report.c("bunkers").skipped_existing += 1
            continue

        vessel = vessels[row["vessel_id"]]
        voyage_id = _clean(row.get("voyage_id"))
        leg = legs.get(voyage_id) if voyage_id else None
        if voyage_id and leg is None:
            report.warnings.append(
                f"soutage {bdn} : voyage {voyage_id} hors périmètre — leg_id laissé NULL"
            )

        allocations = _aggregate_allocations(ds.allocations_by_bunker.get(row["bunker_id"], []))
        total_volume = sum((a["volume_m3"] for a in allocations), Decimal("0"))
        weighted_mass = sum(
            (a["volume_m3"] * a["density_t_m3"] for a in allocations if a["density_t_m3"] is not None),
            Decimal("0"),
        )
        header_density = (
            (weighted_mass / total_volume).quantize(Decimal("0.0001"))
            if total_volume > 0 and weighted_mass > 0
            else FALLBACK_DENSITY_T_M3
        )

        status = (_clean(row.get("status")) or "").lower()
        bunker = BunkerOperation(
            leg_id=(leg.id if leg is not None else None),
            vessel_id=vessel.id,
            bdn_number=bdn,
            port_locode=str(row["port_unlocode"]).strip().upper(),
            delivery_datetime_utc=_utc(row["delivery_datetime_utc"]),
            fuel_type=(_clean(row.get("fuel_type")) or "MDO"),
            mass_t=_dec(row["mass_t"]),
            density_15c_t_m3=header_density,
            status=("valide_master" if status == "valide master" else "brouillon"),
        )
        db.add(bunker)
        await db.flush()
        report.c("bunkers").created += 1

        tanks = await _tanks_for(vessel)
        for alloc in allocations:
            tank_code = alloc["tank_ds_id"].rsplit("-T", 1)[-1]  # « ANEMOS-T14 » → « 14 »
            tank = tanks.get(tank_code)
            if tank is None:
                report.c("bunker_allocations").errors += 1
                report.errors.append(f"soutage {bdn} : cuve {alloc['tank_ds_id']!r} non résolue")
                continue
            db.add(
                BunkerTankAllocation(
                    bunker_id=bunker.id,
                    tank_id=tank.id,
                    volume_m3=alloc["volume_m3"],
                    density_t_m3=alloc["density_t_m3"] or header_density,
                )
            )
            report.c("bunker_allocations").created += 1
    await db.flush()


async def import_flgo(db, ds: Dataset, report: ImportReport, vessels: dict) -> None:
    """Lectures FLGO + compartiments — upsert idempotent de ``flgo_sync``.

    Donnée sale constatée : le dataset contient des lignes FLGO en collision
    de clé naturelle ENTRE ELLES (même navire/horodatage/action/produit avec
    des volumes différents — concaténation de plusieurs exports Marad dans la
    reconstitution, ARTEMIS surtout). La sémantique upsert « photographie la
    plus récente » s'applique : la dernière ligne du fichier gagne ; les
    collisions intra-fichier sont comptées et signalées.
    """
    from app.services.flgo_sync import CompartmentInput, _upsert_reading, derive_tank_code

    seen_keys_this_run: set[tuple] = set()
    in_file_collisions = 0

    for row in ds.flgo_readings:
        if row["vessel_id"] not in vessels:
            continue
        vessel = vessels[row["vessel_id"]]
        action_type = (_clean(row.get("action_type")) or "").lower()
        product_name = _clean(row.get("product_name"))
        reading_dt = _utc(row.get("reading_datetime"))
        total_volume = _dec(row.get("total_volume_m3"))
        if not action_type or not product_name or reading_dt is None or total_volume is None:
            report.c("flgo_readings").errors += 1
            report.errors.append(f"FLGO {row.get('flgo_reading_id')!r} : champs clés manquants")
            continue

        compartments: list[CompartmentInput] = []
        for comp in ds.flgo_compartments.get(row["flgo_reading_id"], ()):
            code = _clean(comp.get("compartment_code")) or ""
            volume = _dec(comp.get("volume_m3"))
            if not code or volume is None:
                continue
            derived = derive_tank_code(code)
            ds_tank = _clean(comp.get("tank_code"))
            if ds_tank and derived != ds_tank:
                report.warnings.append(
                    f"FLGO {row['flgo_reading_id']} : tank_code dérivé {derived!r} ≠ dataset {ds_tank!r} ({code!r})"
                )
            compartments.append(
                CompartmentInput(
                    compartment_code=code, volume_m3=volume, mass_t=_dec(comp.get("mass_t"))
                )
            )

        natural_key = (vessel.id, reading_dt, action_type, product_name)
        if natural_key in seen_keys_this_run:
            in_file_collisions += 1
        seen_keys_this_run.add(natural_key)

        _, created = await _upsert_reading(
            db,
            vessel_id=vessel.id,
            action_type=action_type,
            product_name=product_name,
            reading_datetime=reading_dt,
            total_volume_m3=total_volume,
            total_rob_m3=_dec(row.get("total_rob_m3")),
            remarks=_clean(row.get("remarks")),
            source="xlsx_import",
            compartments=compartments,
        )
        if created:
            report.c("flgo_readings").created += 1
            report.c("flgo_compartments").created += len(compartments)
        else:
            report.c("flgo_readings").skipped_existing += 1
            report.c("flgo_compartments").skipped_existing += len(compartments)

    if in_file_collisions:
        report.warnings.append(
            f"FLGO : {in_file_collisions} ligne(s) source en collision de clé naturelle "
            "DANS le fichier (relevés multiples au même horodatage, valeurs différentes) "
            "— dernière ligne conservée (sémantique upsert), donnée sale à signaler"
        )


async def import_consumption_refs(db, ds: Dataset, report: ImportReport, legs: dict) -> None:
    """Contrôles croisés conso ME/AE par voyage — clé d'idempotence: leg_id."""
    from sqlalchemy import select

    from app.models.flgo import FlgoVoyageConsumptionRef

    existing_leg_ids = set(
        (await db.execute(select(FlgoVoyageConsumptionRef.leg_id))).scalars().all()
    )
    for row in ds.consumption_refs:
        voyage_id = _clean(row.get("voyage_id"))
        leg = legs.get(voyage_id) if voyage_id else None
        if leg is None:
            report.c("consumption_refs").skipped_out_of_scope += 1
            continue
        if leg.id in existing_leg_ids:
            report.c("consumption_refs").skipped_existing += 1
            continue
        me = _dec(row.get("me_consumption_mdo_t"))
        ae = _dec(row.get("ae_consumption_mdo_t"))
        if me is None or ae is None:
            report.c("consumption_refs").errors += 1
            report.errors.append(f"ref conso {row.get('ref_id')!r} : ME/AE manquants")
            continue
        db.add(
            FlgoVoyageConsumptionRef(
                leg_id=leg.id, me_consumption_t=me, ae_consumption_t=ae,
                ecart_t=_dec(row.get("ecart_t")),
            )
        )
        existing_leg_ids.add(leg.id)
        report.c("consumption_refs").created += 1
    await db.flush()


# ════════════════════════════════════════════════════════════ Réconciliation


def _pct(value: Decimal, expected: Decimal) -> Decimal:
    return (value - expected) / expected * Decimal("100")


def _verdict(delta_pct: Decimal) -> str:
    return "✓ dans ±1,5 %" if abs(delta_pct) <= RECONCILE_TOLERANCE_PCT else "✗ HORS ±1,5 %"


def reconcile_from_xlsx(ds: Dataset) -> None:
    """Vue (a) : somme des deltas SOURCE par année calendaire d'événement."""
    ev_by_id = {e["event_id"]: e for e in ds.events}
    vcodes = ds.voyage_codes

    def _sum(predicate) -> Decimal:
        total = Decimal("0")
        for event_id, rows in ds.engine_by_event.items():
            ev = ev_by_id.get(event_id)
            if ev is None or ev["vessel_id"] != "ANEMOS" or not predicate(ev):
                continue
            for r in rows:
                if ENGINE_ROLE_TO_GROUP.get(r["engine_role"]) is None:
                    continue
                d = _dec(r.get("do_consumption_since_last_report_t"))
                if d is not None:
                    total += d
        return total

    in_scope_2025 = _sum(
        lambda ev: _clean(ev.get("voyage_id")) in vcodes and ev["datetime_utc"].year == 2025
    )
    all_2025 = _sum(lambda ev: ev["datetime_utc"].year == 2025)

    print("\n  (a) Somme des deltas SOURCE xlsx (ME+AE, année calendaire de l'événement) :")
    for label, total in (
        ("périmètre 28 voyages ∩ 2025 (= données importées)", in_scope_2025),
        ("tous événements 2025 (y c. voyages hors périmètre)", all_2025),
    ):
        co2 = total * EF_CO2_T_PER_T
        d_fuel = _pct(total, PDF_ANEMOS_2025["fuel_t"])
        d_co2 = _pct(co2, PDF_ANEMOS_2025["co2_t"])
        print(f"    {label}")
        print(
            f"      conso DO {total:.3f} t vs PDF {PDF_ANEMOS_2025['fuel_t']} t "
            f"→ écart {d_fuel:+.3f} % {_verdict(d_fuel)}"
        )
        print(
            f"      CO₂ (×3,206) {co2:.3f} t vs PDF {PDF_ANEMOS_2025['co2_t']} t "
            f"→ écart {d_co2:+.3f} % {_verdict(d_co2)}"
        )


async def reconcile_from_db(db, ds: Dataset, vessels: dict) -> None:
    """Vue (b) : recalcul PRODUCTION via ``inter_event_compute.compute_leg``."""
    from sqlalchemy import select

    from app.models.leg import Leg
    from app.services.inter_event_compute import compute_leg

    anemos = vessels.get("ANEMOS")
    if anemos is None:
        print("\n  (b) recalcul DB : navire ANEMOS hors sélection — ignoré")
        return

    codes = sorted(v["voyage_code"] for v in ds.voyages if v["vessel_id"] == "ANEMOS")
    legs = (
        (await db.execute(select(Leg).where(Leg.leg_code.in_(codes)))).scalars().all()
    )
    total = Decimal("0")
    me = Decimal("0")
    ae = Decimal("0")
    distance = Decimal("0")
    anomalies = 0
    for leg in legs:
        computation = await compute_leg(db, leg)
        for interval in computation.intervals:
            to_dt = interval.to_dt
            if to_dt is None or to_dt.year != 2025:
                continue
            if interval.counter_anomaly:
                anomalies += 1
                continue
            if interval.total_conso_t is not None:
                total += interval.total_conso_t
            g_me = interval.group_conso_t.get("ME")
            g_ae = interval.group_conso_t.get("AE")
            if g_me is not None:
                me += g_me
            if g_ae is not None:
                ae += g_ae
            if interval.distance_nm is not None:
                distance += interval.distance_nm

    co2 = total * EF_CO2_T_PER_T
    d_fuel = _pct(total, PDF_ANEMOS_2025["fuel_t"])
    d_co2 = _pct(co2, PDF_ANEMOS_2025["co2_t"])
    d_dist = _pct(distance, PDF_ANEMOS_2025["distance_nm"]) if distance else Decimal("0")
    print(
        "\n  (b) Recalcul PRODUCTION (inter_event_compute.compute_leg sur les legs importés,\n"
        "      intervalles attribués à l'année de leur événement de fin) :"
    )
    print(f"    legs ANEMOS trouvés en base : {len(legs)}/{len(codes)}")
    print(
        f"    conso DO 2025 : {total:.3f} t (ME {me:.3f} / AE {ae:.3f}) vs PDF "
        f"{PDF_ANEMOS_2025['fuel_t']} t → écart {d_fuel:+.3f} % {_verdict(d_fuel)}"
    )
    print(
        f"    CO₂ (×3,206) : {co2:.3f} t vs PDF {PDF_ANEMOS_2025['co2_t']} t "
        f"→ écart {d_co2:+.3f} % {_verdict(d_co2)}"
    )
    print(
        f"    distance haversine inter-événements : {distance:.1f} nm vs PDF "
        f"{PDF_ANEMOS_2025['distance_nm']} nm → écart {d_dist:+.2f} % "
        "(info seulement : chemin orthodromique événement-à-événement ≠ loch MRV — "
        "noons post-arrivée rattachés aux voyages source et positions sales inclus)"
    )
    if anomalies:
        print(
            f"    intervalles exclus pour anomalie compteur (deltas source négatifs, R10) : {anomalies}"
        )


async def run_reconcile(db, ds: Dataset, vessels: dict) -> None:
    print("\n" + "=" * 76)
    print("RÉCONCILIATION — totaux annuels ANEMOS 2025 vs PDF DNV (EmissionReport)")
    print("=" * 76)
    print(
        f"  Attendus PDF (reporting period 2025) : conso {PDF_ANEMOS_2025['fuel_t']} t DO, "
        f"CO₂ {PDF_ANEMOS_2025['co2_t']} t, distance {PDF_ANEMOS_2025['distance_nm']} nm."
    )
    print(
        "  NB : le total PDF couvre l'année civile complète, y compris janvier-février\n"
        "  sous voyages 2024 (hors des 28 voyages du dataset) et la conso à quai ;\n"
        "  la note de reconstitution du dossier client qualifie de « normal » un\n"
        "  écart ≈1,5 % entre double-calculs indépendants."
    )
    reconcile_from_xlsx(ds)
    await reconcile_from_db(db, ds, vessels)
    print("=" * 76)


# ════════════════════════════════════════════════ Émission des fixtures pytest

FIXTURE_VOYAGES = ("1CLA5", "1EGB5")
# Les 2 soutages appariés FLGO des fixtures (cf. tests/fixtures/mrv_2025/README.md)
FIXTURE_MATCHED_BUNKERS = ("BUNK-0002", "BUNK-0004")


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return _utc(dt).isoformat()


def _s(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _fixture_events(ds: Dataset, voyage_code: str) -> tuple[list[dict], dict]:
    """Événements d'un voyage au format fixture + attendus golden calculés.

    Les attendus de consommation sont dérivés de la CHAÎNE de compteurs
    reconstruite (télescopage ``cum(dernier) − cum(premier)`` par moteur,
    groupes ME/AE) — c'est exactement ce que ``inter_event_compute``
    recalcule. Nuance réelle du dataset : quand le premier événement
    chronologique du voyage est un Noon ANTÉRIEUR au Departure (cas 1EGB5),
    son propre delta « since last report » précède la chaîne et n'est
    récupérable par AUCUN intervalle — il est donc exclu des attendus
    (cf. README.md du dossier fixtures). Le ROB attendu du dernier point est
    ancré sur le ROB déclaré du Departure (comportement de
    ``compute_rob_chain`` : ancrage au premier PortCall rencontré) moins la
    conso des intervalles POSTÉRIEURS au Departure.
    """
    rows = sorted(
        (e for e in ds.events if _clean(e.get("voyage_id")) == voyage_code),
        key=lambda e: (e["datetime_utc"], e["event_id"]),
    )
    ordered_ids = [e["event_id"] for e in rows]
    counters = reconstruct_counters(ordered_ids, ds.engine_by_event, FALLBACK_DENSITY_T_M3)

    events_json: list[dict] = []
    for ev in rows:
        event_type = EVENT_TYPE_MAP[str(ev["event_type"])]
        detail = {
            k: (_iso(v) if isinstance(v, datetime) else (_s(v) if isinstance(v, Decimal) else v))
            for k, v in _event_detail_payload(ds, ev["event_id"], event_type).items()
        }
        events_json.append(
            {
                "dataset_event_id": ev["event_id"],
                "event_type": event_type,
                "datetime_utc": _iso(ev["datetime_utc"]),
                "lat_decimal": _s(_dec(ev.get("lat_decimal"))),
                "lon_decimal": _s(_dec(ev.get("lon_decimal"))),
                "position_source": _norm_position_source(ev.get("position_source")),
                "cargo_mrv_t": _s(_dec(ev.get("cargo_mrv_t"))),
                "status": _norm_status(ev.get("status")),
                "detail": detail,
                "engine_readings": [
                    {
                        "engine_role": r.engine_role,
                        "running_hours_counter_h": _s(r.running_hours_counter_h),
                        "fuel_counter_l": _s(r.fuel_counter_l),
                    }
                    for r in counters.get(ev["event_id"], ())
                ],
                "weather_readings": [
                    {k: (_s(v) if isinstance(v, Decimal) else v) for k, v in p.items()}
                    for p in _weather_payloads(ds, ev["event_id"])
                ],
                "sail_readings": [
                    {k: (_s(v) if isinstance(v, Decimal) else v) for k, v in p.items()}
                    for p in _sail_payloads(ds, ev["event_id"])
                ],
                "hold_readings": [
                    {k: (_s(v) if isinstance(v, Decimal) else v) for k, v in p.items()}
                    for p in _hold_payloads(ds, ev["event_id"])
                ],
            }
        )

    # ── Attendus golden : télescopage de la chaîne de compteurs reconstruite.
    def _group_totals(from_event_id: str) -> dict[str, Decimal]:
        """Conso (t) par groupe entre ``from_event_id`` et le dernier événement."""
        first = {r.engine_role: r.fuel_counter_l for r in counters[from_event_id]}
        last = {r.engine_role: r.fuel_counter_l for r in counters[ordered_ids[-1]]}
        totals = {"ME": Decimal("0"), "AE": Decimal("0")}
        for role, cum_last in last.items():
            group = ENGINE_ROLE_TO_GROUP.get(role)
            if group is None or cum_last is None:
                continue
            cum_first = first.get(role) or Decimal("0")
            totals[group] += (cum_last - cum_first) * Decimal("0.001") * FALLBACK_DENSITY_T_M3
        return totals

    chain = _group_totals(ordered_ids[0])
    dep = next((e for e in events_json if e["event_type"] == "departure"), None)
    arr = next((e for e in events_json if e["event_type"] == "arrival"), None)
    rob_dep = Decimal(dep["detail"]["rob_t"]) if dep and dep["detail"].get("rob_t") else None
    after_departure = (
        _group_totals(dep["dataset_event_id"]) if dep is not None else chain
    )
    expected = {
        "conso_me_t": _s(chain["ME"]),
        "conso_ae_t": _s(chain["AE"]),
        "conso_total_t": _s(chain["ME"] + chain["AE"]),
        "density_t_m3": _s(FALLBACK_DENSITY_T_M3),
        "rob_departure_t": (dep or {}).get("detail", {}).get("rob_t"),
        "rob_arrival_declared_t": (arr or {}).get("detail", {}).get("rob_t"),
        # ROB chaîné au DERNIER événement : ancré au Departure (1er PortCall),
        # moins la conso des intervalles postérieurs (aucun soutage entre les
        # événements des fixtures — BDN livrés à quai avant le Departure).
        "rob_last_calculated_t": _s(
            rob_dep - (after_departure["ME"] + after_departure["AE"])
        )
        if rob_dep is not None
        else None,
        "events_count": len(events_json),
    }
    return events_json, expected


def _fixture_voyage(ds: Dataset, voyage_code: str) -> dict:
    voyage = next(v for v in ds.voyages if v["voyage_code"] == voyage_code)
    ds_vessel_id = voyage["vessel_id"]
    vessel_row = next(v for v in ds.vessels if v["vessel_id"] == ds_vessel_id)
    events_json, expected = _fixture_events(ds, voyage_code)

    dep_locode = str(voyage["dep_port_unlocode"]).strip().upper()
    arr_locode = str(voyage["arr_port_unlocode"]).strip().upper()
    ports = []
    for locode in (dep_locode, arr_locode):
        name, country, lat, lon = PORT_DEFS[locode]
        ports.append({"locode": locode, "name": name, "country": country, "latitude": lat, "longitude": lon})

    bunkers = []
    for b in ds.bunkers:
        if _clean(b.get("voyage_id")) != voyage_code:
            continue
        allocations = _aggregate_allocations(ds.allocations_by_bunker.get(b["bunker_id"], []))
        bunkers.append(
            {
                "dataset_bunker_id": b["bunker_id"],
                "bdn_number": str(b["bdn_number"]).strip(),
                "port_locode": str(b["port_unlocode"]).strip().upper(),
                "delivery_datetime_utc": _iso(b["delivery_datetime_utc"]),
                "fuel_type": _clean(b.get("fuel_type")) or "MDO",
                "mass_t": _s(_dec(b["mass_t"])),
                "status": "valide_master",
                "allocations": [
                    {
                        "tank_code": a["tank_ds_id"].rsplit("-T", 1)[-1],
                        "volume_m3": _s(a["volume_m3"]),
                        "density_t_m3": _s(a["density_t_m3"]),
                    }
                    for a in allocations
                ],
            }
        )

    qc_expected = [
        line
        for line in ds.qc_lines
        if any(b["bdn_number"] in str(line.get("objet", "")) for b in bunkers)
    ]

    return {
        "_meta": {
            "source": "Sample_Dataset_Architecture_Evenementielle_2025.xlsx",
            "generated_by": "python scripts/import_mrv_2025.py --emit-fixtures",
            "voyage": voyage_code,
            "note": (
                "Compteurs moteurs = cumuls SYNTHÉTIQUES reconstruits depuis les deltas "
                "périodiques du dataset (densité 0,845, base 0 au premier événement) — "
                "cf. README.md du dossier."
            ),
        },
        "vessel": {
            "dataset_vessel_id": ds_vessel_id,
            "code": VESSEL_DEFS[ds_vessel_id]["code"],
            "name": VESSEL_DEFS[ds_vessel_id]["name"],
            "imo_number": str(vessel_row.get("imo") or "") or None,
        },
        "ports": ports,
        "leg": {
            "leg_code": voyage_code,
            "dep_locode": dep_locode,
            "arr_locode": arr_locode,
            "dep_datetime_utc": _iso(voyage["dep_datetime_utc"]),
            "arr_datetime_utc": _iso(voyage["arr_datetime_utc"]),
        },
        "events": events_json,
        "bunkers": bunkers,
        "flgo_readings": [],
        "qc_expected": qc_expected,
        "expected": expected,
    }


def _fixture_bunkers_flgo(ds: Dataset) -> dict:
    """2 soutages APPARIÉS + leurs lectures FLGO « Received » (test R24 pass)."""
    flgo_by_id = {f["flgo_reading_id"]: f for f in ds.flgo_readings}
    bunkers = []
    flgo_ids: list[str] = []
    for bunker_ds_id in FIXTURE_MATCHED_BUNKERS:
        b = next(x for x in ds.bunkers if x["bunker_id"] == bunker_ds_id)
        alloc_rows = ds.allocations_by_bunker.get(bunker_ds_id, [])
        source_ids = {r.get("flgo_reading_id_source") for r in alloc_rows} - {None}
        flgo_ids.extend(sorted(source_ids))
        allocations = _aggregate_allocations(alloc_rows)
        bunkers.append(
            {
                "dataset_bunker_id": bunker_ds_id,
                "bdn_number": str(b["bdn_number"]).strip(),
                "port_locode": str(b["port_unlocode"]).strip().upper(),
                "delivery_datetime_utc": _iso(b["delivery_datetime_utc"]),
                "fuel_type": _clean(b.get("fuel_type")) or "MDO",
                "mass_t": _s(_dec(b["mass_t"])),
                "status": "valide_master",
                "allocations": [
                    {
                        "tank_code": a["tank_ds_id"].rsplit("-T", 1)[-1],
                        "volume_m3": _s(a["volume_m3"]),
                        "density_t_m3": _s(a["density_t_m3"]),
                    }
                    for a in allocations
                ],
            }
        )

    flgo_json = []
    for fid in dict.fromkeys(flgo_ids):  # dédoublonne en conservant l'ordre
        f = flgo_by_id[fid]
        comps = ds.flgo_compartments.get(fid, [])
        flgo_json.append(
            {
                "dataset_flgo_id": fid,
                "action_type": (_clean(f.get("action_type")) or "").lower(),
                "product_name": _clean(f.get("product_name")),
                "reading_datetime": _iso(f["reading_datetime"]),
                "total_volume_m3": _s(_dec(f["total_volume_m3"])),
                "total_rob_m3": _s(_dec(f.get("total_rob_m3"))),
                "remarks": _clean(f.get("remarks")),
                "compartments": [
                    {
                        "compartment_code": _clean(c.get("compartment_code")),
                        "volume_m3": _s(_dec(c.get("volume_m3"))),
                        "mass_t": _s(_dec(c.get("mass_t"))),
                    }
                    for c in comps
                    if _clean(c.get("compartment_code")) and _dec(c.get("volume_m3")) is not None
                ],
            }
        )

    return {
        "_meta": {
            "source": "Sample_Dataset_Architecture_Evenementielle_2025.xlsx",
            "generated_by": "python scripts/import_mrv_2025.py --emit-fixtures",
            "note": (
                "2 soutages ANEMOS appariés à leur lecture FLGO 'Received' "
                "(écarts 0,11 j et 0,55 j — fenêtre R24 = 5 j)."
            ),
        },
        "vessel": {
            "dataset_vessel_id": "ANEMOS",
            "code": VESSEL_DEFS["ANEMOS"]["code"],
            "name": VESSEL_DEFS["ANEMOS"]["name"],
            "imo_number": "9982938",
        },
        "ports": [],
        "leg": None,
        "events": [],
        "bunkers": bunkers,
        "flgo_readings": flgo_json,
        "qc_expected": [],
        "expected": {"matched_bunkers": len(bunkers)},
    }


def emit_fixtures(ds: Dataset, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "voyage_1CLA5.json": _fixture_voyage(ds, "1CLA5"),
        "voyage_1EGB5.json": _fixture_voyage(ds, "1EGB5"),
        "bunkers_flgo.json": _fixture_bunkers_flgo(ds),
    }
    for name, payload in files.items():
        path = out_dir / name
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"  fixture écrite : {path}")


# ═══════════════════════════════════════════════════════════════ Orchestration


async def run_import(args: argparse.Namespace, ds: Dataset) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(args.database_url, echo=False, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    report = ImportReport(dry_run=args.dry_run, vessel_filter=args.vessel)
    report.qc_lines = list(ds.qc_lines)

    try:
        async with session_factory() as db:
            vessels = await ensure_vessels(db, ds, report, args.vessel)
            ports = await ensure_ports(db, ds, report, vessels)
            await ensure_referentials(db, ds, report, vessels)
            await ensure_emission_factors(db, ds, report)
            legs = await ensure_legs(db, ds, report, vessels, ports)
            await import_events(db, ds, report, vessels, legs)
            await import_bunkers(db, ds, report, vessels, legs)
            await import_flgo(db, ds, report, vessels)
            await import_consumption_refs(db, ds, report, legs)

            if args.dry_run:
                await db.rollback()
            else:
                await db.commit()

            report.print()

            if args.reconcile:
                await run_reconcile(db, ds, vessels)
    finally:
        await engine.dispose()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Import du Sample_Dataset événementiel 2025 — TESTS/STAGING UNIQUEMENT "
            "(décision Q1 : démarrage à vide en production)."
        )
    )
    parser.add_argument(
        "--database-url",
        help="URL asyncpg de la base CIBLE (jamais la production). "
        "Optionnelle uniquement avec --emit-fixtures seul.",
    )
    parser.add_argument("--xlsx", required=True, help="Chemin du Sample_Dataset xlsx")
    parser.add_argument("--dry-run", action="store_true", help="Rapport sans écriture (rollback)")
    parser.add_argument(
        "--vessel", choices=("ANEMOS", "ARTEMIS", "all"), default="all",
        help="Restreint l'import à un navire (défaut all)",
    )
    parser.add_argument(
        "--reconcile", action="store_true",
        help="Compare les totaux annuels ANEMOS 2025 aux attendus du PDF DNV (±1,5 %%)",
    )
    parser.add_argument(
        "--emit-fixtures", metavar="DIR", default=None,
        help="Régénère les fixtures pytest compactes dans DIR (sans DB si --database-url absent)",
    )
    args = parser.parse_args(argv)
    if args.database_url:
        # Normalise vers le driver async (exigence du schéma app.config).
        if args.database_url.startswith("postgresql://"):
            args.database_url = args.database_url.replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )
    elif not args.emit_fixtures:
        parser.error("--database-url est requis (sauf --emit-fixtures seul)")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    xlsx_path = Path(args.xlsx)
    if not xlsx_path.exists():
        print(f"ERREUR : fichier xlsx introuvable : {xlsx_path}", file=sys.stderr)
        return 2

    # ``app.config`` exige SECRET_KEY/DATABASE_URL avant tout import de
    # ``app.*`` — le script est standalone : il fournit ses propres valeurs
    # (la vraie connexion utilise EXCLUSIVEMENT --database-url).
    os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
    os.environ.setdefault(
        "DATABASE_URL", args.database_url or "postgresql+asyncpg://x:x@localhost:5432/x"
    )

    print(f"Lecture du dataset : {xlsx_path}")
    ds = load_dataset(xlsx_path)
    print(
        f"  feuilles chargées : {len(ds.voyages)} voyages, {len(ds.events)} événements, "
        f"{len(ds.bunkers)} soutages, {len(ds.flgo_readings)} lectures FLGO, "
        f"{len(ds.qc_lines)} lignes QC"
    )

    if args.emit_fixtures:
        emit_fixtures(ds, Path(args.emit_fixtures))
        if not args.database_url:
            return 0

    asyncio.run(run_import(args, ds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
