"""EVO-02 — service/vue unifié des congés & absences.

Unifie **en lecture** les deux modèles historiques qui coexistaient sans
unification (« stub historique ») :

- ``CrewLeave`` (marins, hérité V2) — population « marin » ;
- ``HrAbsence`` (sédentaires, SIRH) — population « sédentaire ».

derrière un DTO commun (``UnifiedLeave``). Choix de conception : **pas de fusion
de schéma** — les deux tables et leurs workflows de saisie/validation restent
distincts (séparation des droits ``crew`` ↔ ``rh``, demi-journées & jours
ouvrés propres au SIRH). Seule la **consultation transverse** est unifiée.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crew import CrewLeave, CrewMember
from app.models.employee import Employee
from app.models.hr_absence import HrAbsence


@dataclass(frozen=True)
class UnifiedLeave:
    """Ligne de congé/absence, indépendante de la population d'origine."""

    source: str  # 'crew' | 'hr'
    population: str  # 'marin' | 'sédentaire'
    person_id: int
    person_name: str
    kind: str
    start_date: _date
    end_date: _date
    status: str
    business_days: Decimal | None  # renseigné côté SIRH uniquement
    reason: str | None


async def list_unified(
    db: AsyncSession,
    *,
    status: str | None = None,
    date_from: _date | None = None,
    date_to: _date | None = None,
) -> list[UnifiedLeave]:
    """Liste fusionnée des congés marins et absences sédentaires.

    Filtres optionnels : ``status`` exact, et chevauchement avec
    [``date_from``, ``date_to``]. Tri par date de début décroissante.
    """
    rows: list[UnifiedLeave] = []

    q_crew = select(CrewLeave, CrewMember.full_name).join(
        CrewMember, CrewLeave.crew_member_id == CrewMember.id
    )
    q_hr = select(HrAbsence, Employee.first_name, Employee.last_name).join(
        Employee, HrAbsence.employee_id == Employee.id
    )
    if status:
        q_crew = q_crew.where(CrewLeave.status == status)
        q_hr = q_hr.where(HrAbsence.status == status)
    if date_from:
        q_crew = q_crew.where(CrewLeave.end_date >= date_from)
        q_hr = q_hr.where(HrAbsence.end_date >= date_from)
    if date_to:
        q_crew = q_crew.where(CrewLeave.start_date <= date_to)
        q_hr = q_hr.where(HrAbsence.start_date <= date_to)

    for lv, full_name in (await db.execute(q_crew)).all():
        rows.append(
            UnifiedLeave(
                source="crew",
                population="marin",
                person_id=lv.crew_member_id,
                person_name=full_name,
                kind=lv.kind,
                start_date=lv.start_date,
                end_date=lv.end_date,
                status=lv.status,
                business_days=None,
                reason=lv.reason,
            )
        )

    for ab, first_name, last_name in (await db.execute(q_hr)).all():
        rows.append(
            UnifiedLeave(
                source="hr",
                population="sédentaire",
                person_id=ab.employee_id,
                person_name=f"{first_name} {last_name}".strip(),
                kind=ab.kind,
                start_date=ab.start_date,
                end_date=ab.end_date,
                status=ab.status,
                business_days=ab.business_days,
                reason=ab.reason,
            )
        )

    rows.sort(key=lambda r: (r.start_date, r.person_name), reverse=True)
    return rows


def summary(rows: list[UnifiedLeave]) -> dict[str, int]:
    """Compteurs par population et par statut en attente."""
    return {
        "total": len(rows),
        "marin": sum(1 for r in rows if r.source == "crew"),
        "sedentaire": sum(1 for r in rows if r.source == "hr"),
        "pending": sum(1 for r in rows if r.status == "requested"),
    }
