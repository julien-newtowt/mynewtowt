"""ERP module scaffolds.

These modules have routes in the sidebar but the full backend hasn't
landed yet. Each renders a single landing page that explains what the
module will provide and links to the matching spec in docs/. This keeps
the sidebar from leading to 404s while we iterate.

Each module enforces the proper permission scope from app.permissions.
Replacing a scaffold with a real implementation is a one-file-per-module
operation.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.permissions import require_permission
from app.templating import templates

router = APIRouter(tags=["erp-scaffolds"])


@dataclass(frozen=True)
class ModuleStub:
    slug: str
    title: str
    kicker: str
    description: str
    backlog: list[str]
    spec_doc: str | None = None


_MODULES: dict[str, ModuleStub] = {
    "escale": ModuleStub(
        slug="escale",
        title="Escale — Operations portuaires",
        kicker="Opérations",
        description=(
            "Suivi d'escale par direction Import / Export, docker shifts, "
            "opérations parallèles (presse, douane, technique, armement), "
            "checklist documents portuaires."
        ),
        backlog=[
            "Vue split Import / Export par leg",
            "Création opérations (NOR, EOSP/SOSP, pilot on/off, gangway)",
            "Docker shifts avec timings réels (planned/actual)",
            "Lien tickets escale automatique sur l'escale",
            "Génération SOF (Statement of Facts) PDF",
        ],
        spec_doc="docs/architecture/01-architecture.md",
    ),
    "onboard": ModuleStub(
        slug="onboard",
        title="Onboard — Vue commandant",
        kicker="À bord",
        description=(
            "Refonte 4 espaces (Escale / Navigation / Cargo / Crew), "
            "service worker PWA installable, mode offline pour la passerelle, "
            "noon report, journal de quart, check-lists ISM/ISPS, registre visiteurs."
        ),
        backlog=[
            "Landing 4 tuiles tap-friendly",
            "NoonReport CRUD + chart vent/SOG",
            "WatchLog par période de quart",
            "OnboardChecklist (drill incendie, ISPS, FSC)",
            "VisitorLog (registre visiteurs ISPS)",
            "Service worker offline + IndexedDB sync",
            "Météo Windy intégrée",
        ],
        spec_doc="Versions TOWT/docs/captain/onboard-v2-spec.md",
    ),
    "crew": ModuleStub(
        slug="crew",
        title="Crew — Équipage",
        kicker="Ressources",
        description=(
            "Gestion des membres d'équipage : rôles, rotations, embarquements, "
            "certifications, compliance Schengen, billets transport."
        ),
        backlog=[
            "Membres équipage (passport, MMSI, certifs)",
            "Crew assignments par leg (rotation)",
            "Crew tickets transport (vol/train)",
            "Liste PAF imprimable par escale",
            "Alertes expiration brevets",
        ],
        spec_doc="docs/personas/01-personas.md",
    ),
    "rh": ModuleStub(
        slug="rh",
        title="RH — Ressources humaines",
        kicker="Humain",
        description=(
            "Suivi RH étendu : congés payés, absences, planning annuel, "
            "compliance Schengen marins étrangers, calendrier embarquement."
        ),
        backlog=[
            "Congés payés / RTT (CRUD)",
            "Absences (maladie, congé maternité, etc.)",
            "Calendrier annuel par marin",
            "Alertes Schengen (90/180 jours)",
            "Export payroll variable",
        ],
        spec_doc="docs/personas/01-personas.md",
    ),
    "finance": ModuleStub(
        slug="finance",
        title="Finance — Marge & OPEX",
        kicker="Performance",
        description=(
            "Reporting financier par leg : LegFinance (revenue / costs / margin), "
            "paramètres OPEX flotte, port configs, encours factures, "
            "couverture assurance."
        ),
        backlog=[
            "LegFinance par leg (revenue, port fees, OPEX, margin)",
            "OPEX parameters CRUD (€/jour mer, €/jour port)",
            "Port configs (taxes portuaires, agency fees)",
            "Suivi encours factures (issued/paid/overdue)",
            "Insurance contracts (P&I, Hull, War Risk)",
        ],
    ),
    "kpi": ModuleStub(
        slug="kpi",
        title="KPI — Indicateurs flotte",
        kicker="Performance",
        description=(
            "KPIs cross-flotte : tonnage transporté, on-time performance, "
            "utilisation navire, certificats CO₂ émis."
        ),
        backlog=[
            "Tonnage cumulé / mois / navire / route",
            "On-time performance (% legs livrés < ETA + 24h)",
            "Utilisation navire (% palettes vs capacité)",
            "CO₂ évité par client / cumul annuel",
        ],
    ),
    "mrv": ModuleStub(
        slug="mrv",
        title="MRV — Reporting EU 2015/757",
        kicker="Conformité",
        description=(
            "Reporting réglementaire émissions maritimes UE. Format DNV CSV "
            "annuel pour vérificateur tiers, paramètres MDO / facteurs émission."
        ),
        backlog=[
            "MRV events (fuel ROB, consumption, distance)",
            "Variables CO₂ paramétrables (default 1.5 vs 13.7 g/tkm)",
            "Variables MRV (densité MDO 0.845, facteur 3.206)",
            "Export DNV CSV annuel",
            "Carbon Report PDF (vérificateur)",
        ],
    ),
    "claims": ModuleStub(
        slug="claims",
        title="Claims — Sinistres",
        kicker="Risque",
        description=(
            "Suivi des sinistres : cargo, équipage, hull/DIV, war risk. "
            "Provisions, timeline, documents, lien assureur."
        ),
        backlog=[
            "Claim CRUD (type cargo/crew/hull, provision)",
            "Timeline d'événements + commentaires",
            "Documents joints (expertises, factures)",
            "Position cargo auto via plan d'arrimage",
            "Export dossier complet PDF assureur",
        ],
    ),
    "tracking": ModuleStub(
        slug="tracking",
        title="Tracking — Position flotte",
        kicker="Pilotage",
        description=(
            "Position GPS en quasi-temps réel des 4 navires sur carte mondiale. "
            "Ingestion via API X-API-Token, trace par leg, ETA projetée."
        ),
        backlog=[
            "Carte mondiale 4 navires (refresh 5 min)",
            "POST /api/tracking/upload (token)",
            "Trace par leg avec waypoints noon report",
            "ETA recalculée depuis vitesse + distance",
            "Conflits port visibles (déjà sur planning)",
        ],
    ),
    "analytics": ModuleStub(
        slug="analytics",
        title="Analytics — Dashboards décisionnels",
        kicker="Performance",
        description=(
            "Dashboards exécutif, commercial, opérations, finance, MRV, RH, "
            "et client. Variance vs plan / N-1 / cohorte. Filtres avancés, "
            "export CSV / Excel / PDF."
        ),
        backlog=[
            "Dashboard /dashboard/exec (KPIs flotte)",
            "/dashboard/sales (funnel booking)",
            "/dashboard/ops (escales, tickets, SLA)",
            "/dashboard/finance (variance)",
            "/dashboard/mrv (CO₂ cumul)",
            "Export CSV / PDF filtré",
            "Sauvegarde filtres par user",
        ],
        spec_doc="docs/analytics/01-data-strategy.md",
    ),
    "admin": ModuleStub(
        slug="admin",
        title="Admin — Configuration",
        kicker="Gouvernance",
        description=(
            "Gouvernance globale : utilisateurs, navires, ports, feature flags, "
            "audit logs, exports/purges DB, paramètres globaux, mode maintenance."
        ),
        backlog=[
            "Users CRUD + import CSV",
            "Vessels CRUD",
            "Ports admin (liste, source filter)",
            "Feature flags admin",
            "Activity logs filtrable",
            "Database exports/purges sélectives",
            "Maintenance mode toggle",
            "Pipedrive sync test",
        ],
    ),
}


def _make_route(slug: str, perm: str = "C"):
    """Build an endpoint that renders the scaffold page for one module."""
    module = _MODULES[slug]

    # Special-case module name for permissions
    perm_module = {
        "onboard": "captain",
        "rh": "rh",
    }.get(slug, slug)

    async def _endpoint(
        request: Request,
        user=Depends(require_permission(perm_module, perm)),
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            "staff/erp_scaffold.html",
            {"request": request, "user": user, "module": module},
        )

    return _endpoint


# Wire each module as a single GET route.
for slug in _MODULES:
    router.add_api_route(
        f"/{slug}",
        _make_route(slug, "C"),
        methods=["GET"],
        response_class=HTMLResponse,
        name=f"erp_{slug}",
    )

# Analytics sub-route used by the sidebar link `/dashboard/analytics`.
router.add_api_route(
    "/dashboard/analytics",
    _make_route("analytics", "C"),
    methods=["GET"],
    response_class=HTMLResponse,
    name="dashboard_analytics",
)
