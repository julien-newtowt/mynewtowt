"""Vérifie l'intégrité des signatures SOF / noon / watch.

Pour chaque enregistrement *signé* (``signature_hash`` non nul), recalcule
le hash à partir du contenu actuel et compare avec le hash stocké.
Tout écart est rapporté comme une violation potentielle (modification
post-signature → contrevient norme IMO sur SOF).

Usage :
  python -m scripts.verify_signatures                      # rapport texte
  python -m scripts.verify_signatures --json               # sortie JSON pipeable
  python -m scripts.verify_signatures --fix-locked         # nothing (lecture seule)
  python -m scripts.verify_signatures --limit 100          # 100 records / type max

Exit code :
  0 = tout est cohérent
  1 = au moins 1 violation détectée
  2 = erreur d'exécution (DB / connection)

À planifier en cron quotidien (ex. 03h00 UTC) :
  0 3 * * *  docker compose exec -T app python -m scripts.verify_signatures \
             >> /var/log/mynewtowt/signature-audit.log 2>&1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.models.noon_report import NoonReport
from app.models.sof_event import SofEvent
from app.models.watch_log import WatchLog
from app.services.signature import (
    compute_noon_hash, compute_sof_hash, compute_watch_hash,
)


logger = logging.getLogger("verify_signatures")


@dataclass
class Violation:
    kind: str
    record_id: int
    leg_id: int | None
    label: str
    stored_hash: str
    recomputed_hash: str
    signed_by: str | None
    signed_at: str | None


async def _check_one(
    db: AsyncSession,
    model,
    hash_fn: Callable,
    *,
    limit: int | None,
    kind: str,
    label_fn: Callable,
) -> tuple[int, list[Violation]]:
    """Renvoie (nb_records_signed, violations)."""
    stmt = select(model).where(model.signature_hash.is_not(None))
    if limit:
        stmt = stmt.limit(limit)
    records = list((await db.execute(stmt)).scalars().all())
    violations: list[Violation] = []
    for r in records:
        recomputed = hash_fn(r)
        if recomputed != r.signature_hash:
            violations.append(Violation(
                kind=kind,
                record_id=r.id,
                leg_id=getattr(r, "leg_id", None),
                label=label_fn(r),
                stored_hash=r.signature_hash,
                recomputed_hash=recomputed,
                signed_by=getattr(r, "signed_by_name", None),
                signed_at=str(getattr(r, "signed_at", "")),
            ))
    return len(records), violations


async def main(*, limit: int | None, json_output: bool) -> int:
    async with SessionLocal() as db:
        try:
            sof_checked, sof_v = await _check_one(
                db, SofEvent, compute_sof_hash,
                limit=limit, kind="sof_event",
                label_fn=lambda e: f"{e.event_type}@{e.occurred_at}",
            )
            noon_checked, noon_v = await _check_one(
                db, NoonReport, compute_noon_hash,
                limit=limit, kind="noon_report",
                label_fn=lambda n: f"leg={n.leg_id}@{n.recorded_at}",
            )
            watch_checked, watch_v = await _check_one(
                db, WatchLog, compute_watch_hash,
                limit=limit, kind="watch_log",
                label_fn=lambda w: f"leg={w.leg_id} {w.watch_date} {w.watch_period}",
            )
        except Exception as e:
            logger.error("Database error: %s", e)
            return 2

    all_violations = sof_v + noon_v + watch_v
    total_checked = sof_checked + noon_checked + watch_checked

    if json_output:
        out = {
            "checked": {
                "sof_event": sof_checked,
                "noon_report": noon_checked,
                "watch_log": watch_checked,
                "total": total_checked,
            },
            "violations": [v.__dict__ for v in all_violations],
        }
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"Signed records checked  : {total_checked}")
        print(f"  - sof_event   : {sof_checked}")
        print(f"  - noon_report : {noon_checked}")
        print(f"  - watch_log   : {watch_checked}")
        print(f"Violations              : {len(all_violations)}")
        for v in all_violations:
            print(
                f"  ✗ {v.kind} #{v.record_id} ({v.label}) "
                f"signed_by={v.signed_by} at={v.signed_at}"
            )
            print(f"      stored     {v.stored_hash}")
            print(f"      recomputed {v.recomputed_hash}")

    return 1 if all_violations else 0


def cli() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--json", dest="json_output", action="store_true",
                   help="Sortie JSON (pour pipeline / alerting)")
    p.add_argument("--limit", type=int, default=None,
                   help="Limite par type (utile en CI / dev). Default: tout.")
    args = p.parse_args()
    return asyncio.run(main(limit=args.limit, json_output=args.json_output))


if __name__ == "__main__":
    sys.exit(cli())
