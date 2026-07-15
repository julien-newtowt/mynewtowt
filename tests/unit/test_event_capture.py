"""Tests — cycle de vie déclaratif des événements MRV (LOT 3).

Couvre ``app.services.event_capture`` : création/reprise de brouillon
(idempotence ``client_uuid``), garde auteur-seul (D11), autosave, calcul
``datetime_utc`` (local+tz, cas DST + tz mixtes), finalisation (bloquée si
règle bloquante fail — R01 ; OK sinon + QualityCheckResult persistés),
validation, préremplissage de position (Thalos).

Moteur SQLite en mémoire (FK activées) + seed du référentiel de validation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — enregistre les modèles sur Base.metadata
from app.database import Base
from app.models.claim import VesselPosition
from app.models.leg import Leg
from app.models.nav_event import NavEvent, NoonEvent
from app.models.port import Port
from app.models.user import User
from app.models.validation import QualityCheckResult
from app.models.vessel import Vessel
from app.services import event_capture
from app.services.validation_engine import invalidate_cache, seed_reference_data


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _rec):  # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    invalidate_cache()
    await seed_reference_data(session)
    invalidate_cache()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()
        invalidate_cache()


async def _base(db):
    """Crée user + vessel + ports + leg, renvoie (author, vessel, leg).

    Le leg référence toujours un vrai navire (FK NOT NULL) ; le test R01 crée
    en revanche l'ÉVÉNEMENT avec ``vessel=None`` pour l'identité incomplète.
    """
    author = User(username="master", email="m@t.test", hashed_password="x", role="marins")
    db.add(author)
    vessel = Vessel(code="ANE", name="Anemos")
    db.add(vessel)
    p1 = Port(name="Fecamp", country="FR", locode="FRFEC", latitude=49.7, longitude=0.37)
    p2 = Port(name="Belem", country="BR", locode="BRBEL", latitude=-1.45, longitude=-48.5)
    db.add_all([p1, p2])
    await db.flush()
    leg = Leg(
        leg_code="1AFRBR6",
        vessel_id=vessel.id,
        departure_port_id=p1.id,
        arrival_port_id=p2.id,
        etd_ref=datetime(2026, 1, 1, tzinfo=UTC),
        eta_ref=datetime(2026, 1, 10, tzinfo=UTC),
        etd=datetime(2026, 1, 1, tzinfo=UTC),
        eta=datetime(2026, 1, 10, tzinfo=UTC),
    )
    db.add(leg)
    await db.flush()
    return author, vessel, leg


# ════════════════════════════════════════════ create_draft / idempotence


async def test_create_draft_sets_defaults(db):
    author, vessel, leg = await _base(db)
    ev = await event_capture.create_draft(
        db,
        leg=leg,
        vessel=vessel,
        event_type="noon",
        author=author,
        payload={"datetime_local": datetime(2026, 1, 2, 12, 0), "timezone": "UTC"},
    )
    assert isinstance(ev, NoonEvent)
    assert ev.status == "brouillon"
    assert ev.author_user_id == author.id
    assert ev.last_saved_at is not None
    # datetime_utc calculé dès le brouillon (best-effort).
    assert ev.datetime_utc == datetime(2026, 1, 2, 12, 0, tzinfo=UTC)


async def test_create_draft_client_uuid_idempotent(db):
    author, vessel, leg = await _base(db)
    e1 = await event_capture.create_draft(
        db,
        leg=leg,
        vessel=vessel,
        event_type="noon",
        author=author,
        payload={"timezone": "UTC"},
        client_uuid="abc-123",
    )
    e2 = await event_capture.create_draft(
        db,
        leg=leg,
        vessel=vessel,
        event_type="noon",
        author=author,
        payload={"timezone": "UTC"},
        client_uuid="abc-123",
    )
    assert e1.id == e2.id
    count = (await db.execute(select(func.count()).select_from(NavEvent))).scalar_one()
    assert count == 1


async def test_create_draft_rejects_unknown_type(db):
    author, vessel, leg = await _base(db)
    with pytest.raises(event_capture.EventCaptureError):
        await event_capture.create_draft(
            db,
            leg=leg,
            vessel=vessel,
            event_type="banquet",
            author=author,
            payload={},
        )


# ════════════════════════════════════════════ garde auteur-seul (D11)


async def test_update_draft_author_only(db):
    author, vessel, leg = await _base(db)
    other = User(username="other", email="o@t.test", hashed_password="x", role="marins")
    db.add(other)
    await db.flush()
    ev = await event_capture.create_draft(
        db,
        leg=leg,
        vessel=vessel,
        event_type="noon",
        author=author,
        payload={"timezone": "UTC"},
    )
    # L'auteur peut modifier.
    await event_capture.update_draft(db, ev, author, {"comments": "ok"})
    assert ev.comments == "ok"
    # Un autre utilisateur → exception dédiée.
    with pytest.raises(event_capture.DraftAuthorError):
        await event_capture.update_draft(db, ev, other, {"comments": "hack"})


async def test_update_draft_refused_when_not_draft(db):
    author, vessel, leg = await _base(db)
    ev = await event_capture.create_draft(
        db,
        leg=leg,
        vessel=vessel,
        event_type="noon",
        author=author,
        payload={"datetime_local": datetime(2026, 1, 2, 12), "timezone": "UTC"},
    )
    await event_capture.finalize(db, ev, author)
    with pytest.raises(event_capture.EventStateError):
        await event_capture.update_draft(db, ev, author, {"comments": "trop tard"})


# ════════════════════════════════════════════ datetime_utc (DST + tz mixtes)


async def test_finalize_computes_utc_across_dst_spring_forward(db):
    """Europe/Paris 2026-03-29 : +01:00 avant, +02:00 après le changement d'heure."""
    author, vessel, leg = await _base(db)
    before = await event_capture.create_draft(
        db,
        leg=leg,
        vessel=vessel,
        event_type="noon",
        author=author,
        payload={"datetime_local": datetime(2026, 3, 29, 1, 30), "timezone": "Europe/Paris"},
    )
    after = await event_capture.create_draft(
        db,
        leg=leg,
        vessel=vessel,
        event_type="noon",
        author=author,
        payload={"datetime_local": datetime(2026, 3, 29, 3, 30), "timezone": "Europe/Paris"},
    )
    await event_capture.finalize(db, before, author)
    await event_capture.finalize(db, after, author)
    assert before.datetime_utc == datetime(2026, 3, 29, 0, 30, tzinfo=UTC)  # CET +01:00
    assert after.datetime_utc == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)  # CEST +02:00


