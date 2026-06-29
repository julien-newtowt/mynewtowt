"""ESC-08 — vue en swim-lanes des opérations d'escale par catégorie.

`operations_by_lane` regroupe les opérations par ``operation_type`` (lanes non
vides, ordre canonique des types), trie chaque lane par borne de début.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace


def _op(op_type, *, action="autre", actual_start=None, planned_start=None):
    return SimpleNamespace(
        operation_type=op_type,
        action=action,
        actual_start=actual_start,
        planned_start=planned_start,
    )


def test_empty():
    from app.services.leg_overview import operations_by_lane

    assert operations_by_lane([]) == []
    assert operations_by_lane(None) == []


def test_groups_by_type_canonical_order():
    from app.services.leg_overview import operations_by_lane

    # commercial avant technique en entrée → l'ordre de sortie suit OPERATION_TYPES.
    ops = [_op("commercial"), _op("technique"), _op("armement")]
    lanes = operations_by_lane(ops)
    assert [lane["type"] for lane in lanes] == ["technique", "armement", "commercial"]
    # chaque lane non vide, libellé FR présent.
    assert all(lane["ops"] for lane in lanes)
    labels = {lane["type"]: lane["label"] for lane in lanes}
    assert labels["technique"] == "Technique"


def test_empty_lanes_excluded():
    from app.services.leg_overview import operations_by_lane

    lanes = operations_by_lane([_op("documentaire")])
    assert len(lanes) == 1
    assert lanes[0]["type"] == "documentaire"


def test_intra_lane_sorted_by_start():
    from app.services.leg_overview import operations_by_lane

    t1 = datetime(2026, 1, 1, 8, tzinfo=UTC)
    t2 = datetime(2026, 1, 1, 12, tzinfo=UTC)
    # ordre d'entrée inversé ; actual_start prioritaire ; sans borne en queue.
    ops = [
        _op("technique", action="b", planned_start=t2),
        _op("technique", action="c"),  # aucune borne → en dernier
        _op("technique", action="a", actual_start=t1),
    ]
    lane = operations_by_lane(ops)[0]
    assert [o.action for o in lane["ops"]] == ["a", "b", "c"]


def test_unknown_type_not_lost():
    from app.services.leg_overview import operations_by_lane

    lanes = operations_by_lane([_op("technique"), _op("inconnu")])
    types = [lane["type"] for lane in lanes]
    assert "inconnu" in types
    # le type connu reste en tête, l'inconnu en queue.
    assert types[0] == "technique"


def test_escale_template_has_lanes():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/escale/index.html")[0]
    assert "Activités parallèles" in src
    assert "lanes" in src
