"""EVO-03 — suppression des collisions de routes `erp_scaffold`.

Le routeur `erp_scaffold_router` (stubs escale/crew/finance/mrv/claims/tracking/
admin/analytics) doublonnait silencieusement de vrais routers selon l'ordre
d'inclusion. Il n'était plus câblé : on le supprime et on garde un garde-fou
contre toute réintroduction.
"""

from __future__ import annotations

import importlib

import pytest


def test_erp_scaffold_router_module_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.routers.erp_scaffold_router")
