"""Réponse standard d'une mutation « Phase 1 » (204 + HX-Trigger / 303 repli).

Motif posé en Phase 1 par le cockpit escale (``escale_router._mutation_response``,
docs/design/03-reprise-ux-legacy.md) puis recopié à l'identique par quatre autres
routeurs (``cargo_packing_router``, ``claims_router``, ``client_dashboard_router``,
``cargo_router``) au fil des Phases 2-3. Ce module en fait l'implémentation
unique : sous ``hx-request``, on ne recharge plus toute la page — 204 sans corps
+ header ``HX-Trigger`` qui (a) affiche un toast (``toast.js``, écouteur
``htmx:afterRequest``) et (b) déclenche un événement de rafraîchissement
applicatif (ex. ``escaleRefresh``, ``cargoRefresh``, ``claimsRefresh``,
``meRefresh``), écouté par un conteneur de la page qui se re-remplit via
``hx-get`` + ``hx-select`` sur elle-même. Sans JS (pas de header ``hx-request``) :
redirect 303 classique vers ``redirect_url``, inchangé.

Les routeurs appelants gardent chacun un wrapper local d'une ligne (même nom,
même signature qu'avant ce lot) pour limiter le diff et ne pas re-toucher tous
les call sites ; ce module ne porte que la logique commune.
"""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response


def mutation_response(
    request: Request,
    *,
    redirect_url: str,
    message: str,
    refresh_event: str,
    toast_type: str = "success",
) -> Response:
    """204 + ``HX-Trigger`` {toast, <refresh_event>: true} sous HTMX, 303 sinon."""
    if request.headers.get("hx-request"):
        return Response(
            status_code=204,
            headers={
                "HX-Trigger": json.dumps(
                    {
                        "toast": {"message": message, "type": toast_type},
                        refresh_event: True,
                    }
                )
            },
        )
    return RedirectResponse(url=redirect_url, status_code=303)
