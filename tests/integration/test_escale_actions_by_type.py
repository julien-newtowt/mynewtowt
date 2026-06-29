"""ESC-08 (tranche) — dépendance Type → Action des opérations d'escale.

`ACTIONS_BY_TYPE` partitionne exactement `OPERATION_ACTIONS` sur les types
d'opération ; le formulaire d'escale groupe le sélecteur d'action par type
(`<optgroup>`), exprimant la dépendance sans JS ni rejet serveur.
"""

from __future__ import annotations


def test_actions_by_type_partitions_operation_actions():
    from app.models.escale import ACTIONS_BY_TYPE, OPERATION_ACTIONS, OPERATION_TYPES

    assert set(ACTIONS_BY_TYPE) <= set(OPERATION_TYPES)
    flat = [a for acts in ACTIONS_BY_TYPE.values() for a in acts]
    assert sorted(flat) == sorted(OPERATION_ACTIONS)  # couverture exacte
    assert len(flat) == len(set(flat))  # chaque action une seule fois


def test_escale_form_groups_actions_by_type():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/escale/index.html")[0]
    assert "actions_by_type.items()" in src
    assert "<optgroup" in src
