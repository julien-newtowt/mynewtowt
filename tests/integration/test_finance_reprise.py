"""Finance / KPI P0 — reprise (FIN-01/02/03) : tests d'intégration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.finance import LegFinance
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel


class _Req:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    url = SimpleNamespace(query="")


async def _setup_leg(db):
    db.add(Vessel(id=1, code="ANE", name="Anemos"))
    db.add(Port(id=1, locode="FRFEC", name="Fécamp", country="FR"))
    db.add(Port(id=2, locode="BRSSO", name="Santos", country="BR"))
    await db.flush()
    base = datetime(2026, 4, 1, tzinfo=UTC)
    leg = Leg(id=1, leg_code="1CFRBR6", vessel_id=1, departure_port_id=1, arrival_port_id=2,
              etd_ref=base, eta_ref=base + timedelta(days=20), etd=base, eta=base + timedelta(days=20))
    db.add(leg)
    await db.flush()
    return leg


# ─────────────────────────────── FIN-01 ───────────────────────────────


@pytest.mark.asyncio
async def test_finance_upsert_forecast_actual_and_variance(db, staff_user):
    from app.routers.finance_router import finance_leg_upsert

    await _setup_leg(db)
    resp = await finance_leg_upsert(
        1, _Req(),
        revenue_eur="10000", port_fees_eur="2000", docker_costs_eur="1500",
        opex_share_eur="3000", other_costs_eur="500",
        revenue_forecast_eur="9000", port_fees_forecast_eur="1800",
        docker_costs_forecast_eur="1200", opex_share_forecast_eur="3200",
        other_costs_forecast_eur="400", notes="budget Q2",
        db=db, user=staff_user,
    )
    assert resp.status_code == 303
    fin = (
        await db.execute(LegFinance.__table__.select().where(LegFinance.__table__.c.leg_id == 1))
    ).fetchone()
    # Réel : marge = 10000 - 2000 - 1500 - 3000 - 500 = 3000
    assert float(fin.margin_eur) == 3000.0
    # Prév : marge = 9000 - 1800 - 1200 - 3200 - 400 = 2400
    assert float(fin.margin_forecast_eur) == 2400.0

    # Propriétés d'écart sur l'objet ORM.
    obj = await db.get(LegFinance, fin.id)
    assert obj.revenue_variance_eur == Decimal("1000.00")   # 10000 - 9000
    assert obj.margin_variance_eur == Decimal("600.00")     # 3000 - 2400


def test_finance_variance_properties_pure():
    f = LegFinance(
        leg_id=1, revenue_eur=Decimal("100"), revenue_forecast_eur=Decimal("80"),
        port_fees_eur=Decimal("30"), port_fees_forecast_eur=Decimal("25"),
        margin_eur=Decimal("20"), margin_forecast_eur=Decimal("15"),
    )
    assert f.revenue_variance_eur == Decimal("20")
    assert f.port_fees_variance_eur == Decimal("5")
    assert f.margin_variance_eur == Decimal("5")


# ─────────────────────────────── FIN-02 ───────────────────────────────


@pytest.mark.asyncio
async def test_finance_csv_export_18_columns(db, staff_user):
    from app.routers.finance_router import _CSV_HEADERS, finance_export_csv

    await _setup_leg(db)
    db.add(LegFinance(leg_id=1, revenue_eur=Decimal("10000"), revenue_forecast_eur=Decimal("9000"),
                      margin_eur=Decimal("3000"), margin_forecast_eur=Decimal("2400")))
    await db.flush()

    resp = await finance_export_csv(_Req(), db=db, user=staff_user)
    assert resp.media_type == "text/csv"
    text = resp.body.decode()
    header = text.splitlines()[0]
    assert len(_CSV_HEADERS) == 19  # leg_code + 6 postes × 3
    assert "revenue_forecast" in header and "margin_variance" in header
    assert "1CFRBR6" in text
    # ligne de données : écart marge = 3000 - 2400 = 600
    assert "600" in text


# ─────────────────────────────── FIN-03 ───────────────────────────────


def test_emissions_estimate_avoided_pure():
    from app.services.emissions import estimate_avoided

    # cargo 100 t × 1000 nm = 100000 t·nm.
    res = estimate_avoided(cargo_t=100, distance_nm=1000)
    # NOx évité = 100000 × (0.000406 - 0.0000528) = 35.32 kg
    assert res.nox_avoided_kg == Decimal("35.320")
    # SOx évité = 100000 × (0.0000812 - 0.00001056) = 7.064 kg
    assert res.sox_avoided_kg == Decimal("7.064")
    assert res.nox_conventional_kg > res.nox_sail_kg


def test_emissions_zero_when_missing_inputs():
    from app.services.emissions import estimate_avoided

    res = estimate_avoided(cargo_t=None, distance_nm=1000)
    assert res.nox_avoided_kg == Decimal("0.000")
    res = estimate_avoided(cargo_t=100, distance_nm=None)
    assert res.sox_avoided_kg == Decimal("0.000")


@pytest.mark.asyncio
async def test_emission_factors_fallback_to_defaults(db):
    """Sans paramètre en base, on retombe sur les constantes V2."""
    from app.services.emissions import (
        CONV_NOX_PER_TNM,
        get_emission_factors,
    )

    factors = await get_emission_factors(db)
    assert factors.conv_nox == CONV_NOX_PER_TNM


@pytest.mark.asyncio
async def test_emission_factors_read_from_co2_variables(db):
    """Un facteur admin courant surcharge la constante par défaut."""
    from datetime import date

    from app.models.co2_variable import Co2Variable
    from app.services.emissions import NOX_CONV_VAR, get_emission_factors

    db.add(Co2Variable(name=NOX_CONV_VAR, value=Decimal("0.000500"), unit="kg/tnm",
                       effective_date=date(2026, 1, 1), is_current=True))
    await db.flush()
    factors = await get_emission_factors(db)
    assert factors.conv_nox == Decimal("0.000500")
