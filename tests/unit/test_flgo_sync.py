"""Tests unitaires — FLGO (Marad, lecture seule), MRV LOT 7.

Patron des tests unitaires ``services.bunkering``/``services.marad_sync``
existants : moteur SQLite async en mémoire, aucune dépendance réseau (le
client HTTP Marad n'est jamais sollicité ici — testé séparément côté
``app.utils.marad``/``app.routers.marad_router``).
"""

from __future__ import annotations

import asyncio
import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import openpyxl
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — enregistre tous les modèles sur Base.metadata
from app.database import Base
from app.models.bunker import BunkerOperation
from app.models.flgo import FlgoReading, FlgoTankCompartmentVolume
from app.models.vessel import Vessel
from app.services import flgo_sync as fs


def _run(coro_factory):
    async def _runner():
        eng = create_async_engine(
            "sqlite+aiosqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        try:
            async with eng.begin() as c:
                await c.run_sync(Base.metadata.create_all)
            Session = async_sessionmaker(eng, expire_on_commit=False)
            async with Session() as s:
                await coro_factory(s)
        finally:
            await eng.dispose()

    asyncio.run(_runner())


async def _vessel(s, name: str = "Anemos", code: str = "ANE") -> Vessel:
    v = Vessel(code=code, name=name)
    s.add(v)
    await s.flush()
    return v


# ════════════════════════════════════════════════ derive_tank_code


@pytest.mark.parametrize(
    "label,expected",
    [
        ("14 - GO DB B", "14"),  # Anemos : numéro en préfixe
        ("15 - GO DB T", "15"),
        ("16 - GO B", "16"),
        ("17 - GO T", "17"),
        ("19 - GO Overflow", "other"),  # numéro hors 14/15/16/17
        ("GO BD B     Ref:14", "14"),  # Artemis : numéro en suffixe "Ref:NN"
        ("GO BD T     Ref:15", "15"),
        ("GO  B         Ref:16", "16"),
        ("GO  T         Ref:17", "17"),
        ("GO DT B     Ref: 29", "other"),  # espace après "Ref:"
        ("GO EM        Ref:35 ", "other"),  # espace final
        ("nonsense", "other"),
        ("", "other"),
    ],
)
def test_derive_tank_code(label, expected):
    assert fs.derive_tank_code(label) == expected


# ════════════════════════════════════════════════ parse_composite_cell


def test_parse_composite_cell_volume_and_mass():
    c = fs.parse_composite_cell("14.6 m3 (12.76 t)")
    assert c.volume_m3 == Decimal("14.6")
    assert c.mass_t == Decimal("12.76")


def test_parse_composite_cell_zero():
    c = fs.parse_composite_cell("0 m3 (0 t)")
    assert c.volume_m3 == Decimal("0")
    assert c.mass_t == Decimal("0")


def test_parse_composite_cell_without_mass():
    """Cas sans masse — la parenthèse est optionnelle."""
    c = fs.parse_composite_cell("14.6 m3")
    assert c.volume_m3 == Decimal("14.6")
    assert c.mass_t is None


def test_parse_composite_cell_malformed_raises_not_crash():
    """Format inattendu → exception typée (l'appelant la liste, ne plante pas)."""
    with pytest.raises(fs.FlgoCompositeCellError):
        fs.parse_composite_cell("14 litres")
    with pytest.raises(fs.FlgoCompositeCellError):
        fs.parse_composite_cell("")


# ════════════════════════════════════════════════ import_flgo_xlsx (repli manuel)


def _build_xlsx(rows: list[list]) -> bytes:
    """Export IHM Marad FLGO minimal (2 compartiments, même structure que les
    exports réels Anemos_All*.xlsx / FLGO {Anemos,Artemis}.xlsx)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Main sheet"
    ws.append(["NewTOWT"])
    ws.append([])
    ws.append(["TestVessel"])
    ws.append(["All - test range"])
    ws.append([None, "Product"])
    ws.append([None, "Category: Fuel"])
    ws.append([None, "Diesel Oil"])
    ws.append(
        [
            None,
            None,
            "",
            "Operation date",
            "14 - GO DB B",
            "15 - GO DB T",
            "Total volume [m3]",
            "ROB [m3]",
            "Remarks",
            "Docs",
        ]
    )
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_import_xlsx_parses_composite_cells_and_upserts():
    async def _check(s):
        v = await _vessel(s)
        content = _build_xlsx(
            [
                [
                    None,
                    None,
                    "Measurement",
                    "06/07/2026 22:13",
                    "14.6 m3 (12.76 t)",
                    "16.4 m3 (14.33 t)",
                    "31",
                    "31",
                    "",
                    "0",
                ],
            ]
        )
        report = await fs.import_flgo_xlsx(s, v, content)
        assert report.imported == 1
        assert report.updated == 0
        assert report.errors == []

        reading = (await s.execute(select(FlgoReading))).scalar_one()
        assert reading.action_type == "measurement"
        assert reading.product_name == "Diesel Oil"
        assert reading.total_volume_m3 == Decimal("31")
        assert reading.total_rob_m3 == Decimal("31")
        assert reading.source == "xlsx_import"

        comps = (await s.execute(select(FlgoTankCompartmentVolume))).scalars().all()
        assert len(comps) == 2
        by_code = {c.compartment_code: c for c in comps}
        assert by_code["14 - GO DB B"].tank_code == "14"
        assert by_code["14 - GO DB B"].volume_m3 == Decimal("14.6")
        assert by_code["14 - GO DB B"].mass_t == Decimal("12.76")
        assert by_code["15 - GO DB T"].tank_code == "15"

    _run(_check)


def test_import_xlsx_malformed_cell_listed_not_crash():
    """Une cellule composite illisible est LISTÉE en erreur — le reste de la
    ligne (et l'import global) continue normalement (pas de crash)."""

    async def _check(s):
        v = await _vessel(s)
        content = _build_xlsx(
            [
                [
                    None,
                    None,
                    "Measurement",
                    "06/07/2026 22:13",
                    "14.6 m3 (12.76 t)",
                    "garbage",
                    "31",
                    "31",
                    "",
                    "0",
                ],
            ]
        )
        report = await fs.import_flgo_xlsx(s, v, content)
        assert report.imported == 1  # la ligne EST importée (volume total lisible)
        assert len(report.errors) == 1
        assert "15 - GO DB T" in report.errors[0]

        comps = (await s.execute(select(FlgoTankCompartmentVolume))).scalars().all()
        assert len(comps) == 1  # seul le compartiment valide est enregistré

    _run(_check)


def test_import_xlsx_dash_placeholder_is_not_an_error():
    """Le placeholder "-" (constaté dans les exports Marad réels, "pas de
    mesure") est traité comme une cellule vide — jamais une anomalie."""

    async def _check(s):
        v = await _vessel(s)
        content = _build_xlsx(
            [
                [
                    None,
                    None,
                    "Measurement",
                    "06/07/2026 22:13",
                    "14.6 m3 (12.76 t)",
                    "-",
                    "31",
                    "31",
                    "",
                    "0",
                ],
            ]
        )
        report = await fs.import_flgo_xlsx(s, v, content)
        assert report.imported == 1
        assert report.errors == []
        comps = (await s.execute(select(FlgoTankCompartmentVolume))).scalars().all()
        assert len(comps) == 1

    _run(_check)


def test_import_xlsx_unreadable_date_skipped_with_error():
    async def _check(s):
        v = await _vessel(s)
        content = _build_xlsx(
            [
                [
                    None,
                    None,
                    "Measurement",
                    "not-a-date",
                    "14.6 m3 (12.76 t)",
                    "16.4 m3 (14.33 t)",
                    "31",
                    "31",
                    "",
                    "0",
                ],
            ]
        )
        report = await fs.import_flgo_xlsx(s, v, content)
        assert report.imported == 0
        assert report.skipped == 1
        assert len(report.errors) == 1
        assert "date illisible" in report.errors[0]

    _run(_check)


def test_import_xlsx_idempotent_double_import_no_duplicate():
    async def _check(s):
        v = await _vessel(s)
        content = _build_xlsx(
            [
                [
                    None,
                    None,
                    "Measurement",
                    "06/07/2026 22:13",
                    "14.6 m3 (12.76 t)",
                    "16.4 m3 (14.33 t)",
                    "31",
                    "31",
                    "",
                    "0",
                ],
            ]
        )
        r1 = await fs.import_flgo_xlsx(s, v, content)
        assert (r1.imported, r1.updated) == (1, 0)

        r2 = await fs.import_flgo_xlsx(s, v, content)
        assert (r2.imported, r2.updated) == (0, 1)  # re-sync = 0 doublon

        readings = (await s.execute(select(FlgoReading))).scalars().all()
        assert len(readings) == 1
        comps = (await s.execute(select(FlgoTankCompartmentVolume))).scalars().all()
        assert len(comps) == 2  # compartiments remplacés, pas dupliqués

    _run(_check)


def test_import_xlsx_update_when_value_changes():
    async def _check(s):
        v = await _vessel(s)
        content1 = _build_xlsx(
            [
                [
                    None,
                    None,
                    "Measurement",
                    "06/07/2026 22:13",
                    "14.6 m3 (12.76 t)",
                    "16.4 m3 (14.33 t)",
                    "31",
                    "31",
                    "",
                    "0",
                ],
            ]
        )
        await fs.import_flgo_xlsx(s, v, content1)
        # Même clé naturelle, volume corrigé côté Marad.
        content2 = _build_xlsx(
            [
                [
                    None,
                    None,
                    "Measurement",
                    "06/07/2026 22:13",
                    "20 m3 (17.4 t)",
                    "16.4 m3 (14.33 t)",
                    "36.4",
                    "36.4",
                    "",
                    "0",
                ],
            ]
        )
        r2 = await fs.import_flgo_xlsx(s, v, content2)
        assert (r2.imported, r2.updated) == (0, 1)
        reading = (await s.execute(select(FlgoReading))).scalar_one()
        assert reading.total_volume_m3 == Decimal("36.4")
        comps = (await s.execute(select(FlgoTankCompartmentVolume))).scalars().all()
        assert len(comps) == 2
        vol_by_code = {c.compartment_code: c.volume_m3 for c in comps}
        assert vol_by_code["14 - GO DB B"] == Decimal("20")

    _run(_check)


# ════════════════════════════════════════════════ flgo_nearest_reading (R17)


def test_flgo_nearest_reading_within_tolerance():
    async def _check(s):
        v = await _vessel(s)
        base = datetime(2026, 6, 1, tzinfo=UTC)
        await fs._upsert_reading(
            s,
            vessel_id=v.id,
            action_type="measurement",
            product_name="Diesel Oil",
            reading_datetime=base,
            total_volume_m3=Decimal("50"),
            total_rob_m3=Decimal("50"),
            remarks=None,
            source="api",
            compartments=[],
        )
        match = await fs.flgo_nearest_reading(s, v, base + timedelta(hours=10))
        assert match.reading is not None
        assert match.within_tolerance is True
        assert match.delta_hours == Decimal("10")
        assert match.tolerance_hours == Decimal("120")  # défaut R17 (seed lot 2)

    _run(_check)


def test_flgo_nearest_reading_outside_tolerance_downgradable_to_info():
    async def _check(s):
        v = await _vessel(s)
        base = datetime(2026, 6, 1, tzinfo=UTC)
        await fs._upsert_reading(
            s,
            vessel_id=v.id,
            action_type="measurement",
            product_name="Diesel Oil",
            reading_datetime=base,
            total_volume_m3=Decimal("50"),
            total_rob_m3=Decimal("50"),
            remarks=None,
            source="api",
            compartments=[],
        )
        # Tolérance par défaut R17 = 120h (~5j) ; 200h dépasse → hors tolérance
        # (le rapprochement doit être déclassé Info par l'appelant, lot 8).
        match = await fs.flgo_nearest_reading(s, v, base + timedelta(hours=200))
        assert match.reading is not None
        assert match.within_tolerance is False

    _run(_check)


def test_flgo_nearest_reading_no_data_returns_none_reading():
    async def _check(s):
        v = await _vessel(s)
        match = await fs.flgo_nearest_reading(s, v, datetime(2026, 6, 1, tzinfo=UTC))
        assert match.reading is None
        assert match.within_tolerance is False

    _run(_check)


def test_flgo_nearest_reading_ignores_readings_without_rob():
    async def _check(s):
        v = await _vessel(s)
        base = datetime(2026, 6, 1, tzinfo=UTC)
        # Relevé sans ROB (total_rob_m3=None) — inutilisable pour R17.
        await fs._upsert_reading(
            s,
            vessel_id=v.id,
            action_type="measurement",
            product_name="Diesel Oil",
            reading_datetime=base,
            total_volume_m3=Decimal("50"),
            total_rob_m3=None,
            remarks=None,
            source="api",
            compartments=[],
        )
        match = await fs.flgo_nearest_reading(s, v, base)
        assert match.reading is None

    _run(_check)


# ════════════════════════════════════════════════ flgo_matches_for_bunker (R24)


async def _bunker(s, vessel, delivery_dt, bdn="BDN-TEST") -> BunkerOperation:
    b = BunkerOperation(
        vessel_id=vessel.id,
        bdn_number=bdn,
        port_locode="FRFEC",
        delivery_datetime_utc=delivery_dt,
        mass_t=Decimal("35"),
        density_15c_t_m3=Decimal("0.845"),
    )
    s.add(b)
    await s.flush()
    return b


def test_flgo_matches_for_bunker_within_window():
    async def _check(s):
        v = await _vessel(s)
        delivery = datetime(2026, 6, 10, tzinfo=UTC)
        await fs._upsert_reading(
            s,
            vessel_id=v.id,
            action_type="received",
            product_name="Diesel Oil",
            reading_datetime=delivery + timedelta(days=2),
            total_volume_m3=Decimal("40"),
            total_rob_m3=Decimal("80"),
            remarks=None,
            source="api",
            compartments=[],
        )
        bunker = await _bunker(s, v, delivery)
        match = await fs.flgo_matches_for_bunker(s, bunker)
        assert match.matched is True
        assert len(match.candidates) == 1
        assert match.window_days == Decimal("5")  # défaut R24 (seed lot 2)

    _run(_check)


def test_flgo_matches_for_bunker_outside_window():
    async def _check(s):
        v = await _vessel(s)
        delivery = datetime(2026, 6, 10, tzinfo=UTC)
        await fs._upsert_reading(
            s,
            vessel_id=v.id,
            action_type="received",
            product_name="Diesel Oil",
            reading_datetime=delivery + timedelta(days=20),
            total_volume_m3=Decimal("40"),
            total_rob_m3=Decimal("80"),
            remarks=None,
            source="api",
            compartments=[],
        )
        bunker = await _bunker(s, v, delivery, bdn="BDN-OUT")
        match = await fs.flgo_matches_for_bunker(s, bunker)
        assert match.matched is False
        assert match.candidates == ()

    _run(_check)


def test_flgo_matches_for_bunker_ignores_measurement_action_type():
    """Seules les lectures "received" comptent pour R24 (pas les jaugeages)."""

    async def _check(s):
        v = await _vessel(s)
        delivery = datetime(2026, 6, 10, tzinfo=UTC)
        await fs._upsert_reading(
            s,
            vessel_id=v.id,
            action_type="measurement",
            product_name="Diesel Oil",
            reading_datetime=delivery + timedelta(hours=6),
            total_volume_m3=Decimal("40"),
            total_rob_m3=Decimal("80"),
            remarks=None,
            source="api",
            compartments=[],
        )
        bunker = await _bunker(s, v, delivery, bdn="BDN-MEAS")
        match = await fs.flgo_matches_for_bunker(s, bunker)
        assert match.matched is False

    _run(_check)


# ════════════════════════════════════════════════ check_internal_consistency (R25)


def test_check_internal_consistency_ok_within_tolerance():
    async def _check(s):
        v = await _vessel(s)
        reading, _ = await fs._upsert_reading(
            s,
            vessel_id=v.id,
            action_type="measurement",
            product_name="Diesel Oil",
            reading_datetime=datetime(2026, 6, 1, tzinfo=UTC),
            total_volume_m3=Decimal("30"),
            total_rob_m3=Decimal("30"),
            remarks=None,
            source="api",
            compartments=[
                fs.CompartmentInput("A", Decimal("15"), None),
                fs.CompartmentInput("B", Decimal("15"), None),
            ],
        )
        result = await fs.check_internal_consistency(s, reading)
        assert result.flagged is False
        assert result.total_compartments_m3 == Decimal("30")
        assert result.tolerance_m3 == Decimal("2")  # défaut R25 (seed lot 2)

    _run(_check)


def test_check_internal_consistency_flagged_when_over_tolerance_never_corrects():
    async def _check(s):
        v = await _vessel(s)
        reading, _ = await fs._upsert_reading(
            s,
            vessel_id=v.id,
            action_type="measurement",
            product_name="Diesel Oil",
            reading_datetime=datetime(2026, 6, 1, tzinfo=UTC),
            total_volume_m3=Decimal("30"),
            total_rob_m3=Decimal("30"),
            remarks=None,
            source="api",
            compartments=[
                fs.CompartmentInput("A", Decimal("15"), None),
                fs.CompartmentInput("B", Decimal("10"), None),
            ],
        )
        # Σ = 25 vs déclaré 30 → écart 5 > tolérance par défaut 2 m3.
        result = await fs.check_internal_consistency(s, reading)
        assert result.flagged is True
        assert result.delta_m3 == Decimal("5")
        # SIGNALE, ne corrige jamais : le total déclaré reste inchangé.
        assert reading.total_volume_m3 == Decimal("30")

    _run(_check)


def test_check_internal_consistency_accepts_preloaded_compartments():
    """Évite le N+1 : accepte une liste de compartiments déjà chargée."""

    async def _check(s):
        v = await _vessel(s)
        reading, _ = await fs._upsert_reading(
            s,
            vessel_id=v.id,
            action_type="measurement",
            product_name="Diesel Oil",
            reading_datetime=datetime(2026, 6, 1, tzinfo=UTC),
            total_volume_m3=Decimal("10"),
            total_rob_m3=Decimal("10"),
            remarks=None,
            source="api",
            compartments=[fs.CompartmentInput("A", Decimal("10"), None)],
        )
        preloaded = (
            (
                await s.execute(
                    select(FlgoTankCompartmentVolume).where(
                        FlgoTankCompartmentVolume.flgo_reading_id == reading.id
                    )
                )
            )
            .scalars()
            .all()
        )
        result = await fs.check_internal_consistency(s, reading, compartments=preloaded)
        assert result.flagged is False

    _run(_check)


# ════════════════════════════════════════════════ sync_flgo_from_api — no-op


def test_sync_flgo_from_api_noop_when_not_configured(monkeypatch):
    from app.utils import marad

    monkeypatch.setattr(marad, "enabled", lambda: False)

    async def _check(s):
        result = await fs.sync_flgo_from_api(s)
        assert result["configured"] is False
        assert result["imported"] == 0

    _run(_check)


def test_sync_flgo_from_api_upserts_from_mocked_client(monkeypatch):
    """Câblage API : mappe ProductName/ActionType/Date/TotalVolumeMeasuredM3/
    TotalROBM3 (schéma confirmé, cf. app.utils.marad.list_flgo) vers FlgoReading."""
    from app.utils import marad

    monkeypatch.setattr(marad, "enabled", lambda: True)

    async def _fake_list_flgo(vessel_name, date_from, date_to):
        assert vessel_name == "anemos"
        return [
            {
                "ProductName": "Diesel Oil",
                "CategoryName": "Fuel",
                "ActionType": "Measurement",
                "Date": "2026-07-06T22:13:13.443",
                "TotalVolumeMeasuredM3": 63.7,
                "TotalROBM3": 63.7,
                "ActionPerContainers": "[List]",  # forme non exploitable → ignorée
                "TotalVolumeReceivedM3": None,
                "TotalVolumeDeliveredM3": None,
            }
        ]

    monkeypatch.setattr(marad, "list_flgo", _fake_list_flgo)

    async def _check(s):
        v = await _vessel(s)
        result = await fs.sync_flgo_from_api(s, vessels=[v])
        assert result["configured"] is True
        assert result["fetched"] == 1
        assert result["imported"] == 1
        assert result["errors"] == 0

        reading = (await s.execute(select(FlgoReading))).scalar_one()
        assert reading.action_type == "measurement"
        assert reading.product_name == "Diesel Oil"
        assert reading.total_volume_m3 == Decimal("63.7")
        assert reading.total_rob_m3 == Decimal("63.7")
        assert reading.source == "api"

        # Re-sync : idempotent, pas de doublon.
        result2 = await fs.sync_flgo_from_api(s, vessels=[v])
        assert (result2["imported"], result2["updated"]) == (0, 1)
        readings = (await s.execute(select(FlgoReading))).scalars().all()
        assert len(readings) == 1

    _run(_check)


def test_sync_flgo_from_api_bad_record_counted_as_skipped(monkeypatch):
    from app.utils import marad

    monkeypatch.setattr(marad, "enabled", lambda: True)

    async def _fake_list_flgo(vessel_name, date_from, date_to):
        return [
            {"ProductName": "Diesel Oil"},  # sans ActionType/Date/volume → ignoré
            "not-a-dict",  # forme totalement invalide → ignoré
        ]

    monkeypatch.setattr(marad, "list_flgo", _fake_list_flgo)

    async def _check(s):
        v = await _vessel(s)
        result = await fs.sync_flgo_from_api(s, vessels=[v])
        assert result["fetched"] == 2
        assert result["imported"] == 0
        assert result["skipped"] == 2
        assert result["errors"] == 0
        readings = (await s.execute(select(FlgoReading))).scalars().all()
        assert readings == []

    _run(_check)