async def test_datetime_utc_mixed_timezones(db):
    author, vessel, leg = await _base(db)
    paris = await event_capture.create_draft(
        db,
        leg=leg,
        vessel=vessel,
        event_type="noon",
        author=author,
        payload={"datetime_local": datetime(2026, 1, 2, 13, 0), "timezone": "Europe/Paris"},
    )
    saigon = await event_capture.create_draft(
        db,
        leg=leg,
        vessel=vessel,
        event_type="noon",
        author=author,
        payload={"datetime_local": datetime(2026, 1, 2, 13, 0), "timezone": "Asia/Ho_Chi_Minh"},
    )
    # Paris +01:00 → 12:00 UTC ; Saigon +07:00 → 06:00 UTC.
    assert paris.datetime_utc == datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    assert saigon.datetime_utc == datetime(2026, 1, 2, 6, 0, tzinfo=UTC)
    assert saigon.datetime_utc < paris.datetime_utc


# ════════════════════════════════════════════ finalisation (gate qualité)


async def test_finalize_blocked_by_blocking_rule_r01(db):
    """Identité incomplète (pas de navire sur l'événement) → R01 bloquant → refus."""
    author, _vessel, leg = await _base(db)
    ev = await event_capture.create_draft(
        db,
        leg=leg,
        vessel=None,
        event_type="noon",
        author=author,
        payload={"datetime_local": datetime(2026, 1, 2, 12), "timezone": "UTC"},
    )
    with pytest.raises(event_capture.EventFinalizationError) as exc:
        await event_capture.finalize(db, ev, author)
    assert any("R01" in m for m in exc.value.messages)
    # L'événement reste brouillon (le gel n'a pas eu lieu).
    assert ev.status == "brouillon"
    assert ev.finalized_at is None


