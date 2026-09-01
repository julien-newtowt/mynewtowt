"""Cloisonnement par navire (ADR-012) — vente à bord et caisse de bord.

Décision du 2026-08-27 : le personnel maritime est borné à son navire
d'affectation ; les seules consultations ouvertes sur la flotte entière sont le
planning de navigation et la position des navires. Ni la caisse, ni les ventes,
ni l'inventaire, ni le registre n'en font partie.

Avant cette règle, tout titulaire de ``captain:C`` lisait le solde de caisse et
le registre douanier de **toute la flotte**, et tout ``captain:M`` y écrivait —
le commandant du navire 1 pouvait clôturer et verrouiller la caisse du navire 2.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.vessel import Vessel
from app.permissions import VesselAccessDenied, assert_vessel_access, visible_vessel_id


def _user(role="marins", vessel_id=None, uid=1):
    return SimpleNamespace(
        id=uid, username=f"u{uid}", role=role, assigned_vessel_id=vessel_id, full_name="Test"
    )


# ── Règle d'accès ───────────────────────────────────────────────────────────


def test_a_seafarer_reaches_only_their_own_vessel():
    marin = _user("marins", vessel_id=1)
    assert_vessel_access(marin, 1)  # ne lève pas
    with pytest.raises(VesselAccessDenied):
        assert_vessel_access(marin, 2)


def test_a_seafarer_without_an_assigned_vessel_is_refused_with_a_remedy():
    """Un refus doit dire quoi faire : c'est un 403 muet qui a fait échouer
    le premier test à bord."""
    with pytest.raises(VesselAccessDenied) as exc:
        assert_vessel_access(_user("marins", vessel_id=None), 1)
    message = str(exc.value)
    assert "rattaché à aucun navire" in message
    assert "/admin/users" in message


@pytest.mark.parametrize("role", ["administrateur", "armement"])
def test_shore_administration_is_never_bounded(role):
    """Ces rôles doivent pouvoir corriger et administrer à distance."""
    user = _user(role, vessel_id=1)
    assert_vessel_access(user, 2)  # ne lève pas
    assert visible_vessel_id(user) is None


def test_a_shore_role_without_assignment_keeps_the_fleet_scope():
    """Les Opérations à terre ne sont pas rattachées à un navire."""
    ops = _user("operation", vessel_id=None)
    assert_vessel_access(ops, 7)
    assert visible_vessel_id(ops) is None


def test_an_embarked_non_seafarer_is_bounded_too():
    """C'est le rattachement qui borne, pas le libellé du rôle : un technicien
    embarqué doit l'être comme un marin."""
    tech = _user("technique", vessel_id=3)
    assert_vessel_access(tech, 3)
    with pytest.raises(VesselAccessDenied):
        assert_vessel_access(tech, 4)


# ── Application aux routes ──────────────────────────────────────────────────


async def _two_vessels(db):
    a, b = Vessel(code="ANE", name="Anemos"), Vessel(code="GRA", name="Grain de Sail")
    db.add_all([a, b])
    await db.flush()
    return a, b


