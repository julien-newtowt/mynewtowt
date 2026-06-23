"""FIN-06 — exposition assurance / sinistres (détail provision / indemnité / franchise).

La V3 n'agrégeait qu'un coût sinistre plat (``LegFinance.claims_cost_eur``). On
restaure le **détail** attendu au KPI : montants provisionnés (réserves des
sinistres non clos), indemnités (montants réglés) et franchises (déductibles
supportés par la compagnie, lus sur le contrat d'assurance rattaché).

Lecture seule.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.claim import Claim
from app.models.insurance import InsuranceContract

# Statuts « non clos » : un sinistre encore actif porte une provision (réserve).
_OPEN_STATUSES = ("open", "in_review", "provisioned")


def _dec(value) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)


async def claims_exposure(db: AsyncSession) -> dict:
    """Détail de l'exposition sinistres (lecture seule).

    Renvoie ::

        {
          "claim_count": int,
          "open_count": int,
          "provision_total": Decimal,   # réserves des sinistres non clos
          "settled_total": Decimal,     # indemnités réglées
          "franchise_total": Decimal,   # franchises (déductibles contrat)
          "net_company_total": Decimal, # indemnités + franchises
        }

    La franchise d'un sinistre est le ``deductible_eur`` de son contrat
    d'assurance rattaché (``insurance_contract_id``) ; les sinistres sans
    contrat ne contribuent pas à la franchise.
    """
    claims = list((await db.execute(select(Claim))).scalars().all())

    contract_ids = {c.insurance_contract_id for c in claims if c.insurance_contract_id}
    contracts: dict[int, InsuranceContract] = {}
    if contract_ids:
        rows = (
            await db.execute(
                select(InsuranceContract).where(InsuranceContract.id.in_(contract_ids))
            )
        ).scalars()
        contracts = {ct.id: ct for ct in rows}

    provision_total = Decimal(0)
    settled_total = Decimal(0)
    franchise_total = Decimal(0)
    open_count = 0
    for c in claims:
        if c.status in _OPEN_STATUSES:
            open_count += 1
            provision_total += _dec(c.provision_eur)
        settled_total += _dec(c.settled_eur)
        ct = contracts.get(c.insurance_contract_id) if c.insurance_contract_id else None
        if ct is not None and ct.deductible_eur is not None:
            franchise_total += _dec(ct.deductible_eur)

    return {
        "claim_count": len(claims),
        "open_count": open_count,
        "provision_total": provision_total,
        "settled_total": settled_total,
        "franchise_total": franchise_total,
        "net_company_total": settled_total + franchise_total,
    }
