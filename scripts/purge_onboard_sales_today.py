#!/usr/bin/env python3
"""Purge les ventes à bord et les mouvements de stock ENREGISTRÉS AUJOURD'HUI.

Outil de remise à zéro après une session de test de la « vente à bord »
(ex. ventes VB-… et avitaillements créés en essayant le paiement CB). Supprime,
pour la journée en cours :

  • les ventes (``onboard_sales``) créées depuis minuit → leurs lignes
    (``onboard_sale_lines``) partent en cascade (ON DELETE CASCADE) ;
  • les mouvements de stock (``onboard_stock_movements``) enregistrés depuis
    minuit (avitaillements, ventes, ajustements… saisis aujourd'hui).

⚠️ La CAISSE n'est pas touchée par défaut : une vente réglée a créé un
   ``cashbox_movements`` (catégorie « vente_a_bord ») que la suppression de la
   vente laisse en place (FK ON DELETE SET NULL). Utilisez ``--with-cashbox``
   pour supprimer AUSSI les mouvements de caisse des ventes purgées.

SÉCURITÉ : DRY-RUN par défaut (n'écrit rien). Il faut ``--commit`` pour
supprimer réellement. « Aujourd'hui » = depuis minuit dans le fuseau ``--tz``
(défaut Europe/Paris) ; surchargeable par ``--since AAAA-MM-JJ[THH:MM]``.

Usage (sur le serveur ; invocation via ``-m``, comme les autres scripts) :
  docker compose exec app python -m scripts.purge_onboard_sales_today             # aperçu
  docker compose exec app python -m scripts.purge_onboard_sales_today --commit     # supprime
  docker compose exec app python -m scripts.purge_onboard_sales_today --vessel ANEM --with-cashbox --commit
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.models.onboard_cashbox import CashboxMovement
from app.models.onboard_sales import OnboardSale, OnboardStockMovement
from app.models.vessel import Vessel


async def _resolve_vessel_id(session: AsyncSession, vessel_code: str | None) -> int | None:
    if not vessel_code:
        return None
    vid = await session.scalar(select(Vessel.id).where(Vessel.code == vessel_code))
    if vid is None:
        raise SystemExit(f"Navire introuvable pour le code « {vessel_code} ».")
    return vid


async def purge(
    session: AsyncSession,
    cutoff: datetime,
    *,
    commit: bool,
    with_cashbox: bool,
    vessel_id: int | None,
) -> dict:
    """Sélectionne (et supprime si ``commit``) ventes + mouvements du jour.

    Renvoie un compte-rendu ``{sales, moves, cashbox_ids, deleted}``. La
    transaction n'est validée QUE si ``commit`` est vrai. Testable : la session
    est injectée (pas de commit implicite en dry-run).
    """
    sales_q = select(OnboardSale).where(OnboardSale.created_at >= cutoff)
    moves_q = select(OnboardStockMovement).where(OnboardStockMovement.recorded_at >= cutoff)
    if vessel_id is not None:
        sales_q = sales_q.where(OnboardSale.vessel_id == vessel_id)
        moves_q = moves_q.where(OnboardStockMovement.vessel_id == vessel_id)

    sales = list((await session.execute(sales_q.order_by(OnboardSale.id))).scalars().all())
    moves = list((await session.execute(moves_q.order_by(OnboardStockMovement.id))).scalars().all())
    cashbox_ids = [s.cashbox_movement_id for s in sales if s.cashbox_movement_id is not None]

    if not commit or (not sales and not moves):
        return {"sales": sales, "moves": moves, "cashbox_ids": cashbox_ids, "deleted": False}

    # Suppression, transaction unique. Ordre : caisse (option) → mouvements de
    # stock → ventes (lignes en cascade DB). Les mouvements liés aux ventes ont
    # sale_id ON DELETE SET NULL : ils sont retirés ici par le filtre « du jour »,
    # pas par la suppression des ventes.
    # synchronize_session=False : DELETE en masse exécuté côté base (pas
    # d'évaluation Python de la condition), plus efficace et sans ambiguïté de
    # comparaison de dates.
    opts = {"synchronize_session": False}
    if with_cashbox and cashbox_ids:
        await session.execute(
            delete(CashboxMovement).where(CashboxMovement.id.in_(cashbox_ids)), execution_options=opts
        )
    if moves:
        del_moves = delete(OnboardStockMovement).where(OnboardStockMovement.recorded_at >= cutoff)
        if vessel_id is not None:
            del_moves = del_moves.where(OnboardStockMovement.vessel_id == vessel_id)
        await session.execute(del_moves, execution_options=opts)
    if sales:
        del_sales = delete(OnboardSale).where(OnboardSale.created_at >= cutoff)
        if vessel_id is not None:
            del_sales = del_sales.where(OnboardSale.vessel_id == vessel_id)
        await session.execute(del_sales, execution_options=opts)

    await session.commit()
    return {"sales": sales, "moves": moves, "cashbox_ids": cashbox_ids, "deleted": True}


async def _run(cutoff: datetime, *, commit: bool, with_cashbox: bool, vessel_id: int | None) -> None:
    async with SessionLocal() as session:
        report = await purge(
            session, cutoff, commit=commit, with_cashbox=with_cashbox, vessel_id=vessel_id
        )
        sales, moves, cashbox_ids = report["sales"], report["moves"], report["cashbox_ids"]

        print(f"Seuil « aujourd'hui » : ≥ {cutoff.isoformat()}")
        if vessel_id is not None:
            print(f"Navire : id={vessel_id}")
        print(f"\nVentes concernées ...................... {len(sales)}")
        for s in sales:
            paid = " · RÉGLÉE (caisse)" if s.cashbox_movement_id is not None else ""
            print(f"  - {s.reference}  [{s.status}]  {s.total} {s.currency}{paid}")
        print(f"Mouvements de stock concernés .......... {len(moves)}")
        for m in moves:
            print(f"  - #{m.id}  produit={m.product_id}  qty={m.qty}  motif={m.reason}")
        if with_cashbox:
            print(f"Mouvements de caisse liés .............. {len(cashbox_ids)}")

        if not sales and not moves:
            print("\nRien à purger pour cette période.")
        elif report["deleted"]:
            print(
                f"\n✓ Purge effectuée : {len(sales)} vente(s), {len(moves)} mouvement(s) de stock"
                + (f", {len(cashbox_ids)} mouvement(s) de caisse" if with_cashbox else "")
                + " supprimé(s)."
            )
        else:
            print("\n[DRY-RUN] Aucune suppression effectuée. Relancez avec --commit pour appliquer.")
            if cashbox_ids and not with_cashbox:
                print(
                    f"Note : {len(cashbox_ids)} mouvement(s) de caisse resteront (ventes réglées). "
                    "Ajoutez --with-cashbox pour les retirer aussi."
                )


def _cutoff(tz_name: str, since: str | None) -> datetime:
    tz = ZoneInfo(tz_name)
    if since:
        # Accepte « AAAA-MM-JJ » ou « AAAA-MM-JJTHH:MM ».
        dt = datetime.fromisoformat(since)
        return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt
    return datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)


def main() -> None:
    p = argparse.ArgumentParser(description="Purge des ventes à bord + mouvements de stock du jour.")
    p.add_argument("--commit", action="store_true", help="applique la suppression (défaut : dry-run)")
    p.add_argument("--tz", default="Europe/Paris", help="fuseau pour « aujourd'hui » (défaut Europe/Paris)")
    p.add_argument("--since", default=None, help="seuil explicite AAAA-MM-JJ[THH:MM] (remplace « aujourd'hui »)")
    p.add_argument("--vessel", default=None, help="restreindre à un navire (code, ex. ANEM)")
    p.add_argument("--with-cashbox", action="store_true", help="supprime aussi les mouvements de caisse des ventes purgées")
    args = p.parse_args()

    cutoff = _cutoff(args.tz, args.since)

    async def _entry() -> None:
        async with SessionLocal() as s:
            vessel_id = await _resolve_vessel_id(s, args.vessel)
        await _run(cutoff, commit=args.commit, with_cashbox=args.with_cashbox, vessel_id=vessel_id)

    asyncio.run(_entry())


if __name__ == "__main__":
    main()