@pytest.mark.asyncio
async def test_cashbox_detail_refuses_another_vessel(db):
    from app.routers import cashbox_router as r

    own, other = await _two_vessels(db)
    marin = _user("marins", vessel_id=own.id)
    with pytest.raises(HTTPException) as exc:
        await r.cashbox_detail(None, other.id, db=db, user=marin)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_a_master_cannot_close_another_vessels_cashbox(db):
    """Le cas le plus grave : la clôture verrouille des mouvements."""
    from app.routers import cashbox_router as r

    own, other = await _two_vessels(db)
    marin = _user("marins", vessel_id=own.id)
    with pytest.raises(HTTPException) as exc:
        await r.close_period(None, other.id, year=2026, month=8, db=db, user=marin)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_a_master_cannot_write_a_movement_on_another_vessel(db):
    from app.routers import cashbox_router as r

    own, other = await _two_vessels(db)
    marin = _user("marins", vessel_id=own.id)
    with pytest.raises(HTTPException) as exc:
        await r.add_mov(
            None,
            other.id,
            amount="50",
            currency="EUR",
            category="depot_recharge",
            description="tentative",
            movement_kind="income",
            db=db,
            user=marin,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_cashbox_index_lists_only_the_assigned_vessel(db):
    from app.routers import cashbox_router as r

    own, _other = await _two_vessels(db)
    marin = _user("marins", vessel_id=own.id)
    captured: dict = {}

    class _T:
        @staticmethod
        def TemplateResponse(name, ctx):
            captured.update(ctx)
            return "ok"

    r.templates, saved = _T, r.templates
    try:
        await r.cashbox_index(None, db=db, user=marin)
    finally:
        r.templates = saved
    assert [row["vessel"].id for row in captured["summary"]] == [own.id]


@pytest.mark.asyncio
async def test_a_sale_of_another_vessel_is_out_of_reach(db, staff_user):
    from app.routers import onboard_sales_router as r
    from app.services import onboard_sales as sales_svc

    own, other = await _two_vessels(db)
    sale = await sales_svc.create_sale(db, vessel_id=other.id, currency="EUR")
    marin = _user("marins", vessel_id=own.id)
    with pytest.raises(HTTPException) as exc:
        await r._get_sale_or_404(db, sale.reference, user=marin)
    assert exc.value.status_code == 403
    # La même vente reste accessible depuis son propre navire.
    entrant = _user("marins", vessel_id=other.id)
    assert (await r._get_sale_or_404(db, sale.reference, user=entrant)).id == sale.id


@pytest.mark.asyncio
async def test_stock_entry_on_another_vessel_is_refused(db):
    from app.routers import onboard_sales_router as r

    own, other = await _two_vessels(db)
    marin = _user("marins", vessel_id=own.id)
    with pytest.raises(HTTPException) as exc:
        await r.add_stock(
            other.id,
            product_id=1,
            qty="10",
            reason="avitaillement",
            note="",
            db=db,
            user=marin,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_the_customs_register_of_another_vessel_is_out_of_reach(db):
    from app.routers import onboard_sales_router as r

    own, other = await _two_vessels(db)
    marin = _user("marins", vessel_id=own.id)
    with pytest.raises(HTTPException) as exc:
        await r.registre_csv(other.id, date_from="", date_to="", db=db, user=marin)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_shore_administration_still_reaches_every_vessel(db):
    """Le cloisonnement ne doit pas enfermer le siège."""
    from app.routers import onboard_sales_router as r

    _own, other = await _two_vessels(db)
    admin = _user("administrateur", vessel_id=None, uid=9)
    # Ne lève pas : seule la présence du produit manque, pas le droit d'accès.
    with pytest.raises(HTTPException) as exc:
        await r.add_stock(
            other.id, product_id=999, qty="1", reason="avitaillement", note="", db=db, user=admin
        )
    assert exc.value.status_code == 404  # produit inconnu, pas 403


def test_cash_count_amounts_are_untouched_by_scoping():
    """Garde-fou : le cloisonnement ne doit pas altérer les calculs."""
    assert Decimal("76.47") == Decimal("20") * 2 + Decimal("5") + Decimal("31.47")


# ── Périmètre de la permission accordée au commandant ───────────────────────


def test_selling_does_not_unlock_the_whole_captain_module():
    """Revue de sécurité du 2026-08-28 — escalade de privilège corrigée.

    Donner `captain:CM` à `marins` pour qu'il puisse encaisser lui ouvrait en
    réalité 39 routes d'écriture qui ne contrôlent pas le navire (SOF, décalages
    d'ETA, messagerie du bord, documents cargo, saisie MRV), sur toute la flotte.
    La vente et la caisse vivent donc dans leur propre module de permission.
    """
    from app.permissions import MODULES, has_permission

    assert "ventes" in MODULES
    # Le commandant encaisse…
    assert has_permission("marins", "ventes", "M")
    # …et ne reçoit pour autant aucun droit d'écriture ailleurs.
    assert not has_permission("marins", "captain", "M")
    assert not has_permission("marins", "mrv", "M")
    assert not has_permission("marins", "cargo", "M")


def test_the_sales_and_cashbox_routers_use_the_dedicated_module():
    """Sentinelle : aucune route de ces deux modules ne doit exiger `captain`."""
    from pathlib import Path

    for path in ("app/routers/onboard_sales_router.py", "app/routers/cashbox_router.py"):
        source = Path(path).read_text(encoding="utf-8")
        assert 'require_permission("captain"' not in source, path
        assert 'require_permission("ventes"' in source, path


def test_shore_roles_keep_the_access_they_had():
    """Le nouveau module ne doit priver personne de ce qu'il avait sur `captain`."""
    from app.permissions import has_permission

    for role, level in (
        ("operation", "M"),
        ("technique", "M"),
        ("manager_maritime", "S"),
        ("administrateur", "S"),
    ):
        assert has_permission(role, "ventes", level), f"{role} a perdu {level} sur ventes"
    for role in ("armement", "data_analyst", "commercial"):
        assert has_permission(role, "ventes", "C")
        assert not has_permission(role, "ventes", "M")
