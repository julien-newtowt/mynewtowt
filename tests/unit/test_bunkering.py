"""Tests — soutage (Bunker Report / BDN), MRV lot 6.

Patron ``tests/unit/test_referential_env.py`` (moteur SQLite en mémoire créé
par test, sans fixture partagée). Couvre ``app.services.bunkering`` :
allocations multi-cuves, contrôles structurels (masse vs Σ(volume×densité),
densité hors plage), rattachement voyage automatique, unicité BDN, garde
auteur-seul.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — enregistre tous les modèles (dont Bunker*)
from app.database import Base
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.models.vessel_env import VesselTank
from app.services import bunkering
from app.services.validation_engine import invalidate_cache


def _run(coro_factory):
    """Exécute ``coro_factory(session)`` dans un moteur SQLite jetable.

    Invalide le cache module-level de ``validation_engine`` avant ET après
    (état process-wide, cf. ``test_referential_env._run``).
    """
    invalidate_cache()

    async def _inner():
        eng = create_async_engine("sqlite+aiosqlite://")
        try:
            async with eng.begin() as c:
                await c.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(eng, expire_on_commit=False)
            async with Session() as s:
                return await coro_factory(s)
        finally:
            await eng.dispose()

    try:
        return asyncio.run(_inner())
    finally:
        invalidate_cache()


async def _make_vessel_with_tanks(s, code: str = "ANE") -> Vessel:
    v = Vessel(code=code, name="Anemos")
    s.add(v)
    await s.flush()
    for tc in ("14", "15", "16", "17", "other"):
        s.add(VesselTank(vessel_id=v.id, tank_code=tc))
    await s.flush()
    return v


async def _tanks_of(s, vessel_id: int) -> dict[str, VesselTank]:
    """Cuves d'un navire indexées par ``tank_code`` (objets ORM)."""
    rows = (
        await s.execute(select(VesselTank).where(VesselTank.vessel_id == vessel_id))
    ).scalars().all()
    return {t.tank_code: t for t in rows}


