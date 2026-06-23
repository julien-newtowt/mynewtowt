"""Consolidation financière par leg — FLX-05 (action corrective direction).

Agrège en un clic, vers la ligne ``LegFinance`` du leg, les données
d'exploitation déjà saisies dans les autres modules :

- ``revenue_eur``      ← Σ bookings plateforme du leg dont le statut est
  confirmé ou au-delà (``confirmed``, ``loaded``, ``at_sea``,
  ``discharged``, ``delivered``) — ``confirmed_price_eur`` avec repli
  ``estimated_price_eur`` — + Σ ``Order.total_eur`` des commandes
  commerciales rattachées au leg (statuts ``confirmed``, ``loaded``,
  ``delivered``).
- ``docker_costs_eur`` ← Σ ``DockerShift.cost_eur`` des shifts du leg.
- ``opex_share_eur``   ← OPEX journalier (paramètre ``opex_daily_sea``,
  repli 12 000 EUR) × jours de mer : ATD→ATA si renseignés, sinon
  ETD→ETA, sinon 0 (Decimal, 2 décimales).
- ``port_fees_eur``    ← coût prescrit escale, recomposé à chaque rollup
  (déterministe / idempotent) = frais ``PortConfig`` (agence + pilote des
  ports de départ et d'arrivée + quai journalier × durée d'escale à
  l'arrivée) + Σ coût des opérations d'escale (FLX-05 :
  ``EscaleOperation.cost_actual`` si renseigné sinon ``cost_forecast``).
  La saisie manuelle n'est pas préservée ici — utiliser ``other_costs_eur``
  pour les ajustements.
- ``other_costs_eur``  ← jamais écrasé (champ strictement manuel).
- ``margin_eur``       ← revenue − (port_fees + dockers + opex + autres).

Simplification V1 (assumée) : une commande commerciale ventilée sur
plusieurs legs via ``OrderAssignment`` est comptée à PLEINE valeur sur
son leg direct (``Order.leg_id``) — aucun prorata multi-leg n'est
appliqué.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.commercial import Order
from app.models.escale import DockerShift, EscaleOperation
from app.models.finance import LegFinance, OpexParameter, PortConfig
from app.models.leg import Leg

# Statuts générateurs de revenu (cf. workflows models/booking.py et
# models/commercial.py — ORDER_STATUSES).
BOOKING_REVENUE_STATUSES = ("confirmed", "loaded", "at_sea", "discharged", "delivered")
ORDER_REVENUE_STATUSES = ("confirmed", "loaded", "delivered")

# Aligné sur services/quoting.py (formule OPEX historique NEWTOWT).
OPEX_PARAMETER_NAME = "opex_daily_sea"
FALLBACK_OPEX_DAILY_EUR = Decimal("12000")

_TWO_PLACES = Decimal("0.01")


def _dec(value: object) -> Decimal:
    """Convertit float/int/Decimal/None en Decimal sûr (None → 0)."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _q2(value: Decimal) -> Decimal:
    return value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _sea_days(leg: Leg) -> Decimal:
    """Jours de mer du leg : ATD→ATA si les deux sont saisis, sinon
    ETD→ETA, sinon 0. Decimal arrondi à 2 décimales, jamais négatif."""
    if leg.atd and leg.ata:
        delta = leg.ata - leg.atd
    elif leg.etd and leg.eta:
        delta = leg.eta - leg.etd
    else:
        return Decimal("0")
    days = Decimal(str(delta.total_seconds())) / Decimal("86400")
    if days <= 0:
        return Decimal("0")
    return _q2(days)


async def _port_fees_prefill(db: AsyncSession, leg: Leg) -> Decimal:
    """Pré-remplissage défensif des frais portuaires depuis ``PortConfig``.

    Somme, pour les ports de départ et d'arrivée du leg : frais d'agence
    + frais de pilote (quand renseignés). Pour le port d'arrivée
    uniquement, ajoute le forfait de quai journalier × durée d'escale
    planifiée (``leg.port_stay_planned_hours``) si les deux existent —
    pour le départ la durée à quai n'est pas connue ici, on ne compte
    donc pas de frais de quai (estimation volontairement prudente,
    affinable manuellement).
    """
    total = Decimal("0")
    port_ids = {leg.departure_port_id, leg.arrival_port_id} - {None}
    if not port_ids:
        return total

    configs = list(
        (await db.execute(select(PortConfig).where(PortConfig.port_id.in_(port_ids))))
        .scalars()
        .all()
    )
    for cfg in configs:
        total += _dec(cfg.agency_fee_eur) + _dec(cfg.pilot_fee_eur)
        if (
            cfg.port_id == leg.arrival_port_id
            and cfg.berth_fee_per_day_eur is not None
            and leg.port_stay_planned_hours
        ):
            stay_days = Decimal(str(leg.port_stay_planned_hours)) / Decimal("24")
            total += _dec(cfg.berth_fee_per_day_eur) * stay_days
    return _q2(total)


