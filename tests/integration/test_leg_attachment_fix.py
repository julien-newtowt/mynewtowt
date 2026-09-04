"""Régularisation du rattachement au voyage des ventes et des mouvements de caisse.

Le défaut constaté en production : des ventes et des mouvements de 2026 imputés
au voyage `1ABRFR7`, dont le départ est en **janvier 2027**. Cause : le voyage
était choisi par `ORDER BY id DESC` — le dernier leg *créé* — si bien qu'un
voyage planifié à l'avance raflait toutes les opérations en cours.

Ces tests verrouillent la reprise : ce qui est démontrablement faux est corrigé,
ce qui est juste n'est pas touché, et ce qui est indéterminable devient NULL
plutôt que de rester faux.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.activity_log import ActivityLog
from app.models.leg import Leg
from app.models.onboard_sales import OnboardSale
from app.models.port import Port
from app.models.vessel import Vessel
from app.services import cashbox as cashbox_svc
from app.services import leg_attachment as svc
from app.services.planning import ensure_utc


async def _fleet(db):
    vessel = Vessel(code="ANE", name="Anemos")
    pol = Port(locode="BRSSO", name="São Sebastião", country="BR")
    pod = Port(locode="FRFEC", name="Fécamp", country="FR")
    db.add_all([vessel, pol, pod])
    await db.flush()
    return vessel, pol, pod


async def _leg(db, vessel, pol, pod, *, code, etd, eta, atd=None, ata=None):
    leg = Leg(
        leg_code=code,
        vessel_id=vessel.id,
        departure_port_id=pol.id,
        arrival_port_id=pod.id,
        etd_ref=etd,
        eta_ref=eta,
        etd=etd,
        eta=eta,
        atd=atd,
        ata=ata,
    )
    db.add(leg)
    await db.flush()
    return leg


async def _sale(db, vessel, *, reference, leg_id, paid_at, movement=None):
    sale = OnboardSale(
        reference=reference,
        vessel_id=vessel.id,
        leg_id=leg_id,
        status="paid",
        currency="EUR",
        total=Decimal("12.00"),
        paid_at=paid_at,
        created_at=paid_at,
        cashbox_movement_id=movement.id if movement is not None else None,
    )
    db.add(sale)
    await db.flush()
    return sale


# Le voyage réel : parti, arrivé, en 2026.
REEL_ETD = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
REEL_ETA = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
# Le voyage planifié qui raflait tout : départ en 2027.
FUTUR_ETD = datetime(2027, 1, 10, 22, 43, tzinfo=UTC)
FUTUR_ETA = datetime(2027, 2, 10, 2, 43, tzinfo=UTC)
# L'opération : à bord, pendant le voyage réel.
QUAND = datetime(2026, 6, 15, 14, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_une_vente_imputee_a_un_voyage_futur_revient_au_voyage_en_cours(db):
    """Le cas signalé : une vente de juin 2026 rattachée à un départ de 2027."""
    vessel, pol, pod = await _fleet(db)
    reel = await _leg(db, vessel, pol, pod, code="1ABRFR6", etd=REEL_ETD, eta=REEL_ETA)
    futur = await _leg(db, vessel, pol, pod, code="1ABRFR7", etd=FUTUR_ETD, eta=FUTUR_ETA)
    sale = await _sale(db, vessel, reference="VB-0001", leg_id=futur.id, paid_at=QUAND)

    corrections = await svc.plan(db)

    assert len(corrections) == 1
    c = corrections[0]
    assert (c.kind, c.from_leg_code, c.to_leg_code) == ("vente", "1ABRFR7", "1ABRFR6")
    assert c.reason == svc.REASON_IMPOSSIBLE

    assert await svc.apply(db, corrections) == 1
    await db.refresh(sale)
    assert sale.leg_id == reel.id


@pytest.mark.asyncio
async def test_le_mouvement_de_caisse_suit_la_vente_qu_il_regle(db):
    """Rattachement exact plutôt que recalculé : les deux registres concordent.

    Le mouvement est daté à la **journée** (minuit UTC) — un recalcul par date
    pourrait le faire basculer d'un voyage à l'autre aux frontières. Son voyage
    n'est pas une déduction : c'est celui de la vente dont il est le règlement.
    """
    vessel, pol, pod = await _fleet(db)
    reel = await _leg(db, vessel, pol, pod, code="1ABRFR6", etd=REEL_ETD, eta=REEL_ETA)
    futur = await _leg(db, vessel, pol, pod, code="1ABRFR7", etd=FUTUR_ETD, eta=FUTUR_ETA)

    cb = await cashbox_svc.get_or_create(db, vessel.id)
    mov = await cashbox_svc.add_movement(
        db,
        cb,
        amount=Decimal("12.00"),
        currency="EUR",
        category="vente_a_bord",
        description="VB-0001",
        occurred_at=QUAND,
        leg_id=futur.id,
    )
    sale = await _sale(
        db, vessel, reference="VB-0001", leg_id=futur.id, paid_at=QUAND, movement=mov
    )

    corrections = await svc.plan(db)
    par_type = {c.kind: c for c in corrections}

    assert set(par_type) == {"vente", "mouvement"}
    assert par_type["mouvement"].basis == svc.BASIS_SALE_LINK
    assert par_type["mouvement"].to_leg_id == par_type["vente"].to_leg_id == reel.id

    await svc.apply(db, corrections)
    await db.refresh(sale)
    await db.refresh(mov)
    assert sale.leg_id == mov.leg_id == reel.id


@pytest.mark.asyncio
async def test_le_montant_et_la_date_du_mouvement_ne_bougent_pas(db):
    """Seule l'étiquette de voyage change — le registre d'argent reste intact."""
    vessel, pol, pod = await _fleet(db)
    await _leg(db, vessel, pol, pod, code="1ABRFR6", etd=REEL_ETD, eta=REEL_ETA)
    futur = await _leg(db, vessel, pol, pod, code="1ABRFR7", etd=FUTUR_ETD, eta=FUTUR_ETA)

    cb = await cashbox_svc.get_or_create(db, vessel.id)
    mov = await cashbox_svc.add_movement(
        db,
        cb,
        amount=Decimal("-40.00"),
        currency="EUR",
        category="avitaillement",
        description="glace",
        occurred_at=QUAND,
        leg_id=futur.id,
    )

    def _empreinte(m):
        # ``ensure_utc`` des deux côtés : SQLite relit les datetimes en naïf
        # après ``refresh``, ce qui ferait échouer la comparaison sur un
        # instant pourtant identique.
        return (
            m.amount,
            m.currency,
            m.category,
            m.description,
            ensure_utc(m.occurred_at),
            m.medium,
        )

    avant = _empreinte(mov)

    await svc.apply(db, await svc.plan(db))
    await db.refresh(mov)

    assert _empreinte(mov) == avant


@pytest.mark.asyncio
async def test_un_rattachement_correct_n_est_pas_touche(db):
    """Le défaut par défaut ne corrige que le démontrablement faux."""
    vessel, pol, pod = await _fleet(db)
    reel = await _leg(db, vessel, pol, pod, code="1ABRFR6", etd=REEL_ETD, eta=REEL_ETA)
    await _sale(db, vessel, reference="VB-0002", leg_id=reel.id, paid_at=QUAND)

    assert await svc.plan(db) == []


@pytest.mark.asyncio
async def test_sans_voyage_anterieur_on_passe_a_null_plutot_que_de_mentir(db):
    """Aucun voyage ne précède l'opération : l'étiquette tombe, elle ne ment pas."""
    vessel, pol, pod = await _fleet(db)
    futur = await _leg(db, vessel, pol, pod, code="1ABRFR7", etd=FUTUR_ETD, eta=FUTUR_ETA)
    sale = await _sale(db, vessel, reference="VB-0003", leg_id=futur.id, paid_at=QUAND)

    corrections = await svc.plan(db)

    assert len(corrections) == 1
    assert corrections[0].drops_attachment
    await svc.apply(db, corrections)
    await db.refresh(sale)
    assert sale.leg_id is None


@pytest.mark.asyncio
async def test_le_depart_reel_prime_sur_le_previsionnel(db):
    """``atd`` connu : c'est lui qui dit si l'opération précède le départ.

    Un voyage dont l'ETD est passé mais qui n'est **pas encore parti** ne peut
    pas porter d'opération : sans cette règle, un retard de départ ferait passer
    un rattachement faux pour légitime.
    """
    vessel, pol, pod = await _fleet(db)
    retarde = await _leg(
        db,
        vessel,
        pol,
        pod,
        code="1ABRFR8",
        etd=QUAND - timedelta(days=10),
        eta=QUAND + timedelta(days=20),
        atd=QUAND + timedelta(days=5),  # parti pour de vrai après l'opération
    )

    assert svc.is_impossible(retarde, QUAND) is True
    # Sans ``atd``, la fenêtre prévisionnelle couvrait l'opération : légitime.
    retarde.atd = None
    assert svc.is_impossible(retarde, QUAND) is False


@pytest.mark.asyncio
async def test_chaque_correction_laisse_une_trace_au_journal(db):
    """Écrire dans un registre append-only se paie d'une trace auditable."""
    vessel, pol, pod = await _fleet(db)
    await _leg(db, vessel, pol, pod, code="1ABRFR6", etd=REEL_ETD, eta=REEL_ETA)
    futur = await _leg(db, vessel, pol, pod, code="1ABRFR7", etd=FUTUR_ETD, eta=FUTUR_ETA)
    await _sale(db, vessel, reference="VB-0004", leg_id=futur.id, paid_at=QUAND)

    await svc.apply(db, await svc.plan(db), actor_name="script:test")

    logs = (
        (await db.execute(select(ActivityLog).where(ActivityLog.action == "leg_attachment_fix")))
        .scalars()
        .all()
    )
    assert len(logs) == 1
    assert logs[0].entity_label == "VB-0004"
    assert logs[0].user_name == "script:test"
    # Le journal est relu par un humain : il doit se lire, pas se décoder.
    assert "1ABRFR7 → 1ABRFR6" in logs[0].detail
    assert svc.REASON_IMPOSSIBLE in logs[0].detail


@pytest.mark.asyncio
async def test_le_filtre_navire_borne_la_reprise(db):
    """Un navire à la fois : la reprise doit pouvoir être menée par étapes."""
    vessel, pol, pod = await _fleet(db)
    autre = Vessel(code="ATL", name="Atlantis")
    db.add(autre)
    await db.flush()

    await _leg(db, vessel, pol, pod, code="1ABRFR6", etd=REEL_ETD, eta=REEL_ETA)
    futur = await _leg(db, vessel, pol, pod, code="1ABRFR7", etd=FUTUR_ETD, eta=FUTUR_ETA)
    futur_autre = await _leg(db, autre, pol, pod, code="2ABRFR7", etd=FUTUR_ETD, eta=FUTUR_ETA)
    await _sale(db, vessel, reference="VB-0005", leg_id=futur.id, paid_at=QUAND)
    await _sale(db, autre, reference="VB-0006", leg_id=futur_autre.id, paid_at=QUAND)

    ciblees = await svc.plan(db, vessel_id=vessel.id)

    assert [c.label for c in ciblees] == ["VB-0005"]
    assert len(await svc.plan(db)) == 2
