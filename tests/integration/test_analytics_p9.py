"""Intégration — P9 : instrumentation étendue + tableau de bord B2B2C.

Couvre : le vocabulaire d'événements élargi, les helpers UTM, l'instrumentation
des pages de conviction (/solutions, /impact, /preuves), du scan de vérification
(/verify) et du formulaire de contact, et le rendu du dashboard commercial
(full funnel + section B2B2C + cibles).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models.analytics_event import ANALYTICS_EVENTS, AnalyticsEvent
from app.services import analytics


def _req(query: dict | None = None, lang: str = "fr", path: str = "/"):
    return SimpleNamespace(
        headers={},
        cookies={},
        query_params=query or {},
        client=SimpleNamespace(host="127.0.0.1"),
        url=SimpleNamespace(path=path),
        state=SimpleNamespace(lang=lang),
    )


async def _count(db, event: str) -> int:
    return (
        await db.execute(select(func.count(AnalyticsEvent.id)).where(AnalyticsEvent.event == event))
    ).scalar_one()


async def _last_detail(db, event: str) -> str | None:
    return (
        await db.execute(
            select(AnalyticsEvent.detail)
            .where(AnalyticsEvent.event == event)
            .order_by(AnalyticsEvent.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


# ───────────────────── vocabulaire & UTM ─────────────────────


def test_new_events_registered():
    for ev in (
        "solutions_view",
        "impact_view",
        "preuves_view",
        "contact_submitted",
        "verify_lookup",
        "kit_generated",
        "kit_download",
        "rebooking",
    ):
        assert ev in ANALYTICS_EVENTS


def test_utm_helpers():
    r = _req({"utm_source": "linkedin", "utm_medium": "post", "utm_campaign": "cacao26"})
    assert analytics.utm_from_request(r) == "source=linkedin medium=post campaign=cacao26"
    assert analytics.detail_with_utm(r, "cacao").startswith("cacao | source=linkedin")
    assert analytics.utm_from_request(_req({})) is None
    assert analytics.detail_with_utm(_req({}), "cafe") == "cafe"
    assert analytics.detail_with_utm(_req({}), None) is None


@pytest.mark.asyncio
async def test_record_rejects_unknown_event(db):
    await analytics.record(db, "not_a_real_event")
    assert await _count(db, "not_a_real_event") == 0


# ───────────────────── instrumentation des pages ─────────────────────


@pytest.mark.asyncio
async def test_solutions_pages_record_view_with_vertical(db):
    from app.routers.public_router import solutions_cacao, solutions_cafe

    await solutions_cafe(_req({"utm_source": "newsletter"}, path="/solutions/cafe"), db=db)
    await solutions_cacao(_req(path="/solutions/cacao"), db=db)
    assert await _count(db, "solutions_view") == 2
    # Le détail porte la verticale (+ UTM le cas échéant).
    details = (
        (
            await db.execute(
                select(AnalyticsEvent.detail).where(AnalyticsEvent.event == "solutions_view")
            )
        )
        .scalars()
        .all()
    )
    joined = " ".join(d or "" for d in details)
    assert "cafe" in joined and "cacao" in joined
    assert "source=newsletter" in joined


@pytest.mark.asyncio
async def test_impact_and_preuves_record_views(db):
    from app.routers.vitrine_router import impact, preuves

    await impact(_req(path="/impact"), db=db)
    await preuves(_req(path="/preuves"), db=db)
    assert await _count(db, "impact_view") == 1
    assert await _count(db, "preuves_view") == 1


@pytest.mark.asyncio
async def test_verify_records_lookup_notfound(db):
    from app.routers.vitrine_router import verify_certificate

    await verify_certificate(
        _req({"ref": "ANEMOS-UNKNOWN"}, path="/verify"), ref="ANEMOS-UNKNOWN", db=db
    )
    assert await _count(db, "verify_lookup") == 1
    assert (await _last_detail(db, "verify_lookup")).startswith("notfound")


@pytest.mark.asyncio
async def test_verify_without_ref_records_nothing(db):
    from app.routers.vitrine_router import verify_certificate

    await verify_certificate(_req(path="/verify"), ref=None, db=db)
    assert await _count(db, "verify_lookup") == 0


@pytest.mark.asyncio
async def test_contact_submit_records_event(db):
    from app.routers.vitrine_router import contact_submit

    resp = await contact_submit(
        _req(path="/contact"),
        db=db,
        name="Marie Test",
        email="marie@example.test",
        company="Choco SAS",
        phone="",
        pol="",
        pod="",
        cargo_nature="Cacao / fèves",
        volume_weight="",
        desired_dates="",
        message="Bonjour, un devis cacao svp.",
        consent="on",
        website="",  # honeypot vide
    )
    assert resp.status_code == 303
    assert await _count(db, "contact_submitted") == 1
    assert "Cacao" in (await _last_detail(db, "contact_submitted"))


# ───────────────────── tableau de bord commercial ─────────────────────


@pytest.mark.asyncio
async def test_commercial_dashboard_renders_b2b2c_and_targets(db, staff_user):
    from app.routers.modules_router import analytics_commercial

    # Sème quelques événements pour peupler funnel + B2B2C.
    await analytics.record(db, "landing_view", channel="public")
    await analytics.record(db, "solutions_view", channel="public", detail="cacao")
    await analytics.record(db, "voyage_page_view", channel="public")
    await analytics.record(db, "verify_lookup", channel="public", detail="found")
    await analytics.record(db, "kit_download", channel="client")

    resp = await analytics_commercial(
        _req(path="/dashboard/analytics/commercial"), db=db, user=staff_user
    )
    assert resp.status_code == 200
    body = resp.body.decode()
    # Section B2B2C présente avec ses libellés.
    assert "Boucle B2B2C" in body
    assert "Scans page voyage" in body
    assert "Réachats" in body
    # Cibles affichées (conversion ≥ 5 %, self-service ≥ 30 %).
    assert "cible ≥ 5.0 %" in body
    assert "cible ≥ 30.0 %" in body
