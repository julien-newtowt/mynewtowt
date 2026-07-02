"""Intégration — P5 « flotte » (roster ERP + horizons de livraison) & P3
(baseline de marque factuelle « à la voile »).

Couvre :
- le service ``fleet.roster`` : groupement service/construction, ordre par code,
  parsing du jeton « AAAA-MM » / « AAAA », localisation du mois ;
- la page ``/flotte`` : roster rendu depuis l'ERP (aucune date en dur),
  livraisons localisées, et repli propre quand la flotte est vide ;
- la baseline de marque : « à la voile », plus aucun « décarboné » absolu.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.i18n import t
from app.services import fleet as fleet_svc
from app.templating import _BRAND_BY_LANG, brand_for_lang


class _Req:
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    query_params: dict[str, str] = {}
    client = SimpleNamespace(host="127.0.0.1")
    url = SimpleNamespace(path="/flotte")
    state = SimpleNamespace(lang="fr")


class _ReqLang(_Req):
    """Requête avec une langue arbitraire (cookie ``towt_lang``, cf. templating)."""

    def __init__(self, lang: str) -> None:
        self.cookies = {"towt_lang": lang}
        self.state = SimpleNamespace(lang=lang)


async def _seed_fleet(db) -> None:
    from app.models.vessel import Vessel

    db.add_all(
        [
            Vessel(code="1", name="Anemos", build_status="operational"),
            Vessel(code="2", name="Artemis", build_status="operational"),
            Vessel(
                code="3",
                name="Atlantis",
                build_status="under_construction",
                expected_delivery="2026-07",
            ),
            Vessel(
                code="4",
                name="Atlas",
                build_status="under_construction",
                expected_delivery="2026-09",
            ),
            Vessel(
                code="5",
                name="Archimedes",
                build_status="under_construction",
                expected_delivery="2027",
            ),
            Vessel(
                code="6",
                name="Astérias",
                build_status="under_construction",
                expected_delivery="2027",
            ),
        ]
    )
    await db.flush()


# ───────────────────── service roster ─────────────────────


def test_parse_delivery_token():
    assert fleet_svc._parse_delivery("2026-07") == (2026, 7)
    assert fleet_svc._parse_delivery("2027") == (2027, None)
    assert fleet_svc._parse_delivery(None) == (None, None)
    assert fleet_svc._parse_delivery("") == (None, None)
    assert fleet_svc._parse_delivery("2026-13") == (2026, None)  # mois hors bornes
    assert fleet_svc._parse_delivery("nope") == (None, None)


def test_delivery_label_is_localised():
    v = fleet_svc.FleetVessel("Atlantis", "under_construction", 2026, 7)
    assert v.delivery_label("fr") == "juillet 2026"
    assert v.delivery_label("en") == "July 2026"
    assert v.delivery_label("es") == "julio 2026"
    assert v.delivery_label("pt-br") == "julho 2026"
    assert v.delivery_label("vi") == "tháng 7 2026"
    assert v.delivery_label("xx") == "juillet 2026"  # repli FR
    # Année seule : neutre, aucune traduction de mois.
    year_only = fleet_svc.FleetVessel("Astérias", "under_construction", 2027, None)
    assert year_only.delivery_label("en") == "2027"
    # Navire en service : pas de livraison.
    op = fleet_svc.FleetVessel("Anemos", "operational", None, None)
    assert op.delivery_label("fr") is None


@pytest.mark.asyncio
async def test_roster_empty_db_has_no_content(db):
    fleet_svc.invalidate_cache()
    roster = await fleet_svc.roster(db)
    assert roster.has_content is False
    assert roster.total == 0


@pytest.mark.asyncio
async def test_roster_groups_and_orders(db):
    fleet_svc.invalidate_cache()
    await _seed_fleet(db)
    roster = await fleet_svc.roster(db)
    assert roster.operational_count == 2
    assert roster.under_construction_count == 4
    # Ordonné par code → chronologie de livraison.
    assert [v.name for v in roster.operational] == ["Anemos", "Artemis"]
    assert [v.name for v in roster.under_construction] == [
        "Atlantis",
        "Atlas",
        "Archimedes",
        "Astérias",
    ]
    assert roster.under_construction[0].delivery_label("fr") == "juillet 2026"
    assert roster.under_construction[3].delivery_label("fr") == "2027"
    fleet_svc.invalidate_cache()


# ───────────────────── page /flotte ─────────────────────


@pytest.mark.asyncio
async def test_flotte_renders_roster_from_erp(db):
    from app.routers.vitrine_router import fleet_capabilities

    fleet_svc.invalidate_cache()
    await _seed_fleet(db)
    resp = await fleet_capabilities(_Req(), db=db)
    assert resp.status_code == 200
    body = resp.body.decode()
    assert t("fleet_roster_title", "fr") in body
    # Chaque navire nommé, dates localisées FR issues de l'ERP.
    for name in ("Anemos", "Atlantis", "Atlas", "Archimedes", "Astérias"):
        assert name in body
    assert "juillet 2026" in body
    assert "septembre 2026" in body
    assert "2027" in body
    fleet_svc.invalidate_cache()


@pytest.mark.asyncio
async def test_flotte_localises_delivery_month(db):
    from app.routers.vitrine_router import fleet_capabilities

    fleet_svc.invalidate_cache()
    await _seed_fleet(db)
    resp = await fleet_capabilities(_ReqLang("en"), db=db)
    body = resp.body.decode()
    # Le roster visible localise le mois de livraison (delivery_label(lang)).
    assert "July 2026" in body
    assert "September 2026" in body
    assert t("fleet_roster_building_badge", "en") in body  # « Under construction »
    fleet_svc.invalidate_cache()


@pytest.mark.asyncio
async def test_flotte_empty_db_hides_roster_but_serves(db):
    from app.routers.vitrine_router import fleet_capabilities

    fleet_svc.invalidate_cache()
    resp = await fleet_capabilities(_Req(), db=db)
    assert resp.status_code == 200
    body = resp.body.decode()
    # Roster masqué (aucun navire), mais la page reste servie (specs, CTA).
    assert t("fleet_roster_title", "fr") not in body  # « Navire par navire » absent
    assert "2 226 m²" in body  # tableau des caractéristiques toujours rendu
    assert t("fleet_cta_routes", "fr") in body  # CTA de bas de page présent
    fleet_svc.invalidate_cache()


# ───────────────────── P3 baseline de marque ─────────────────────


def test_brand_baseline_is_factual_sail():
    assert (
        brand_for_lang("fr")["mention"]
        == "Pionnier du transport de marchandises à la voile depuis 2011"
    )
    assert brand_for_lang("en")["mention"] == "Pioneer of sail-powered cargo transport since 2011"


def test_no_absolute_decarbonised_claim_in_any_baseline():
    """Aucune baseline de marque ne revendique « décarboné » en absolu (ECGT)."""
    for lang, brand in _BRAND_BY_LANG.items():
        mention = brand["mention"].lower()
        assert "décarbon" not in mention, lang
        assert "decarbon" not in mention, lang
        assert "descarboniz" not in mention, lang
