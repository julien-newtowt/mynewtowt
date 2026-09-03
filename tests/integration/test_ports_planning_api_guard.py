"""Régression — la liste des ports ne chargeait plus dans le planning.

Cause : les selects Zone/Pays/Port du formulaire de leg sont peuplés côté client
par `leg-cascade.js` via `GET /api/v1/ports/search`, verrouillé par
`require_api_key` (SEC-06). L'UI staff n'envoie aucune clé → 503 → cascade vide.

Correctif : `require_api_key_or_staff` autorise une **session staff** authentifiée
(cookie) OU la clé API B2B, appliqué aux 3 routes ports consommées par l'UI
(`/ports/search`, `/ports/nearby`, `/ports/bbox`). Les routes purement B2B
restent sous `require_api_key`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.config import settings
from app.routers.api_v1_router import require_api_key, require_api_key_or_staff


@pytest.mark.asyncio
async def test_staff_session_bypasses_api_key():
    # Une session staff authentifiée suffit — même sans clé API configurée.
    staff = SimpleNamespace(id=1, username="op", role="operation")
    assert await require_api_key_or_staff(x_api_key=None, staff=staff) is None


@pytest.mark.asyncio
async def test_no_staff_no_key_still_503(monkeypatch):
    # Sans staff ET sans clé configurée : comportement B2B inchangé (503).
    monkeypatch.setattr(settings, "public_api_key", None, raising=False)
    with pytest.raises(HTTPException) as ei:
        await require_api_key_or_staff(x_api_key=None, staff=None)
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_no_staff_valid_key_ok(monkeypatch):
    monkeypatch.setattr(settings, "public_api_key", "k-secret-123456", raising=False)
    assert await require_api_key_or_staff(x_api_key="k-secret-123456", staff=None) is None


@pytest.mark.asyncio
async def test_no_staff_wrong_key_401(monkeypatch):
    monkeypatch.setattr(settings, "public_api_key", "k-secret-123456", raising=False)
    with pytest.raises(HTTPException) as ei:
        await require_api_key_or_staff(x_api_key="wrong", staff=None)
    assert ei.value.status_code == 401


def test_ui_port_routes_use_staff_or_key_guard():
    """Verrou anti-régression : les 3 routes ports de l'UI utilisent le garde
    staff-ou-clé ; les routes purement B2B gardent `require_api_key` seul."""
    from app.routers import api_v1_router as mod

    def _dep_funcs(path: str):
        for route in mod.router.routes:
            if getattr(route, "path", None) == path:
                return {d.call for d in route.dependant.dependencies}
        return set()

    for path in (
        "/api/v1/ports/search",
        "/api/v1/ports/nearby",
        "/api/v1/ports/bbox",
        # Ajoutées le 2026-09-02 avec le passage de la cascade côté serveur :
        # sans le garde staff-ou-clé, le formulaire de leg retomberait sur le
        # même 503 → cascade vide.
        "/api/v1/ports/countries",
        "/api/v1/ports/{port_id}",
    ):
        deps = _dep_funcs(path)
        assert require_api_key_or_staff in deps, path
        assert require_api_key not in deps, path


def test_port_by_id_is_declared_after_every_literal_ports_path():
    """FastAPI n'ajoute pas de convertisseur de type au motif de route.

    `/ports/{port_id}` capture n'importe quel segment : déclaré avant
    `/ports/search` ou `/ports/countries`, il les masquerait (mêmes conséquences
    que le piège documenté sur `/captain/ventes`).
    """
    from app.routers import api_v1_router as mod

    paths = [getattr(r, "path", "") for r in mod.router.routes]
    wildcard = paths.index("/api/v1/ports/{port_id}")
    literals = [
        i for i, path in enumerate(paths) if path.startswith("/api/v1/ports/") and "{" not in path
    ]
    assert literals, "aucun chemin littéral /ports/... trouvé — test sans valeur"
    assert max(literals) < wildcard, (
        "un chemin littéral /ports/... est déclaré APRÈS /ports/{port_id} : "
        "il ne sera jamais atteint"
    )