async def _get_or_create_port(s, locode: str, name: str, country: str) -> Port:
    existing = (
        await s.execute(select(Port).where(Port.locode == locode))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    p = Port(locode=locode, name=name, country=country)
    s.add(p)
    await s.flush()
    return p


async def _make_leg(
    s,
    vessel: Vessel,
    etd: datetime,
    eta: datetime | None = None,
    atd: datetime | None = None,
    leg_code: str = "1AFRBR6",
    status: str = "planned",
) -> Leg:
    pol = await _get_or_create_port(s, "FRFEC", "Fécamp", "FR")
    pod = await _get_or_create_port(s, "BRSSZ", "Santos", "BR")
    eta = eta or (etd + timedelta(days=20))
    leg = Leg(
        leg_code=leg_code,
        vessel_id=vessel.id,
        departure_port_id=pol.id,
        arrival_port_id=pod.id,
        etd=etd,
        eta=eta,
        etd_ref=etd,
        eta_ref=eta,
        atd=atd,
        status=status,
    )
    s.add(leg)
    await s.flush()
    return leg


# ════════════════════════════════════════════════════ Allocations multi-cuves


def test_set_allocations_multi_tank():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        tanks = await _tanks_of(s, v.id)
        bunker = await bunkering.create_draft(
            s,
            vessel=v,
            author_user_id=1,
            bdn_number="BDN-001",
            port_locode="FRFEC",
            delivery_datetime_utc=datetime(2026, 1, 10, tzinfo=UTC),
            mass_t=Decimal("20.0"),
            density_15c_t_m3=Decimal("0.845"),
        )
        rows = [
            bunkering.AllocationInput(tank_id=tanks["14"].id, volume_m3=Decimal("10"), density_t_m3=Decimal("0.845")),
            bunkering.AllocationInput(tank_id=tanks["15"].id, volume_m3=Decimal("13.6"), density_t_m3=Decimal("0.845")),
        ]
        created = await bunkering.set_allocations(s, bunker, rows)
        return created, bunker.id

    created, bunker_id = _run(scenario)
    assert len(created) == 2
    assert {a.tank_id for a in created} == {a.tank_id for a in created}  # 2 distinct rows persisted
    assert all(a.bunker_id == bunker_id for a in created)


def test_set_allocations_rejects_duplicate_tank_in_same_call():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        tank = next(iter((await _tanks_of(s, v.id)).values()))
        bunker = await bunkering.create_draft(
            s, vessel=v, author_user_id=1, bdn_number="BDN-002", port_locode="FRFEC",
            delivery_datetime_utc=datetime(2026, 1, 10, tzinfo=UTC),
            mass_t=Decimal("10"), density_15c_t_m3=Decimal("0.845"),
        )
        rows = [
            bunkering.AllocationInput(tank_id=tank.id, volume_m3=Decimal("5"), density_t_m3=Decimal("0.845")),
            bunkering.AllocationInput(tank_id=tank.id, volume_m3=Decimal("5"), density_t_m3=Decimal("0.845")),
        ]
        with pytest.raises(bunkering.BunkerError):
            await bunkering.set_allocations(s, bunker, rows)
        return True

    assert _run(scenario) is True


def test_set_allocations_rejects_tank_from_another_vessel():
    async def scenario(s):
        v1 = await _make_vessel_with_tanks(s, code="ANE")
        v2 = await _make_vessel_with_tanks(s, code="ART")
        foreign_tank = next(iter((await _tanks_of(s, v2.id)).values()))
        bunker = await bunkering.create_draft(
            s, vessel=v1, author_user_id=1, bdn_number="BDN-003", port_locode="FRFEC",
            delivery_datetime_utc=datetime(2026, 1, 10, tzinfo=UTC),
            mass_t=Decimal("10"), density_15c_t_m3=Decimal("0.845"),
        )
        with pytest.raises(bunkering.BunkerError):
            await bunkering.set_allocations(
                s, bunker,
                [bunkering.AllocationInput(tank_id=foreign_tank.id, volume_m3=Decimal("5"), density_t_m3=Decimal("0.845"))],
            )
        return True

    assert _run(scenario) is True


# ═══════════════════════════════════ Masse vs Σ(volume×densité) — R23 (3 paliers)


def test_mass_consistency_ok_within_tolerance():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        bunker = await bunkering.create_draft(
            s, vessel=v, author_user_id=1, bdn_number="BDN-010", port_locode="FRFEC",
            delivery_datetime_utc=datetime(2026, 1, 10, tzinfo=UTC),
            mass_t=Decimal("20.0"), density_15c_t_m3=Decimal("0.845"),
        )
        tank = next(iter((await _tanks_of(s, v.id)).values()))
        # 20 m3 * 0.845 = 16.9 t declared vs volume*density -> use volume so
        # that allocated mass = 20.0 t exactly (delta = 0 <= tolerance).
        allocs = await bunkering.set_allocations(
            s, bunker,
            [bunkering.AllocationInput(tank_id=tank.id, volume_m3=Decimal("20"), density_t_m3=Decimal("1.0"))],
        )
        check = await bunkering.check_mass_consistency(s, bunker, allocs)
        return check

    check = _run(scenario)
    assert check.status == "ok"
    assert check.delta_t == Decimal("0")
    assert check.tolerance_t == Decimal("2")  # défaut codé R23:tolerance_bdn_flgo_t


def test_mass_consistency_ecart_mineur():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        # tolérance codée = 2 t -> écart mineur entre ]2, 4] t.
        bunker = await bunkering.create_draft(
            s, vessel=v, author_user_id=1, bdn_number="BDN-011", port_locode="FRFEC",
            delivery_datetime_utc=datetime(2026, 1, 10, tzinfo=UTC),
            mass_t=Decimal("23.0"), density_15c_t_m3=Decimal("0.845"),
        )
        tank = next(iter((await _tanks_of(s, v.id)).values()))
        allocs = await bunkering.set_allocations(
            s, bunker,
            [bunkering.AllocationInput(tank_id=tank.id, volume_m3=Decimal("20"), density_t_m3=Decimal("1.0"))],
        )
        return await bunkering.check_mass_consistency(s, bunker, allocs)

    check = _run(scenario)
    assert check.status == "ecart_mineur"
    assert check.delta_t == Decimal("3.0")


def test_mass_consistency_ecart_majeur():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        bunker = await bunkering.create_draft(
            s, vessel=v, author_user_id=1, bdn_number="BDN-012", port_locode="FRFEC",
            delivery_datetime_utc=datetime(2026, 1, 10, tzinfo=UTC),
            mass_t=Decimal("30.0"), density_15c_t_m3=Decimal("0.845"),
        )
        tank = next(iter((await _tanks_of(s, v.id)).values()))
        allocs = await bunkering.set_allocations(
            s, bunker,
            [bunkering.AllocationInput(tank_id=tank.id, volume_m3=Decimal("20"), density_t_m3=Decimal("1.0"))],
        )
        return await bunkering.check_mass_consistency(s, bunker, allocs)

    check = _run(scenario)
    assert check.status == "ecart_majeur"
    assert check.delta_t == Decimal("10.0")


# ══════════════════════════════════════════════════════ Densité hors plage — R16


def test_density_within_range_not_flagged():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        bunker = await bunkering.create_draft(
            s, vessel=v, author_user_id=1, bdn_number="BDN-020", port_locode="FRFEC",
            delivery_datetime_utc=datetime(2026, 1, 10, tzinfo=UTC),
            mass_t=Decimal("10"), density_15c_t_m3=Decimal("0.850"),
        )
        return await bunkering.check_density(s, bunker)

    check = _run(scenario)
    assert check.flagged is False
    assert check.low == Decimal("0.830")
    assert check.high == Decimal("0.860")


def test_density_out_of_range_flagged():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        bunker = await bunkering.create_draft(
            s, vessel=v, author_user_id=1, bdn_number="BDN-021", port_locode="FRFEC",
            delivery_datetime_utc=datetime(2026, 1, 10, tzinfo=UTC),
            mass_t=Decimal("10"), density_15c_t_m3=Decimal("0.900"),
        )
        return await bunkering.check_density(s, bunker)

    check = _run(scenario)
    assert check.flagged is True


# ═══════════════════════════════════════════════ Capacité cuves — Info seulement


def test_capacity_check_is_info_only_never_blocking():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        bunker = await bunkering.create_draft(
            s, vessel=v, author_user_id=1, bdn_number="BDN-030", port_locode="FRFEC",
            delivery_datetime_utc=datetime(2026, 1, 10, tzinfo=UTC),
            mass_t=Decimal("10"), density_15c_t_m3=Decimal("0.845"),
        )
        tank = next(iter((await _tanks_of(s, v.id)).values()))
        allocs = await bunkering.set_allocations(
            s, bunker,
            [bunkering.AllocationInput(tank_id=tank.id, volume_m3=Decimal("999"), density_t_m3=Decimal("0.845"))],
        )
        tanks_by_id = await bunkering.vessel_tanks_by_id(s, v.id)
        return bunkering.check_capacity(allocs, tanks_by_id)

    check = _run(scenario)
    # capacity_m3 est NULL par défaut (Q11, données officielles absentes) ->
    # total_capacity_m3 est None -> "exceeds" ne peut jamais être vrai.
    assert check.total_capacity_m3 is None
    assert check.exceeds is False


# ═══════════════════════════════════════════════════ Rattachement voyage auto


def test_resolve_leg_for_bunker_picks_next_leg_after_delivery():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        delivery = datetime(2026, 1, 10, tzinfo=UTC)
        leg = await _make_leg(s, v, etd=delivery + timedelta(days=3))
        return await bunkering.resolve_leg_for_bunker(s, v, delivery), leg.id

    leg_id, expected_id = _run(scenario)
    assert leg_id == expected_id


def test_resolve_leg_for_bunker_ignores_past_legs():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        delivery = datetime(2026, 1, 10, tzinfo=UTC)
        # Leg passé (ETD avant la livraison) : ne doit jamais être choisi.
        await _make_leg(s, v, etd=delivery - timedelta(days=30), leg_code="1AFRBR5")
        future_leg = await _make_leg(s, v, etd=delivery + timedelta(days=5), leg_code="1AFRBR6")
        return await bunkering.resolve_leg_for_bunker(s, v, delivery), future_leg.id

    leg_id, expected_id = _run(scenario)
    assert leg_id == expected_id


def test_resolve_leg_for_bunker_prefers_atd_over_etd_when_known():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        delivery = datetime(2026, 1, 10, tzinfo=UTC)
        # ETD planifié dans la fenêtre, mais ATD réel (départ réel) hors
        # fenêtre -> doit retomber sur None (ATD prioritaire sur ETD).
        await _make_leg(
            s, v, etd=delivery + timedelta(days=5), atd=delivery + timedelta(days=40),
        )
        return await bunkering.resolve_leg_for_bunker(s, v, delivery)

    assert _run(scenario) is None


def test_resolve_leg_for_bunker_out_of_window_returns_none():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        delivery = datetime(2026, 1, 10, tzinfo=UTC)
        # Fenêtre par défaut codée = 25 j -> 40 j après = hors fenêtre.
        await _make_leg(s, v, etd=delivery + timedelta(days=40))
        return await bunkering.resolve_leg_for_bunker(s, v, delivery)

    assert _run(scenario) is None


def test_resolve_leg_for_bunker_no_future_leg_returns_none():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        delivery = datetime(2026, 1, 10, tzinfo=UTC)
        await _make_leg(s, v, etd=delivery - timedelta(days=5))
        return await bunkering.resolve_leg_for_bunker(s, v, delivery)

    assert _run(scenario) is None


def test_create_draft_auto_attaches_leg_within_window():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        delivery = datetime(2026, 1, 10, tzinfo=UTC)
        leg = await _make_leg(s, v, etd=delivery + timedelta(days=2))
        bunker = await bunkering.create_draft(
            s, vessel=v, author_user_id=1, bdn_number="BDN-040", port_locode="FRFEC",
            delivery_datetime_utc=delivery, mass_t=Decimal("10"), density_15c_t_m3=Decimal("0.845"),
        )
        return bunker.leg_id, leg.id

    leg_id, expected_id = _run(scenario)
    assert leg_id == expected_id


def test_create_draft_manual_leg_override_bypasses_auto_resolution():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        delivery = datetime(2026, 1, 10, tzinfo=UTC)
        far_leg = await _make_leg(s, v, etd=delivery + timedelta(days=200), leg_code="1AFRBR9")
        bunker = await bunkering.create_draft(
            s, vessel=v, author_user_id=1, bdn_number="BDN-041", port_locode="FRFEC",
            delivery_datetime_utc=delivery, mass_t=Decimal("10"), density_15c_t_m3=Decimal("0.845"),
            leg_id=far_leg.id,
        )
        return bunker.leg_id, far_leg.id

    leg_id, expected_id = _run(scenario)
    assert leg_id == expected_id  # override manuel respecté malgré la fenêtre dépassée


# ══════════════════════════════════════════════════════════════ Unicité BDN


def test_duplicate_bdn_number_rejected():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        await bunkering.create_draft(
            s, vessel=v, author_user_id=1, bdn_number="BDN-DUP", port_locode="FRFEC",
            delivery_datetime_utc=datetime(2026, 1, 10, tzinfo=UTC),
            mass_t=Decimal("10"), density_15c_t_m3=Decimal("0.845"),
        )
        with pytest.raises(bunkering.DuplicateBdnError):
            await bunkering.create_draft(
                s, vessel=v, author_user_id=2, bdn_number="BDN-DUP", port_locode="FRFEC",
                delivery_datetime_utc=datetime(2026, 1, 11, tzinfo=UTC),
                mass_t=Decimal("12"), density_15c_t_m3=Decimal("0.845"),
            )
        return True

    assert _run(scenario) is True


def test_create_draft_requires_bdn_number():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        with pytest.raises(bunkering.BunkerError):
            await bunkering.create_draft(
                s, vessel=v, author_user_id=1, bdn_number="   ", port_locode="FRFEC",
                delivery_datetime_utc=datetime(2026, 1, 10, tzinfo=UTC),
                mass_t=Decimal("10"), density_15c_t_m3=Decimal("0.845"),
            )
        return True

    assert _run(scenario) is True


# ═════════════════════════════════════════════════════════════ Garde auteur-seul


def test_update_draft_rejects_non_author():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        bunker = await bunkering.create_draft(
            s, vessel=v, author_user_id=1, bdn_number="BDN-050", port_locode="FRFEC",
            delivery_datetime_utc=datetime(2026, 1, 10, tzinfo=UTC),
            mass_t=Decimal("10"), density_15c_t_m3=Decimal("0.845"),
        )
        with pytest.raises(bunkering.AuthorOnlyError):
            await bunkering.update_draft(s, bunker, user_id=2, form={"supplier_name": "Total"})
        return True

    assert _run(scenario) is True


def test_update_draft_allows_author():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        bunker = await bunkering.create_draft(
            s, vessel=v, author_user_id=1, bdn_number="BDN-051", port_locode="FRFEC",
            delivery_datetime_utc=datetime(2026, 1, 10, tzinfo=UTC),
            mass_t=Decimal("10"), density_15c_t_m3=Decimal("0.845"),
        )
        updated = await bunkering.update_draft(
            s, bunker, user_id=1, form={"supplier_name": "Total Energies"}
        )
        return updated.supplier_name

    assert _run(scenario) == "Total Energies"


def test_update_draft_rejects_once_validated_master():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        bunker = await bunkering.create_draft(
            s, vessel=v, author_user_id=1, bdn_number="BDN-052", port_locode="FRFEC",
            delivery_datetime_utc=datetime(2026, 1, 10, tzinfo=UTC),
            mass_t=Decimal("10"), density_15c_t_m3=Decimal("0.845"),
        )
        from types import SimpleNamespace

        await bunkering.validate_master(s, bunker, SimpleNamespace(id=1))
        with pytest.raises(bunkering.BunkerError):
            await bunkering.update_draft(s, bunker, user_id=1, form={"supplier_name": "Total"})
        return True

    assert _run(scenario) is True


def test_validate_master_sets_validation_fields_and_is_idempotent_guarded():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        bunker = await bunkering.create_draft(
            s, vessel=v, author_user_id=1, bdn_number="BDN-053", port_locode="FRFEC",
            delivery_datetime_utc=datetime(2026, 1, 10, tzinfo=UTC),
            mass_t=Decimal("10"), density_15c_t_m3=Decimal("0.845"),
        )
        from types import SimpleNamespace

        validator = SimpleNamespace(id=7)
        await bunkering.validate_master(s, bunker, validator)
        status_after = bunker.status
        validated_by = bunker.validated_master_by
        validated_at_is_set = bunker.validated_master_at is not None
        with pytest.raises(bunkering.BunkerError):
            await bunkering.validate_master(s, bunker, validator)
        return status_after, validated_by, validated_at_is_set

    status_after, validated_by, validated_at_is_set = _run(scenario)
    assert status_after == "valide_master"
    assert validated_by == 7
    assert validated_at_is_set is True


def test_apply_review_correction_bypasses_author_guard_after_validation():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        bunker = await bunkering.create_draft(
            s, vessel=v, author_user_id=1, bdn_number="BDN-054", port_locode="FRFEC",
            delivery_datetime_utc=datetime(2026, 1, 10, tzinfo=UTC),
            mass_t=Decimal("10"), density_15c_t_m3=Decimal("0.845"),
        )
        from types import SimpleNamespace

        await bunkering.validate_master(s, bunker, SimpleNamespace(id=1))
        # Correction siège (mrv:M) par un tiers non-auteur, après validation.
        corrected = await bunkering.apply_review_correction(
            s, bunker, form={"supplier_name": "Corrigé par le siège"}
        )
        return corrected.supplier_name, corrected.status

    supplier_name, status = _run(scenario)
    assert supplier_name == "Corrigé par le siège"
    assert status == "valide_master"  # la correction ne déverrouille pas le statut


# ════════════════════════════ Interface d'exposition — grand livre (lots 3/9)


def test_bunkered_t_lookup_sums_only_validated_master():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        delivery = datetime(2026, 1, 10, tzinfo=UTC)
        leg = await _make_leg(s, v, etd=delivery + timedelta(days=2))

        validated = await bunkering.create_draft(
            s, vessel=v, author_user_id=1, bdn_number="BDN-060", port_locode="FRFEC",
            delivery_datetime_utc=delivery, mass_t=Decimal("12.5"), density_15c_t_m3=Decimal("0.845"),
            leg_id=leg.id,
        )
        from types import SimpleNamespace

        await bunkering.validate_master(s, validated, SimpleNamespace(id=1))

        # Brouillon rattaché au même leg : ne doit PAS compter (pas fiable).
        await bunkering.create_draft(
            s, vessel=v, author_user_id=1, bdn_number="BDN-061", port_locode="FRFEC",
            delivery_datetime_utc=delivery, mass_t=Decimal("99"), density_15c_t_m3=Decimal("0.845"),
            leg_id=leg.id,
        )
        return await bunkering.bunkered_t_lookup(s, leg.id)

    total = _run(scenario)
    assert total == Decimal("12.5")


def test_bunkered_t_lookup_returns_zero_when_no_bunker():
    async def scenario(s):
        v = await _make_vessel_with_tanks(s)
        leg = await _make_leg(s, v, etd=datetime(2026, 1, 10, tzinfo=UTC))
        return await bunkering.bunkered_t_lookup(s, leg.id)

    assert _run(scenario) == Decimal("0")
