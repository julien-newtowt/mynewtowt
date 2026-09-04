"""Régularisation du rattachement au voyage des ventes à bord et de la caisse.

**Ce que ce module répare.** Jusqu'au correctif de ``_default_leg_id``, le leg
d'une vente était choisi par ``ORDER BY id DESC`` — le dernier leg *créé*, sans
rapport avec le voyage en cours. Un voyage planifié pour l'année suivante
l'emportait donc sur le voyage réel : des ventes de 2026 se retrouvaient
imputées à un départ de 2027. Le code est corrigé (``planning.current_leg_id``),
mais les lignes écrites avant lui portent toujours le mauvais rattachement, et
tout indicateur par voyage bâti dessus est faux.

**Ce module ne touche QUE la colonne ``leg_id``** — jamais un montant, une
devise, une date d'effet, une catégorie, un support ni une description. Le grand
livre de caisse est append-only pour ce qui fait foi : l'argent. ``leg_id`` est
une étiquette analytique — elle alimente ``onboard_revenue_by_leg`` — et ne
porte aucune écriture comptable.

La contre-passation, instrument prévu pour rectifier un mouvement, serait ici le
mauvais outil : corriger N étiquettes par N mouvements négatifs et N positifs
doublerait le registre et fausserait le solde même que la règle protège. On
corrige donc l'étiquette en place, et chaque correction est journalisée dans
``activity_logs`` — le registre reste auditable, ce qui est l'objet de la règle.

**Deux sources d'attribution, de la plus sûre à la plus probable :**

1. **Le lien de règlement** — un mouvement né du règlement (ou du remboursement)
   d'une vente hérite du leg de cette vente. C'est un fait, pas une déduction,
   et cela garantit que les deux registres racontent la même histoire.
2. **Le recalcul par date** — à défaut de lien, ``planning.current_leg_id``
   répond « quel voyage à cette date », sur la date d'effet du mouvement.

**Ce qu'on ne devine pas.** Si aucun leg n'existe avant la date de l'opération,
le rattachement devient ``NULL`` plutôt que de rester faux : une étiquette
absente se voit et s'interroge, une étiquette fausse se propage en silence dans
les analyses. C'est la même règle que ``schengen_status = indetermine`` ou que
le ``??`` des références tarifaires — dire qu'on ne sait pas, plutôt qu'affirmer
à côté.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leg import Leg
from app.models.onboard_cashbox import CashboxMovement, OnboardCashbox
from app.models.onboard_sales import OnboardSale
from app.services.planning import current_leg_id, ensure_utc

# Origine de l'attribution retenue, reprise telle quelle dans le rapport.
BASIS_SALE_LINK = "lien de règlement"
BASIS_RECOMPUTED = "recalcul par date"

# Motif du changement, pour le journal et le rapport.
REASON_IMPOSSIBLE = "voyage postérieur à l'opération"
REASON_DIVERGENT = "divergence avec la vente réglée"
REASON_REALIGNED = "réalignement demandé"


@dataclass(frozen=True)
class Reattachment:
    """Une correction proposée. Rien n'est écrit tant qu'``apply`` n'est pas appelé."""

    kind: str  # "vente" | "mouvement"
    row_id: int
    label: str
    vessel_id: int
    moment: datetime
    from_leg_id: int | None
    from_leg_code: str | None
    to_leg_id: int | None
    to_leg_code: str | None
    basis: str
    reason: str

    @property
    def drops_attachment(self) -> bool:
        """Vrai quand aucun voyage ne peut être déterminé : on passe à ``NULL``."""
        return self.to_leg_id is None


def sale_moment(sale: OnboardSale) -> datetime:
    """Instant qui fait foi pour rattacher une vente à un voyage.

    Le règlement (``paid_at``) prime sur la saisie : c'est lui qui matérialise
    l'opération. Une vente non réglée n'a que sa création, qui reste la
    meilleure approximation du moment où elle a eu lieu à bord.
    """
    return ensure_utc(sale.paid_at or sale.created_at)


def _departure(leg: Leg) -> datetime:
    """Départ du voyage : le réel (``atd``) quand il est connu, sinon le prévu."""
    return ensure_utc(leg.atd or leg.etd)


