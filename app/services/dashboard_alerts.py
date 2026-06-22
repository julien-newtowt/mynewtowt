"""ADM-02 — moteur d'alertes proactives du dashboard.

Reprise V2 (``dashboard_router.compute_alerts``) adaptée au modèle V3 : répond
à « qu'est-ce qui ne va pas aujourd'hui ? » en balayant les legs de l'année et
en remontant 6 familles d'anomalies, triées par sévérité (danger > warning >
info), avec un deep-link vers l'écran concerné.

Familles :
1. Retard d'arrivée — ATA postérieure à l'ETA de plus de 24 h.
2. ETA dépassée — ETA passée de plus de 24 h sans ATA/ATD.
3. Escale non verrouillée — ATD posée mais escale non verrouillée.
4. Départ imminent sans préparation — ETD < 48 h, aucune opération d'escale.
5. Conflit de port — deux navires différents au même port à < 48 h d'écart.
6. Commandes non affectées — commandes actives sans leg ni affectation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial import Order, OrderAssignment
from app.models.escale import EscaleOperation
from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel

_SEVERITY_ORDER = {"danger": 0, "warning": 1, "info": 2}
_ACTIVE_ORDER_STATUSES = ("draft", "confirmed")


def _h(delta_seconds: float) -> int:
    return int(delta_seconds / 3600)


def _aware(dt: datetime | None) -> datetime | None:
    """Normalise en aware-UTC (SQLite relit des datetimes naïfs ; Postgres aware)."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


async def compute_alerts(db: AsyncSession, year: int | None = None) -> list[dict]:
    """Calcule les alertes actives, triées danger → warning → info."""
    now = datetime.now(UTC)
    year = year or now.year

    legs = list(
        (
            await db.execute(
                select(Leg).where(Leg.status != "cancelled").order_by(Leg.vessel_id, Leg.etd)
            )
        )
        .scalars()
        .all()
    )
    legs = [lg for lg in legs if lg.etd and lg.etd.year == year]
    vessels = {v.id: v for v in (await db.execute(select(Vessel))).scalars().all()}
    ports = {p.id: p for p in (await db.execute(select(Port))).scalars().all()}

    alerts: list[dict] = []

    def _vname(leg: Leg) -> str:
        v = vessels.get(leg.vessel_id)
        return v.name if v else f"navire #{leg.vessel_id}"

    def _pname(port_id: int | None) -> str:
        p = ports.get(port_id)
        return p.name if p else "?"

    for leg in legs:
        link = f"/escale?leg_id={leg.id}"
        eta = _aware(leg.eta)
        ata = _aware(leg.ata)
        atd = _aware(leg.atd)
        etd = _aware(leg.etd)

        # 1. Retard d'arrivée (ATA > ETA + 24 h).
        if ata and eta:
            delay_h = (ata - eta).total_seconds() / 3600
            if delay_h > 24:
                alerts.append({
                    "family": "retard",
                    "severity": "warning" if delay_h < 72 else "danger",
                    "icon": "clock",
                    "title": f"Retard {leg.leg_code}",
                    "message": (
                        f"{_vname(leg)} — arrivée {_pname(leg.arrival_port_id)} avec "
                        f"{_h((ata - eta).total_seconds())} h de retard"
                    ),
                    "link": link,
                })

        # 2. ETA dépassée (>24 h) sans ATA/ATD.
        if eta and not ata and not atd:
            overdue_h = (now - eta).total_seconds() / 3600
            if overdue_h > 24:
                alerts.append({
                    "family": "retard",
                    "severity": "danger",
                    "icon": "alert-triangle",
                    "title": f"ETA dépassée {leg.leg_code}",
                    "message": (
                        f"{_vname(leg)} → {_pname(leg.arrival_port_id)} — ETA dépassée de "
                        f"{_h((now - eta).total_seconds())} h, ATA non renseignée"
                    ),
                    "link": link,
                })

        # 3. Escale non verrouillée (ATD posée mais pas de lock).
        if atd and leg.escale_locked_at is None:
            alerts.append({
                "family": "verrouillage",
                "severity": "info",
                "icon": "unlock",
                "title": f"Escale non verrouillée {leg.leg_code}",
                "message": f"{_vname(leg)} — ATD posée mais escale non verrouillée",
                "link": link,
            })

        # 4. Départ imminent (<48 h) sans opération planifiée.
        if etd and not atd:
            hours_to_dep = (etd - now).total_seconds() / 3600
            if 0 < hours_to_dep < 48:
                ops_count = await db.scalar(
                    select(func.count(EscaleOperation.id)).where(EscaleOperation.leg_id == leg.id)
                )
                if not ops_count:
                    alerts.append({
                        "family": "preparation",
                        "severity": "warning",
                        "icon": "alert-circle",
                        "title": f"Départ imminent {leg.leg_code}",
                        "message": (
                            f"{_vname(leg)} — départ {_pname(leg.departure_port_id)} dans "
                            f"{_h((etd - now).total_seconds())} h, aucune opération planifiée"
                        ),
                        "link": link,
                    })

    # 5. Conflit de port — deux navires différents, même port d'arrivée, <48 h.
    by_port: dict[int, list[Leg]] = {}
    for leg in legs:
        if leg.eta and leg.arrival_port_id:
            by_port.setdefault(leg.arrival_port_id, []).append(leg)
    seen_conflicts: set[str] = set()
    for port_id, plegs in by_port.items():
        for i in range(len(plegs)):
            for j in range(i + 1, len(plegs)):
                a, b = plegs[i], plegs[j]
                if a.vessel_id == b.vessel_id:
                    continue
                if abs((_aware(a.eta) - _aware(b.eta)).total_seconds()) / 3600 < 48:
                    key = f"{port_id}-{min(a.id, b.id)}-{max(a.id, b.id)}"
                    if key in seen_conflicts:
                        continue
                    seen_conflicts.add(key)
                    alerts.append({
                        "family": "conflit",
                        "severity": "warning",
                        "icon": "alert-triangle",
                        "title": f"Conflit port {_pname(port_id)}",
                        "message": (
                            f"{_vname(a)} ({a.leg_code}) et {_vname(b)} ({b.leg_code}) — "
                            f"ETA à moins de 48 h d'écart"
                        ),
                        "link": f"/planning?leg_id={a.id}",
                    })

    # 6. Commandes actives non affectées (ni leg ni affectation).
    has_assignment = (
        select(OrderAssignment.id).where(OrderAssignment.order_id == Order.id).exists()
    )
    unassigned = await db.scalar(
        select(func.count(Order.id)).where(
            Order.status.in_(_ACTIVE_ORDER_STATUSES),
            Order.leg_id.is_(None),
            ~has_assignment,
        )
    )
    if unassigned:
        plural = "s" if unassigned > 1 else ""
        alerts.append({
            "family": "commercial",
            "severity": "info",
            "icon": "package",
            "title": f"{unassigned} commande{plural} non affectée{plural}",
            "message": "Des commandes actives attendent une affectation à un leg.",
            "link": "/commercial/orders",
        })

    alerts.sort(key=lambda a: _SEVERITY_ORDER.get(a["severity"], 3))
    return alerts
