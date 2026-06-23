"""ONB-05 — clôture d'escale : checklist documentaire + données du récapitulatif.

Reprise V2 : agrège, pour un leg, l'état de complétude documentaire (checklist
✅/⬜) et toutes les données du PDF récapitulatif de clôture (SOF, documents
cargo, pièces jointes, équipage embarqué, finance, KPI, chaîne de validation).
Lecture seule.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crew import CrewAssignment, CrewMember
from app.models.finance import LegFinance, LegKPI
from app.models.leg import Leg
from app.models.leg_attachment import LegAttachment
from app.models.port import Port
from app.models.sof_event import CargoDocument, SofEvent
from app.models.vessel import Vessel


async def _count(db: AsyncSession, model, leg_id: int) -> int:
    return int(await db.scalar(select(func.count(model.id)).where(model.leg_id == leg_id)) or 0)


async def closure_checklist(db: AsyncSession, leg: Leg) -> list[dict]:
    """Checklist documentaire de clôture : éléments attendus présents/absents."""
    sof_types = {
        r[0]
        for r in (
            await db.execute(select(SofEvent.event_type).where(SofEvent.leg_id == leg.id))
        ).all()
    }
    doc_kinds = {
        r[0]
        for r in (
            await db.execute(select(CargoDocument.kind).where(CargoDocument.leg_id == leg.id))
        ).all()
    }
    n_attachments = await _count(db, LegAttachment, leg.id)
    n_crew = await db.scalar(
        select(func.count(CrewAssignment.id)).where(
            CrewAssignment.leg_id == leg.id, CrewAssignment.embark_at.is_not(None)
        )
    )

    items = [
        ("Départ consigné (SOSP)", "SOSP" in sof_types),
        ("Arrivée consignée (EOSP)", "EOSP" in sof_types),
        ("Notice of Readiness", "NOR" in doc_kinds),
        ("Mate's Receipt", "MATES_RECEIPT" in doc_kinds),
        ("Au moins un document cargo", bool(doc_kinds)),
        ("Pièces jointes déposées", n_attachments > 0),
        ("Équipage embarqué renseigné", bool(n_crew)),
        ("ATA posée", leg.ata is not None),
        ("ATD posée", leg.atd is not None),
    ]
    return [{"label": label, "present": present} for label, present in items]


async def closure_recap_data(db: AsyncSession, leg: Leg) -> dict:
    """Toutes les données du PDF récapitulatif de clôture d'un leg."""
    vessel = await db.get(Vessel, leg.vessel_id) if leg.vessel_id else None
    pol = await db.get(Port, leg.departure_port_id) if leg.departure_port_id else None
    pod = await db.get(Port, leg.arrival_port_id) if leg.arrival_port_id else None

    sof = list(
        (
            await db.execute(
                select(SofEvent)
                .where(SofEvent.leg_id == leg.id)
                .order_by(SofEvent.occurred_at.asc())
            )
        )
        .scalars()
        .all()
    )
    docs = list(
        (
            await db.execute(
                select(CargoDocument)
                .where(CargoDocument.leg_id == leg.id)
                .order_by(CargoDocument.issued_at.asc())
            )
        )
        .scalars()
        .all()
    )
    attachments = list(
        (await db.execute(select(LegAttachment).where(LegAttachment.leg_id == leg.id)))
        .scalars()
        .all()
    )
    crew = list(
        (
            await db.execute(
                select(CrewMember.full_name, CrewMember.role)
                .join(CrewAssignment, CrewAssignment.crew_member_id == CrewMember.id)
                .where(CrewAssignment.leg_id == leg.id, CrewAssignment.embark_at.is_not(None))
                .order_by(CrewMember.full_name)
            )
        ).all()
    )
    finance = (
        await db.execute(select(LegFinance).where(LegFinance.leg_id == leg.id))
    ).scalar_one_or_none()
    kpi = (await db.execute(select(LegKPI).where(LegKPI.leg_id == leg.id))).scalar_one_or_none()

    return {
        "leg": leg,
        "vessel": vessel,
        "pol": pol,
        "pod": pod,
        "sof": sof,
        "docs": docs,
        "attachments": attachments,
        "crew": [{"name": n, "role": r} for n, r in crew],
        "finance": finance,
        "kpi": kpi,
        "checklist": await closure_checklist(db, leg),
        "generated_at": datetime.now(UTC),
    }