def is_impossible(leg: Leg, moment: datetime) -> bool:
    """Vrai si l'opération précède le départ du voyage auquel elle est rattachée.

    C'est le défaut constaté : des ventes imputées à un voyage qui n'était pas
    encore parti. On ne juge pas la borne d'arrivée — une opération postérieure
    à l'``ata`` peut légitimement relever du voyage (régularisation tardive
    d'une dépense d'escale). Un rattachement *antérieur au départ*, lui, n'a
    aucune lecture métier possible.
    """
    return ensure_utc(moment) < _departure(leg)


async def _legs_by_id(db: AsyncSession, ids: set[int]) -> dict[int, Leg]:
    if not ids:
        return {}
    rows = (await db.execute(select(Leg).where(Leg.id.in_(ids)))).scalars().all()
    return {leg.id: leg for leg in rows}


async def _resolve(
    db: AsyncSession,
    vessel_id: int,
    moment: datetime,
    cache: dict[tuple[int, datetime], int | None],
) -> int | None:
    """``current_leg_id`` avec mémoïsation — 19 lignes du même jour = 1 requête."""
    key = (vessel_id, moment)
    if key not in cache:
        cache[key] = await current_leg_id(db, vessel_id, when=moment)
    return cache[key]


async def plan(
    db: AsyncSession, *, vessel_id: int | None = None, realign_all: bool = False
) -> list[Reattachment]:
    """Liste les corrections à apporter, sans rien écrire.

    Par défaut, on ne touche que ce qui est **démontrablement faux** :

    * une opération antérieure au départ du voyage auquel elle est rattachée ;
    * un mouvement de caisse dont le voyage diverge de celui de la vente qu'il
      règle — les deux registres doivent raconter la même histoire.

    ``realign_all`` recalcule en plus tous les rattachements existants sur la
    règle en vigueur. Utile après un changement de planning (un ``atd`` saisi
    a posteriori déplace la frontière entre deux voyages), mais il écrase alors
    d'éventuelles corrections manuelles : ce n'est pas le défaut.
    """
    out: list[Reattachment] = []
    cache: dict[tuple[int, datetime], int | None] = {}

    # ── 1. Ventes ───────────────────────────────────────────────────────────
    sales_q = select(OnboardSale).where(OnboardSale.leg_id.is_not(None))
    if vessel_id is not None:
        sales_q = sales_q.where(OnboardSale.vessel_id == vessel_id)
    sales = list((await db.execute(sales_q.order_by(OnboardSale.id))).scalars().all())

    legs = await _legs_by_id(db, {s.leg_id for s in sales if s.leg_id is not None})

    # Leg retenu pour chaque vente (corrigé si besoin) — sert ensuite aux
    # mouvements qui en découlent.
    leg_for_sale: dict[int, int | None] = {}

    for sale in sales:
        moment = sale_moment(sale)
        leg = legs.get(sale.leg_id) if sale.leg_id is not None else None
        broken = leg is not None and is_impossible(leg, moment)
        if not (broken or realign_all):
            leg_for_sale[sale.id] = sale.leg_id
            continue

        target = await _resolve(db, sale.vessel_id, moment, cache)
        leg_for_sale[sale.id] = target
        if target == sale.leg_id:
            continue

        target_leg = legs.get(target) if target is not None else None
        if target is not None and target_leg is None:
            target_leg = await db.get(Leg, target)
            if target_leg is not None:
                legs[target] = target_leg
        out.append(
            Reattachment(
                kind="vente",
                row_id=sale.id,
                label=sale.reference,
                vessel_id=sale.vessel_id,
                moment=moment,
                from_leg_id=sale.leg_id,
                from_leg_code=leg.leg_code if leg is not None else None,
                to_leg_id=target,
                to_leg_code=target_leg.leg_code if target_leg is not None else None,
                basis=BASIS_RECOMPUTED,
                reason=REASON_IMPOSSIBLE if broken else REASON_REALIGNED,
            )
        )

    # ── 2. Mouvements issus d'un règlement : ils suivent leur vente ─────────
    # Un mouvement né du règlement ou du remboursement d'une vente n'a pas à
    # être redaté : son voyage EST celui de la vente. C'est exact, et cela
    # garantit que caisse et ventes ne divergent pas.
    from_sale: dict[int, OnboardSale] = {}
    for sale in sales:
        for mov_id in (sale.cashbox_movement_id, sale.refund_cashbox_movement_id):
            if mov_id is not None:
                from_sale[mov_id] = sale

    # ── 3. Mouvements de caisse ─────────────────────────────────────────────
    mov_q = (
        select(CashboxMovement, OnboardCashbox.vessel_id)
        .join(OnboardCashbox, OnboardCashbox.id == CashboxMovement.cashbox_id)
        .where(CashboxMovement.leg_id.is_not(None))
    )
    if vessel_id is not None:
        mov_q = mov_q.where(OnboardCashbox.vessel_id == vessel_id)
    movements = list((await db.execute(mov_q.order_by(CashboxMovement.id))).all())

    legs.update(
        await _legs_by_id(
            db, {m.leg_id for m, _ in movements if m.leg_id is not None and m.leg_id not in legs}
        )
    )

    for mov, mov_vessel_id in movements:
        leg = legs.get(mov.leg_id) if mov.leg_id is not None else None
        moment = ensure_utc(mov.occurred_at)
        # Nom distinct du `sale` de la boucle précédente : réutiliser le même
        # mélangeait deux types (`OnboardSale` puis `OnboardSale | None`).
        linked_sale = from_sale.get(mov.id)

        if linked_sale is not None:
            target = leg_for_sale.get(linked_sale.id, linked_sale.leg_id)
            basis = BASIS_SALE_LINK
            reason = REASON_DIVERGENT
        else:
            broken = leg is not None and is_impossible(leg, moment)
            if not (broken or realign_all):
                continue
            target = await _resolve(db, mov_vessel_id, moment, cache)
            basis = BASIS_RECOMPUTED
            reason = REASON_IMPOSSIBLE if broken else REASON_REALIGNED

        if target == mov.leg_id:
            continue

        target_leg = legs.get(target) if target is not None else None
        if target is not None and target_leg is None:
            target_leg = await db.get(Leg, target)
            if target_leg is not None:
                legs[target] = target_leg
        out.append(
            Reattachment(
                kind="mouvement",
                row_id=mov.id,
                label=f"#{mov.id} {mov.category}",
                vessel_id=mov_vessel_id,
                moment=moment,
                from_leg_id=mov.leg_id,
                from_leg_code=leg.leg_code if leg is not None else None,
                to_leg_id=target,
                to_leg_code=target_leg.leg_code if target_leg is not None else None,
                basis=basis,
                reason=reason,
            )
        )

    return out


