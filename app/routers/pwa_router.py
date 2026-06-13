"""PWA « NEWTOWT Bord » — service worker + manifest à la racine (ARC-01).

Un service worker ne peut contrôler que les pages sous son scope ; servi
depuis ``/sw.js`` (avec ``Service-Worker-Allowed: /``), il couvre
``/onboard*``. Pas d'auth : le SW et le manifest doivent rester
accessibles au navigateur en toutes circonstances (aucune donnée
sensible — fichiers statiques).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["pwa"])

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/sw.js", include_in_schema=False)
async def service_worker() -> FileResponse:
    return FileResponse(
        _STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


@router.get("/manifest.json", include_in_schema=False)
async def web_manifest() -> FileResponse:
    return FileResponse(
        _STATIC_DIR / "manifest.json",
        media_type="application/manifest+json",
    )
