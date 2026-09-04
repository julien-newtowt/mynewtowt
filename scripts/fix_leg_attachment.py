#!/usr/bin/env python3
"""Régularise le rattachement au voyage des ventes à bord et des mouvements de caisse.

CONTEXTE. Jusqu'au correctif de ``_default_leg_id``, le voyage d'une vente était
choisi par ``ORDER BY id DESC`` — le dernier leg *créé*, sans rapport avec celui
en cours. Un voyage planifié pour l'année suivante l'emportait donc sur le
voyage réel : des opérations de 2026 imputées à un départ de 2027. Le code est
corrigé ; ce script répare les lignes écrites avant lui.

CE QUI EST TOUCHÉ. La colonne ``leg_id`` de ``onboard_sales`` et de
``cashbox_movements``, et rien d'autre — jamais un montant, une devise, une date
d'effet, une catégorie, un support ni une description. Chaque correction est
journalisée dans ``activity_logs`` (action ``leg_attachment_fix``).

CE QUI N'EST PAS TOUCHÉ. ``cash_counts.leg_id`` : le routeur ne l'alimente
jamais, la colonne est toujours nulle — le défaut ne l'a pas atteinte.

COMMENT LE VOYAGE EST RETROUVÉ, du plus sûr au plus probable :
  1. lien de règlement — un mouvement né du règlement (ou du remboursement)
     d'une vente hérite du voyage de cette vente ;
  2. recalcul par date — ``planning.current_leg_id`` sur la date d'effet.
Si aucun voyage n'existe avant l'opération, le rattachement passe à NULL plutôt
que de rester faux : une étiquette absente s'interroge, une étiquette fausse se
propage en silence.

SÉCURITÉ : DRY-RUN par défaut (n'écrit rien). Il faut ``--commit`` pour appliquer.

Usage (sur le serveur, comme les autres scripts) :
  docker compose exec app python -m scripts.fix_leg_attachment                     # aperçu
  docker compose exec app python -m scripts.fix_leg_attachment --vessel ANEM       # un navire
  docker compose exec app python -m scripts.fix_leg_attachment --commit            # applique
  docker compose exec app python -m scripts.fix_leg_attachment --realign-all       # cf. ci-dessous

``--realign-all`` recalcule AUSSI les rattachements qui ne sont pas
démontrablement faux. Utile après coup si un ``atd`` saisi a posteriori déplace
la frontière entre deux voyages — mais il écrase alors d'éventuelles corrections
manuelles. À n'utiliser qu'en connaissance de cause.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.models.vessel import Vessel
from app.services import leg_attachment
from app.services.leg_attachment import Reattachment


async def _resolve_vessel_id(session: AsyncSession, vessel_code: str | None) -> int | None:
    if not vessel_code:
        return None
    vessel = (
        await session.execute(select(Vessel).where(Vessel.code == vessel_code.upper()))
    ).scalar_one_or_none()
    if vessel is None:
        raise SystemExit(f"Navire inconnu : {vessel_code}")
    return vessel.id


def _render(corrections: list[Reattachment]) -> None:
    if not corrections:
        print("Aucun rattachement à corriger.")
        return

    ventes = [c for c in corrections if c.kind == "vente"]
    mouvements = [c for c in corrections if c.kind == "mouvement"]
    orphelins = [c for c in corrections if c.drops_attachment]

    print(
        f"{len(corrections)} rattachement(s) à corriger — "
        f"{len(ventes)} vente(s), {len(mouvements)} mouvement(s) de caisse.\n"
    )

    width = max((len(c.label) for c in corrections), default=10)
    for c in corrections:
        cible = c.to_leg_code or "— (aucun voyage déterminable)"
        print(
            f"  {c.kind:<10} {c.label:<{width}}  {c.moment:%Y-%m-%d %H:%M}  "
            f"{c.from_leg_code or c.from_leg_id} → {cible}"
            f"   [{c.basis} · {c.reason}]"
        )

    if orphelins:
        print(
            f"\n⚠ {len(orphelins)} ligne(s) passeront à NULL : aucun voyage ne précède "
            "l'opération pour ce navire. Vérifiez le planning avant d'appliquer."
        )


async def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--vessel", help="Code navire (ex. ANEM). Par défaut : toute la flotte.")
    ap.add_argument("--commit", action="store_true", help="Applique réellement (sinon aperçu).")
    ap.add_argument(
        "--realign-all",
        action="store_true",
        help="Recalcule aussi les rattachements qui ne sont pas démontrablement faux.",
    )
    ap.add_argument("--actor", default="script:fix_leg_attachment", help="Nom porté au journal.")
    args = ap.parse_args()

    async with SessionLocal() as session:
        vessel_id = await _resolve_vessel_id(session, args.vessel)
        corrections = await leg_attachment.plan(
            session, vessel_id=vessel_id, realign_all=args.realign_all
        )
        _render(corrections)

        if not corrections:
            return
        if not args.commit:
            print("\nDRY-RUN — rien n'a été écrit. Relancez avec --commit pour appliquer.")
            return

        changed = await leg_attachment.apply(session, corrections, actor_name=args.actor)
        await session.commit()
        print(f"\n{changed} rattachement(s) corrigé(s) et journalisé(s) dans activity_logs.")


if __name__ == "__main__":
    asyncio.run(main())