async def apply(
    db: AsyncSession,
    corrections: list[Reattachment],
    *,
    actor_name: str = "script",
    actor_id: int | None = None,
) -> int:
    """Applique les corrections et journalise chacune d'elles.

    La trace est le prix d'entrée pour écrire dans un registre append-only : on
    doit pouvoir répondre plus tard « qui a changé quoi, quand, et pourquoi ».
    Chaque ligne produit une entrée ``activity_logs`` portant l'ancien et le
    nouveau voyage, la base d'attribution et le motif.
    """
    from app.services.activity import record as activity_record

    changed = 0
    for c in corrections:
        obj: OnboardSale | CashboxMovement | None
        if c.kind == "vente":
            obj = await db.get(OnboardSale, c.row_id)
        else:
            obj = await db.get(CashboxMovement, c.row_id)
        if obj is None:  # pragma: no cover - la ligne a disparu entre plan et apply
            continue
        obj.leg_id = c.to_leg_id
        changed += 1
        await activity_record(
            db,
            action="leg_attachment_fix",
            user_name=actor_name,
            user_id=actor_id,
            module="ventes",
            entity_type=c.kind,
            entity_id=c.row_id,
            entity_label=c.label,
            # Une phrase, pas un dict : la colonne est du texte libre relu par
            # un humain dans /admin/activity-logs. Un `repr` de dictionnaire y
            # serait illisible au moment où on en a besoin — un contrôle.
            detail=(
                f"Rattachement corrigé : {c.from_leg_code or c.from_leg_id or '—'} → "
                f"{c.to_leg_code or 'aucun voyage'} "
                f"(opération du {c.moment:%Y-%m-%d %H:%M} UTC ; "
                f"{c.basis} ; {c.reason})"
            ),
        )
    await db.flush()
    return changed