async def test_finalize_ok_persists_quality_results(db):
    author, vessel, leg = await _base(db)
    ev = await event_capture.create_draft(
        db,
        leg=leg,
        vessel=vessel,
        event_type="noon",
        author=author,
        payload={"datetime_local": datetime(2026, 1, 2, 12), "timezone": "UTC"},
    )
    out = await event_capture.finalize(db, ev, author)
    assert out.status == "finalise"
    assert out.finalized_at is not None
    qcr = (await db.execute(select(func.count()).select_from(QualityCheckResult))).scalar_one()
    assert qcr > 0  # QualityCheckResults persistés (audit)


async def test_finalize_blocked_when_manual_position_unjustified(db):
    author, vessel, leg = await _base(db)
    ev = await event_capture.create_draft(
        db,
        leg=leg,
        vessel=vessel,
        event_type="noon",
        author=author,
        payload={
            "datetime_local": datetime(2026, 1, 2, 12),
            "timezone": "UTC",
            "lat_decimal": Decimal("49.5"),
            "lon_decimal": Decimal("-3.0"),
            "position_source": "manuel_justifie",  # sans justification
        },
    )
    with pytest.raises(event_capture.EventFinalizationError) as exc:
        await event_capture.finalize(db, ev, author)
    assert any("R05" in m for m in exc.value.messages)


async def test_validate_only_after_finalize(db):
    author, vessel, leg = await _base(db)
    validator = User(username="siege", email="s@t.test", hashed_password="x", role="administrateur")
    db.add(validator)
    await db.flush()
    ev = await event_capture.create_draft(
        db,
        leg=leg,
        vessel=vessel,
        event_type="noon",
        author=author,
        payload={"datetime_local": datetime(2026, 1, 2, 12), "timezone": "UTC"},
    )
    # Impossible de valider un brouillon.
    with pytest.raises(event_capture.EventStateError):
        await event_capture.validate(db, ev, validator)
    await event_capture.finalize(db, ev, author)
    await event_capture.validate(db, ev, validator)
    assert ev.status == "valide"
    assert ev.validated_by == validator.id
    assert ev.validated_at is not None


# ════════════════════════════════════════════ préremplissage position


async def test_prefill_position_picks_nearest(db):
    author, vessel, leg = await _base(db)
    db.add_all(
        [
            VesselPosition(
                vessel_id=vessel.id,
                recorded_at=datetime(2026, 1, 2, 6, tzinfo=UTC),
                latitude=48.0,
                longitude=-4.0,
            ),
            VesselPosition(
                vessel_id=vessel.id,
                recorded_at=datetime(2026, 1, 2, 11, tzinfo=UTC),
                latitude=47.0,
                longitude=-5.0,
            ),
            VesselPosition(
                vessel_id=vessel.id,
                recorded_at=datetime(2026, 1, 2, 20, tzinfo=UTC),
                latitude=45.0,
                longitude=-6.0,
            ),
        ]
    )
    await db.flush()
    pre = await event_capture.prefill_position(db, vessel, datetime(2026, 1, 2, 12, tzinfo=UTC))
    assert pre is not None
    assert pre.source == "thalos_auto"
    # La position de 11:00 est la plus proche de 12:00 (tz-robuste : SQLite
    # restitue un datetime naïf, Postgres un aware).
    assert pre.lat_decimal == Decimal("47.0")
    assert pre.recorded_at.replace(tzinfo=None) == datetime(2026, 1, 2, 11)


