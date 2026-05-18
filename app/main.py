"""FastAPI entrypoint — assembles middlewares, routers, exception handlers."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from app import __version__
from app.auth import AuthError, AuthExpired, AuthInvalid, AuthRequired
from app.config import settings
from app.csrf import CSRFMiddleware
from app.database import init_db
from app.middlewares import MaintenanceMiddleware, SecurityHeadersMiddleware
from app.routers import (
    api_v1_router,
    booking_router,
    client_auth_router,
    client_dashboard_router,
    public_router,
    staff_auth_router,
    staff_booking_router,
    staff_dashboard_router,
)
from app.templating import templates

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="NEWTOWT ERP and customer booking platform.",
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url="/redoc" if settings.app_env != "production" else None,
        openapi_url="/openapi.json" if settings.app_env != "production" else None,
    )

    # ------------------------------------------------------------------ Static
    app.mount(
        "/static",
        StaticFiles(directory=str((__import__("pathlib").Path(__file__).parent / "static"))),
        name="static",
    )

    # ------------------------------------------------------------- Middlewares
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.site_url],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=["*", "HX-Request", "HX-Trigger", "HX-Target", "HX-Current-URL"],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(MaintenanceMiddleware)
    app.add_middleware(CSRFMiddleware)

    # --------------------------------------------------------------- Routers
    app.include_router(public_router.router)
    app.include_router(api_v1_router.router)
    app.include_router(staff_auth_router.router)
    app.include_router(staff_dashboard_router.router)
    app.include_router(staff_booking_router.router)
    app.include_router(client_auth_router.router)
    app.include_router(client_dashboard_router.router)
    app.include_router(booking_router.router)

    # ------------------------------------------------------------ Health/meta
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "env": settings.app_env}

    @app.get("/.well-known/security.txt", response_class=PlainTextResponse)
    async def security_txt() -> str:
        return (
            "Contact: mailto:communication@towt.eu\n"
            "Expires: 2026-12-31T23:59:59.000Z\n"
            "Preferred-Languages: fr, en\n"
            f"Canonical: {settings.site_url}/.well-known/security.txt\n"
            f"Policy: {settings.site_url}/about/legal\n"
        )

    # ------------------------------------------------------- Exception handlers
    @app.exception_handler(AuthRequired)
    async def _auth_required_handler(request: Request, exc: AuthRequired):
        # Decide between client and staff redirect based on URL prefix
        target = "/me/login" if request.url.path.startswith("/me") else "/login"
        if request.headers.get("hx-request"):
            return _hx_redirect(target)
        return RedirectResponse(url=target, status_code=303)

    @app.exception_handler(AuthExpired)
    async def _auth_expired_handler(request: Request, exc: AuthExpired):
        target = "/me/login" if request.url.path.startswith("/me") else "/login"
        return RedirectResponse(url=target, status_code=303)

    @app.exception_handler(AuthInvalid)
    async def _auth_invalid_handler(request: Request, exc: AuthInvalid):
        target = "/me/login" if request.url.path.startswith("/me") else "/login"
        return RedirectResponse(url=target, status_code=303)

    @app.exception_handler(404)
    async def _not_found(request: Request, exc: HTTPException) -> HTMLResponse | JSONResponse:
        if request.headers.get("accept", "").startswith("application/json"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        try:
            return templates.TemplateResponse(
                "errors/404.html", {"request": request}, status_code=404
            )
        except Exception:
            return PlainTextResponse("404 — Page non trouvée", status_code=404)

    @app.exception_handler(403)
    async def _forbidden(request: Request, exc: HTTPException) -> HTMLResponse | JSONResponse:
        if request.headers.get("accept", "").startswith("application/json"):
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
        try:
            return templates.TemplateResponse(
                "errors/403.html", {"request": request}, status_code=403
            )
        except Exception:
            return PlainTextResponse("403 — Accès refusé", status_code=403)

    # ----------------------------------------------------------- Lifecycle
    @app.on_event("startup")
    async def _on_startup() -> None:
        from app.config import enforce_production_safety
        enforce_production_safety()
        await init_db()
        logger.info("mynewtowt %s started (env=%s)", __version__, settings.app_env)

    return app


def _hx_redirect(target: str):
    from fastapi.responses import Response

    return Response(status_code=200, headers={"HX-Redirect": target})


app = create_app()
