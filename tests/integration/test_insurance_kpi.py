"""FIN-06 — exposition assurance / sinistres (provision / indemnité / franchise)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.models.claim import Claim
from app.models.insurance import InsuranceContract
from app.services.insurance_kpi import claims_exposure


async def _setup(db) -> None:
    db.add(
        InsuranceContract(
            id=1,
            kind="cargo",
            reference="P&I-2026",
            insurer="Steamship",
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
            deductible_eur=5000.0,
        )
    )
    await db.flush()
    now = datetime(2026, 4, 1, tzinfo=UTC)
    db.add_all(
        [
            # Sinistre ouvert → porte une provision + une franchise (contrat rattaché).
            Claim(
                reference="CLM-2026-0001",
                claim_type="cargo",
                title="A",
                description="x",
                status="open",
                occurred_at=now,
                provision_eur=Decimal("1000"),
                insurance_contract_id=1,
            ),
            # Sinistre réglé → indemnité + franchise, mais plus de provision.
            Claim(
                reference="CLM-2026-0002",
                claim_type="hull",
                title="B",
                description="y",
                status="settled",
                occurred_at=now,
                provision_eur=Decimal("2000"),
                settled_eur=Decimal("800"),
                insurance_contract_id=1,
            ),
            # Sinistre provisionné sans contrat → provision mais aucune franchise.
            Claim(
                reference="CLM-2026-0003",
                claim_type="crew",
                title="C",
                description="z",
                status="provisioned",
                occurred_at=now,
                provision_eur=Decimal("500"),
            ),
        ]
    )
    await db.flush()


@pytest.mark.asyncio
async def test_claims_exposure_detail(db):
    await _setup(db)
    exp = await claims_exposure(db)

    assert exp["claim_count"] == 3
    # open + provisioned comptent comme « non clos » ; settled non.
    assert exp["open_count"] == 2
    # Provisions = sinistres non clos uniquement (1000 + 500), pas le réglé.
    assert exp["provision_total"] == Decimal("1000") + Decimal("500")
    # Indemnités = montants réglés (seul CLM-0002).
    assert exp["settled_total"] == Decimal("800")
    # Franchise = déductible du contrat, par sinistre rattaché (CLM-0001 + CLM-0002).
    assert exp["franchise_total"] == Decimal("10000")
    # Coût net compagnie = indemnités + franchises.
    assert exp["net_company_total"] == Decimal("800") + Decimal("10000")


@pytest.mark.asyncio
async def test_claims_exposure_empty(db):
    exp = await claims_exposure(db)
    assert exp == {
        "claim_count": 0,
        "open_count": 0,
        "provision_total": Decimal(0),
        "settled_total": Decimal(0),
        "franchise_total": Decimal(0),
        "net_company_total": Decimal(0),
    }