async def test_prefill_position_none_when_no_data(db):
    author, vessel, leg = await _base(db)
    pre = await event_capture.prefill_position(db, vessel, datetime(2026, 1, 2, 12, tzinfo=UTC))
    assert pre is None


# ════════════════════════════════════ G1 — 6ᵉ type d'événement Year-End Cut-off


def test_cutoff_event_type_registered():
    """CDC v0.7 §9.2/§10.1 — le type ``cutoff`` doit exister au même titre que
    les 5 autres, résolu vers ``CutoffEvent`` (pas de table propre — cf.
    docstring du modèle)."""
    from app.models.nav_event import EVENT_CLASS_BY_TYPE, EVENT_TYPES, CutoffEvent

    assert "cutoff" in EVENT_TYPES
    assert EVENT_CLASS_BY_TYPE["cutoff"] is CutoffEvent


async def test_create_cutoff_draft(db):
    """Un brouillon Cut-off se crée comme les autres types (mêmes champs
    communs — position, local/tz) ; seul son ``datetime_utc`` diffère."""
    from app.models.nav_event import CutoffEvent

    author, vessel, leg = await _base(db)
    ev = await event_capture.create_draft(
        db,
        leg=leg,
        vessel=vessel,
        event_type="cutoff",
        author=author,
        payload={
            "datetime_local": datetime(2026, 12, 31, 23, 0),
            "timezone": "UTC",
            "lat_decimal": Decimal("10.0"),
            "lon_decimal": Decimal("-30.0"),
            "position_source": "thalos_auto",
        },
    )
    assert isinstance(ev, CutoffEvent)
    assert ev.lat_decimal == Decimal("10.0")  # champs communs appliqués normalement


@pytest.mark.parametrize(
    "local_dt,expected_pinned_utc",
    [
        # Saisie proche du 31/12 24:00 UTC (= 01/01 00:00 UTC) → fige sur
        # l'année suivante (candidat le plus proche).
        (datetime(2026, 12, 31, 22, 0), datetime(2027, 1, 1, tzinfo=UTC)),
        (datetime(2026, 12, 31, 23, 59), datetime(2027, 1, 1, tzinfo=UTC)),
        # Saisie juste après minuit UTC le 1er janvier → fige sur la même
        # bascule (année N, pas N+1).
        (datetime(2027, 1, 1, 0, 30), datetime(2027, 1, 1, tzinfo=UTC)),
        # Saisie en milieu d'année (Master pressé/erreur de date) → fige quand
        # même sur la bascule la plus proche, jamais une valeur arbitraire
        # (1er juillet 12:00 est encore légèrement plus proche du 01/01 de la
        # même année que du 01/01 suivant — 365 jours pairs, pas de bascule
        # exactement à mi-année).
        (datetime(2026, 7, 1, 12, 0), datetime(2026, 1, 1, tzinfo=UTC)),
        (datetime(2026, 6, 29, 12, 0), datetime(2026, 1, 1, tzinfo=UTC)),
    ],
)
async def test_cutoff_datetime_pinned_to_exact_boundary(db, local_dt, expected_pinned_utc):
    """G1 — décision produit : le Cut-off est une règle fixe, pas une
    observation de terrain. ``datetime_utc`` ne doit JAMAIS refléter la saisie
    brute du Master, seulement l'instant réglementaire le plus proche."""
    author, vessel, leg = await _base(db)
    ev = await event_capture.create_draft(
        db,
        leg=leg,
        vessel=vessel,
        event_type="cutoff",
        author=author,
        payload={"datetime_local": local_dt, "timezone": "UTC"},
    )
    assert ev.datetime_utc == expected_pinned_utc
    # Le local/tz saisi reste conservé tel quel (affichage informatif) —
    # seul l'UTC dérivé est figé, jamais la saisie elle-même.
    assert ev.datetime_local == local_dt
