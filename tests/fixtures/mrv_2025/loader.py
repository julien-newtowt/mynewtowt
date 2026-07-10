"""Loader des fixtures golden MRV 2025 — objets ORM prêts à l'emploi (LOT 13).

``load_voyage(db, name)`` charge un extrait JSON du dossier (généré depuis le
Sample_Dataset 2025 par ``scripts/import_mrv_2025.py --emit-fixtures``, cf.
``README.md``) et matérialise en base les objets ORM correspondants :
navire (+ référentiel cuves/moteurs via ``ensure_vessel_env_defaults``),
ports, leg clôturé, événements typés + relevés, soutages + allocations,
lectures FLGO + compartiments.

Noms disponibles : ``"1CLA5"`` (voyage ANEMOS complet Departure + noons +
Arrival + relevés, + le soutage non apparié BDN 433421 — anomalie R24 du
journal QC), ``"1EGB5"`` (voyage avec mouillage Begin/End Anchoring),
``"bunkers_flgo"`` (2 soutages appariés + leurs lectures FLGO « Received » —
pas de leg).

Le loader est volontairement idempotent par clé naturelle (get-or-create sur
vessel.code / port.locode / leg.leg_code / bdn_number / clé FLGO) : deux
``load_voyage`` du même nom, ou deux fixtures partageant le même navire,
cohabitent dans la même session de test sans doublon.

Compatible avec le fixture ``db`` SQLite in-memory des tests d'intégration
(aucune fonctionnalité Postgres requise).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bunker import BunkerOperation, BunkerTankAllocation
from app.models.flgo import FlgoReading, FlgoTankCompartmentVolume
from app.models.leg import Leg
from app.models.nav_event import (
    EVENT_CLASS_BY_TYPE,
    NavEvent,
    NavEventEngineReading,
    NavEventHoldReading,
    NavEventSailReading,
    NavEventWeatherReading,
    NoonEvent,
)
from app.models.port import Port
from app.models.vessel import Vessel
from app.models.vessel_env import VesselEngine, VesselTank
from app.services.referential_env import ensure_vessel_env_defaults

_FIXTURE_DIR = Path(__file__).parent
_FIXTURE_FILES = {
    "1CLA5": "voyage_1CLA5.json",
    "1EGB5": "voyage_1EGB5.json",
    "bunkers_flgo": "bunkers_flgo.json",
}


@dataclass
class LoadedFixture:
    """Objets ORM matérialisés + attendus golden de la fixture."""

    name: str
    vessel: Vessel
    leg: Leg | None
    events: list[NavEvent]
    bunkers: list[BunkerOperation]
    flgo_readings: list[FlgoReading]
    expected: dict[str, Any] = field(default_factory=dict)
    qc_expected: list[dict] = field(default_factory=list)


def _dec(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


async def _get_or_create_vessel(db: AsyncSession, payload: dict) -> Vessel:
    vessel = (
        await db.execute(select(Vessel).where(Vessel.code == payload["code"]))
    ).scalar_one_or_none()
    if vessel is None:
        vessel = Vessel(
            code=payload["code"],
            name=payload["name"],
            imo_number=payload.get("imo_number"),
            flag="FR",
        )
        db.add(vessel)
        await db.flush()
    await ensure_vessel_env_defaults(db, vessel)
    return vessel


async def _get_or_create_ports(db: AsyncSession, payloads: list[dict]) -> dict[str, Port]:
    out: dict[str, Port] = {}
    for p in payloads:
        port = (
            await db.execute(select(Port).where(Port.locode == p["locode"]))
        ).scalar_one_or_none()
        if port is None:
            port = Port(
                locode=p["locode"],
                name=p["name"],
                country=p["country"],
                latitude=p.get("latitude"),
                longitude=p.get("longitude"),
                source="user",
            )
            db.add(port)
            await db.flush()
        out[p["locode"]] = port
    return out


async def _get_or_create_leg(
    db: AsyncSession, payload: dict, vessel: Vessel, ports: dict[str, Port]
) -> Leg:
    leg = (
        await db.execute(select(Leg).where(Leg.leg_code == payload["leg_code"]))
    ).scalar_one_or_none()
    if leg is not None:
        return leg
    dep_dt = _dt(payload["dep_datetime_utc"])
    arr_dt = _dt(payload["arr_datetime_utc"])
    leg = Leg(
        leg_code=payload["leg_code"],
        vessel_id=vessel.id,
        departure_port_id=ports[payload["dep_locode"]].id,
        arrival_port_id=ports[payload["arr_locode"]].id,
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
        closure_submitted_by="fixtures_mrv_2025",
        closure_reviewed_by="fixtures_mrv_2025",
        closure_notes="Fixture golden Sample_Dataset 2025 (lot 13).",
    )
    db.add(leg)
    await db.flush()
    return leg


# Champs du bloc ``detail`` par type — datetime vs Decimal vs texte.
_DETAIL_DT_FIELDS = frozenset({"etd_confirmed", "eta_announced", "etb"})
_DETAIL_TEXT_FIELDS = frozenset({"vessel_condition", "reason"})
_DETAIL_INT_FIELDS = frozenset({"sequence_no"})


def _apply_detail(instance: NavEvent, detail: dict) -> None:
    for key, raw in detail.items():
        if raw is None:
            setattr(instance, key, None)
        elif key in _DETAIL_DT_FIELDS:
            setattr(instance, key, _dt(raw))
        elif key in _DETAIL_TEXT_FIELDS:
            setattr(instance, key, raw)
        elif key in _DETAIL_INT_FIELDS:
            setattr(instance, key, int(raw))
        else:
            setattr(instance, key, _dec(raw))


async def _create_events(
    db: AsyncSession, payloads: list[dict], vessel: Vessel, leg: Leg
) -> list[NavEvent]:
    engines: dict[str, VesselEngine] = {
        e.engine_role: e
        for e in (
            await db.execute(select(VesselEngine).where(VesselEngine.vessel_id == vessel.id))
        ).scalars()
    }

    events: list[NavEvent] = []
    anchoring_by_seq: dict[tuple[str, int | None], NavEvent] = {}
    for ev in payloads:
        existing = (
            await db.execute(
                select(NavEvent).where(
                    NavEvent.leg_id == leg.id,
                    NavEvent.event_type == ev["event_type"],
                    NavEvent.datetime_utc == _dt(ev["datetime_utc"]),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            events.append(existing)
            continue

        cls = EVENT_CLASS_BY_TYPE[ev["event_type"]]
        instance = cls(
            leg_id=leg.id,
            vessel_id=vessel.id,
            datetime_utc=_dt(ev["datetime_utc"]),
            lat_decimal=_dec(ev.get("lat_decimal")),
            lon_decimal=_dec(ev.get("lon_decimal")),
            position_source=ev.get("position_source"),
            cargo_mrv_t=_dec(ev.get("cargo_mrv_t")),
            status=ev.get("status", "valide"),
        )
        _apply_detail(instance, ev.get("detail") or {})
        db.add(instance)
        await db.flush()

        if ev["event_type"] in ("anchoring_begin", "anchoring_end"):
            seq = getattr(instance, "sequence_no", None)
            anchoring_by_seq[(ev["event_type"], seq)] = instance
            if ev["event_type"] == "anchoring_end":
                begin = anchoring_by_seq.get(("anchoring_begin", seq))
                if begin is not None:
                    instance.paired_event_id = begin.id

        for r in ev.get("engine_readings", ()):
            engine = engines[r["engine_role"]]
            db.add(
                NavEventEngineReading(
                    event_id=instance.id,
                    engine_id=engine.id,
                    running_hours_counter_h=_dec(r.get("running_hours_counter_h")),
                    fuel_counter_l=_dec(r.get("fuel_counter_l")),
                    is_counter_reset=False,
                )
            )
        if isinstance(instance, NoonEvent):
            for r in ev.get("weather_readings", ()):
                db.add(
                    NavEventWeatherReading(
                        event_id=instance.id,
                        slot_time=r.get("slot_time"),
                        tws_kn=_dec(r.get("tws_kn")),
                        awa_deg=_dec(r.get("awa_deg")),
                        aws_kn=_dec(r.get("aws_kn")),
                        sea_state=r.get("sea_state"),
                        sea_direction_deg=_dec(r.get("sea_direction_deg")),
                        ship_speed_kn=_dec(r.get("ship_speed_kn")),
                    )
                )
            for r in ev.get("sail_readings", ()):
                db.add(
                    NavEventSailReading(
                        event_id=instance.id,
                        slot_time=r.get("slot_time"),
                        j0=bool(r.get("j0")),
                        fwd_j1=bool(r.get("fwd_j1")),
                        fwd_ms=bool(r.get("fwd_ms")),
                        aft_j1=bool(r.get("aft_j1")),
                        aft_ms=bool(r.get("aft_ms")),
                        sail_boost_pct=_dec(r.get("sail_boost_pct")),
                        me_ps_load_pct=_dec(r.get("me_ps_load_pct")),
                        me_sb_load_pct=_dec(r.get("me_sb_load_pct")),
                    )
                )
            for r in ev.get("hold_readings", ()):
                db.add(
                    NavEventHoldReading(
                        event_id=instance.id,
                        period=r.get("period"),
                        zone=r.get("zone"),
                        temp_c=_dec(r.get("temp_c")),
                        rh_pct=_dec(r.get("rh_pct")),
                    )
                )
        events.append(instance)
    await db.flush()
    return events


async def _create_bunkers(
    db: AsyncSession, payloads: list[dict], vessel: Vessel, leg: Leg | None
) -> list[BunkerOperation]:
    tanks: dict[str, VesselTank] = {
        t.tank_code: t
        for t in (
            await db.execute(select(VesselTank).where(VesselTank.vessel_id == vessel.id))
        ).scalars()
    }
    default_density = Decimal("0.845")

    out: list[BunkerOperation] = []
    for b in payloads:
        existing = (
            await db.execute(
                select(BunkerOperation).where(BunkerOperation.bdn_number == b["bdn_number"])
            )
        ).scalar_one_or_none()
        if existing is not None:
            out.append(existing)
            continue
        allocations = b.get("allocations", ())
        densities = [
            (_dec(a["volume_m3"]), _dec(a["density_t_m3"]))
            for a in allocations
            if a.get("density_t_m3") is not None
        ]
        total_v = sum((v for v, _ in densities), Decimal("0"))
        header_density = (
            (sum((v * d for v, d in densities), Decimal("0")) / total_v).quantize(Decimal("0.0001"))
            if total_v > 0
            else default_density
        )
        bunker = BunkerOperation(
            leg_id=(leg.id if leg is not None else None),
            vessel_id=vessel.id,
            bdn_number=b["bdn_number"],
            port_locode=b["port_locode"],
            delivery_datetime_utc=_dt(b["delivery_datetime_utc"]),
            fuel_type=b.get("fuel_type") or "MDO",
            mass_t=_dec(b["mass_t"]),
            density_15c_t_m3=header_density,
            status=b.get("status", "valide_master"),
        )
        db.add(bunker)
        await db.flush()
        for a in allocations:
            tank = tanks[a["tank_code"]]
            db.add(
                BunkerTankAllocation(
                    bunker_id=bunker.id,
                    tank_id=tank.id,
                    volume_m3=_dec(a["volume_m3"]),
                    density_t_m3=_dec(a.get("density_t_m3")) or header_density,
                )
            )
        out.append(bunker)
    await db.flush()
    return out


async def _create_flgo(db: AsyncSession, payloads: list[dict], vessel: Vessel) -> list[FlgoReading]:
    from app.services.flgo_sync import derive_tank_code

    out: list[FlgoReading] = []
    for f in payloads:
        reading_dt = _dt(f["reading_datetime"])
        existing = (
            await db.execute(
                select(FlgoReading).where(
                    FlgoReading.vessel_id == vessel.id,
                    FlgoReading.reading_datetime == reading_dt,
                    FlgoReading.action_type == f["action_type"],
                    FlgoReading.product_name == f["product_name"],
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            out.append(existing)
            continue
        reading = FlgoReading(
            vessel_id=vessel.id,
            action_type=f["action_type"],
            product_name=f["product_name"],
            reading_datetime=reading_dt,
            total_volume_m3=_dec(f["total_volume_m3"]),
            total_rob_m3=_dec(f.get("total_rob_m3")),
            remarks=f.get("remarks"),
            source="xlsx_import",
        )
        db.add(reading)
        await db.flush()
        for c in f.get("compartments", ()):
            db.add(
                FlgoTankCompartmentVolume(
                    flgo_reading_id=reading.id,
                    compartment_code=c["compartment_code"],
                    tank_code=derive_tank_code(c["compartment_code"]),
                    volume_m3=_dec(c["volume_m3"]),
                    mass_t=_dec(c.get("mass_t")),
                )
            )
        out.append(reading)
    await db.flush()
    return out


async def load_voyage(db: AsyncSession, name: str) -> LoadedFixture:
    """Charge la fixture ``name`` (« 1CLA5 » / « 1EGB5 » / « bunkers_flgo »).

    Renvoie un :class:`LoadedFixture` avec les objets ORM matérialisés (leg
    ``None`` pour ``bunkers_flgo``) et le bloc ``expected`` du JSON (valeurs
    golden dérivées du dataset — cf. README.md pour la dérivation).
    """
    try:
        path = _FIXTURE_DIR / _FIXTURE_FILES[name]
    except KeyError as exc:
        raise ValueError(f"Fixture inconnue : {name!r} (choix : {sorted(_FIXTURE_FILES)})") from exc
    payload = json.loads(path.read_text(encoding="utf-8"))

    vessel = await _get_or_create_vessel(db, payload["vessel"])
    ports = await _get_or_create_ports(db, payload.get("ports", []))
    leg = None
    if payload.get("leg"):
        leg = await _get_or_create_leg(db, payload["leg"], vessel, ports)
    events = await _create_events(db, payload.get("events", []), vessel, leg) if leg else []
    bunkers = await _create_bunkers(db, payload.get("bunkers", []), vessel, leg)
    flgo_readings = await _create_flgo(db, payload.get("flgo_readings", []), vessel)

    return LoadedFixture(
        name=name,
        vessel=vessel,
        leg=leg,
        events=events,
        bunkers=bunkers,
        flgo_readings=flgo_readings,
        expected=payload.get("expected", {}),
        qc_expected=payload.get("qc_expected", []),
    )