async def _escale_operations_cost(db: AsyncSession, leg: Leg) -> Decimal:
    """Σ coût des opérations d'escale du leg.

    Coût prescrit escale = Σ opérations + Σ shifts dockers + quai×jours
    (cf. audit). Les shifts dockers et le quai×jours sont déjà capturés
    (``docker_costs_eur`` et ``_port_fees_prefill``) ; cette fonction
    ajoute la composante « opérations ». Pour chaque opération on retient
    ``cost_actual`` si renseigné, sinon ``cost_forecast`` (repli 0).
    """
    operations = list(
        (await db.execute(select(EscaleOperation).where(EscaleOperation.leg_id == leg.id)))
        .scalars()
        .all()
    )
    total = sum(
        (
            _dec(op.cost_actual if op.cost_actual is not None else op.cost_forecast)
            for op in operations
        ),
        Decimal("0"),
    )
    return _q2(total)


async def rollup_for_leg(db: AsyncSession, leg: Leg) -> LegFinance:
    """Get-or-create puis recalcule la ligne ``LegFinance`` du leg.

    Signature stable — appelée aussi depuis le endpoint de clôture de
    voyage. Voir le docstring module pour la formule détaillée ; rappel
    V1 : les commandes ventilées multi-leg (``OrderAssignment``) sont
    comptées à pleine valeur sur leur leg direct (``Order.leg_id``).

    Flush (jamais commit — géré par la dependency ``get_db``) puis
    retourne la ligne.
    """
    finance: LegFinance | None = (
        await db.execute(select(LegFinance).where(LegFinance.leg_id == leg.id))
    ).scalar_one_or_none()
    if finance is None:
        finance = LegFinance(leg_id=leg.id)
        db.add(finance)

    # 1. Revenu — bookings plateforme + commandes commerciales.
    bookings = list(
        (
            await db.execute(
                select(Booking).where(
                    Booking.leg_id == leg.id,
                    Booking.status.in_(BOOKING_REVENUE_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    booking_revenue = sum(
        (
            _dec(
                b.confirmed_price_eur
                if b.confirmed_price_eur is not None
                else b.estimated_price_eur
            )
            for b in bookings
        ),
        Decimal("0"),
    )
    orders = list(
        (
            await db.execute(
                select(Order).where(
                    Order.leg_id == leg.id,
                    Order.status.in_(ORDER_REVENUE_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    order_revenue = sum((_dec(o.total_eur) for o in orders), Decimal("0"))
    revenue = _q2(booking_revenue + order_revenue)

    # 2. Coûts dockers — Σ DockerShift.cost_eur du leg.
    shifts = list(
        (await db.execute(select(DockerShift).where(DockerShift.leg_id == leg.id))).scalars().all()
    )
    docker_costs = _q2(sum((_dec(s.cost_eur) for s in shifts), Decimal("0")))

    # 3. Quote-part OPEX — opex_daily_sea × jours de mer.
    opex_daily_raw = await db.scalar(
        select(OpexParameter.parameter_value).where(
            OpexParameter.parameter_name == OPEX_PARAMETER_NAME
        )
    )
    opex_daily = _dec(opex_daily_raw) if opex_daily_raw is not None else FALLBACK_OPEX_DAILY_EUR
    opex_share = _q2(opex_daily * _sea_days(leg))

    # 4. Frais portuaires — pré-remplissage PortConfig (frais agence +
    #    pilote + quai×jours à l'arrivée) AUGMENTÉ du coût prescrit des
    #    opérations d'escale (FLX-05 : Σ EscaleOperation.cost_actual|forecast).
    #    Recomposé de façon déterministe à chaque rollup (idempotent) — la
    #    saisie manuelle n'est volontairement pas préservée ici, le rollup
    #    recalcule (les ajustements vont dans ``other_costs_eur``).
    port_prefill = await _port_fees_prefill(db, leg)
    operations_cost = await _escale_operations_cost(db, leg)
    port_fees_total = _q2(port_prefill + operations_cost)
    if port_fees_total > 0:
        finance.port_fees_eur = port_fees_total

    # 5. Coût des sinistres (FLX-09) — Σ règlement sinon provision des claims
    #    affectés au leg (statuts provisioned/settled). Recalculé à chaque rollup.
    claims_cost = await _claims_cost(db, leg)
    finance.claims_cost_eur = claims_cost

    # 6. Autres coûts — strictement manuel, jamais écrasé.
    port_fees = _dec(finance.port_fees_eur)
    other_costs = _dec(finance.other_costs_eur)

    finance.revenue_eur = revenue
    finance.docker_costs_eur = docker_costs
    finance.opex_share_eur = opex_share
    finance.margin_eur = _q2(
        revenue - port_fees - docker_costs - opex_share - claims_cost - other_costs
    )

    await db.flush()
    return finance


async def _claims_cost(db: AsyncSession, leg: Leg) -> Decimal:
    """Σ coût compagnie des sinistres du leg : règlement si connu, sinon provision."""
    from app.models.claim import Claim

    claims = list(
        (
            await db.execute(
                select(Claim).where(
                    Claim.leg_id == leg.id,
                    Claim.status.in_(("provisioned", "settled")),
                )
            )
        )
        .scalars()
        .all()
    )
    total = Decimal("0")
    for c in claims:
        amount = c.settled_eur if c.settled_eur is not None else c.provision_eur
        total += _dec(amount)
    return _q2(total)
