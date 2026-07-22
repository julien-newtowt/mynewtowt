"""QHSE — règles de qualité (Phase 0 : RQ01-RQ03).

Enregistre les premières règles du scope ``qhse`` dans le moteur générique
``validation_engine`` (même patron que ``validation_rules_catalog`` pour
MRV) — aucune modification du moteur lui-même : ``run_rules(db, "qhse",
subjects=[...])`` suffit à les déclencher sur des sujets duck-typés
(``QhseReport``-like : ``subject``/``description``/``issued_date``/
``closed_date``/``vessel_id``).

Ces 3 règles couvrent les anomalies déjà identifiées dans l'analyse des
exports source (cahier des charges §3.5) : le record de test "Essai de non
conformité" (``ClosedDate`` antérieur à ``IssuedDate``), et l'exigence de
résolution stricte du navire à l'ingestion (§2.1.B).
"""

from __future__ import annotations

import re

from app.services.validation_engine import CheckOutcome, RuleContext, _get, _present, rule

_TEST_PATTERN_RE = re.compile(r"\b(test|essai|demo)\b", re.IGNORECASE)


@rule("RQ01")
async def _rq01_date_consistency(ctx: RuleContext) -> list[CheckOutcome]:
    """RQ01 — ``closed_date`` ne peut pas précéder ``issued_date``."""
    issued = _get(ctx.subject, "issued_date")
    closed = _get(ctx.subject, "closed_date")
    if _present(issued) and _present(closed) and closed < issued:
        return [
            CheckOutcome(
                "fail",
                "Date de clôture antérieure à la date d'émission — probable donnée de test.",
                {"issued_date": str(issued), "closed_date": str(closed)},
            )
        ]
    return [CheckOutcome("pass", "Cohérence des dates OK.")]


@rule("RQ02")
async def _rq02_test_keyword(ctx: RuleContext) -> list[CheckOutcome]:
    """RQ02 — ``subject``/``description`` contient un motif de test connu."""
    subject = str(_get(ctx.subject, "subject") or "")
    description = str(_get(ctx.subject, "description") or "")
    match = _TEST_PATTERN_RE.search(subject) or _TEST_PATTERN_RE.search(description)
    if match:
        return [
            CheckOutcome(
                "fail",
                f"Motif de test détecté (« {match.group(0)} ») — à confirmer avant import en production.",
                {"matched": match.group(0)},
            )
        ]
    return [CheckOutcome("pass", "Aucun motif de test détecté.")]


@rule("RQ03")
async def _rq03_vessel_resolved(ctx: RuleContext) -> list[CheckOutcome]:
    """RQ03 — le navire doit être résolu vers le référentiel ``vessels`` existant."""
    vessel_id = _get(ctx.subject, "vessel_id")
    if not _present(vessel_id):
        return [
            CheckOutcome(
                "fail",
                "Navire non résolu vers le référentiel MyTOWT — ligne à quarantainer.",
            )
        ]
    return [CheckOutcome("pass", "Navire résolu.")]
