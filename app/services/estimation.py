"""Estimation tarifaire — libre-service client, et demande publique qualifiée.

Deux parcours, délibérément asymétriques :

**Extranet client.** Un client authentifié demande une estimation sur **ses**
grilles actives : il choisit une route où il a une grille valide à l'ETD et
saisit un volume, le prix s'affiche immédiatement. L'estimation est notifiée à
son commercial attitré, qui peut la transformer en offre.

**Demande publique.** Un visiteur sans compte dépose une demande depuis la
vitrine. **Aucun prix ne lui est affiché** : il n'a pas de grille négociée, et
publier un tarif avant qualification exposerait la politique tarifaire à
n'importe qui — y compris à un concurrent. La demande crée une **fiche
prospect**, le commercial ouvre un extranet si l'affaire se qualifie, puis lui
propose une offre validée. La tarification s'appuie alors sur la grille standard
de la route.

Cette asymétrie est le point de sécurité du module : le tarif négocié ne sort
jamais vers quelqu'un dont l'identité n'a pas été établie par un opérateur.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.client_account import ClientAccount
from app.models.commercial import Client, RateGrid, RateGridLine
from app.models.leg import Leg
from app.models.port import Port
from app.models.quote import Quote


class EstimationError(Exception):
    """Demande d'estimation refusée (route hors grille, volume invalide…)."""


async def routes_for_client(
    db: AsyncSession, commercial_client_id: int | None, *, on_date: date | None = None
) -> list[dict]:
    """Routes sur lesquelles le client a une grille **active et valide**.

    C'est le catalogue proposé au client dans l'extranet : il ne peut estimer
    que là où un tarif a été négocié pour lui. Retourne des dictionnaires prêts
    à afficher (LOCODE, noms de ports, référence de grille).

    Sans client rattaché, la liste est **vide** : mieux vaut un écran qui dit
    « aucune grille » et renvoie vers le commercial qu'un tarif générique
    présenté comme le sien.
    """
    if commercial_client_id is None:
        return []
    today = on_date or datetime.now(UTC).date()

    rows = (
        await db.execute(
            select(RateGridLine, RateGrid)
            .join(RateGrid, RateGrid.id == RateGridLine.grid_id)
            .where(
                RateGrid.client_id == commercial_client_id,
                RateGrid.status == "active",
                RateGrid.valid_from <= today,
                or_(RateGrid.valid_to.is_(None), RateGrid.valid_to >= today),
            )
            .order_by(RateGridLine.is_route_default.desc(), RateGrid.valid_from.desc())
        )
    ).all()

    ports = {
        p.locode: p
        for p in (await db.execute(select(Port))).scalars().all()
    }

    seen: set[tuple[str, str]] = set()
    routes: list[dict] = []
    for line, grid in rows:
        key = (line.pol_locode, line.pod_locode)
        if key in seen:
            # Plusieurs grilles peuvent couvrir la même route : la première
            # rencontrée est celle que la résolution retiendra (défaut de route,
            # sinon la plus récente). On ne propose donc pas de doublon.
            continue
        seen.add(key)
        pol, pod = ports.get(line.pol_locode), ports.get(line.pod_locode)
        routes.append(
            {
                "pol_locode": line.pol_locode,
                "pod_locode": line.pod_locode,
                "pol_name": pol.name if pol else line.pol_locode,
                "pod_name": pod.name if pod else line.pod_locode,
                "pol_country": pol.country if pol else None,
                "pod_country": pod.country if pod else None,
                "grid_reference": grid.reference,
                "tariff_reference": line.tariff_reference,
                "valid_to": grid.valid_to,
            }
        )
    return routes


async def upcoming_legs_for_route(
    db: AsyncSession, pol_locode: str, pod_locode: str, *, limit: int = 12
) -> list[Leg]:
    """Voyages à venir sur une route — l'ETD détermine la grille applicable."""
    pol = (
        await db.execute(select(Port).where(Port.locode == pol_locode))
    ).scalar_one_or_none()
    pod = (
        await db.execute(select(Port).where(Port.locode == pod_locode))
    ).scalar_one_or_none()
    if pol is None or pod is None:
        return []
    now = datetime.now(UTC)
    return list(
        (
            await db.execute(
                select(Leg)
                .where(
                    Leg.departure_port_id == pol.id,
                    Leg.arrival_port_id == pod.id,
                    Leg.atd.is_(None),
                    or_(Leg.etd.is_(None), Leg.etd >= now),
                )
                .order_by(Leg.etd.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def assert_route_is_covered(
    db: AsyncSession,
    *,
    commercial_client_id: int | None,
    pol_locode: str,
    pod_locode: str,
    on_date: date | None = None,
) -> None:
    """Refuse une estimation hors des grilles actives du client.

    Sans ce contrôle, la résolution de grille retomberait silencieusement sur la
    grille par défaut : le client verrait un tarif public en croyant lire le sien.
    """
    routes = await routes_for_client(db, commercial_client_id, on_date=on_date)
    if not any(
        r["pol_locode"] == pol_locode and r["pod_locode"] == pod_locode for r in routes
    ):
        raise EstimationError(
            "Aucune grille tarifaire active ne couvre cette route pour votre compte. "
            "Contactez votre commercial pour la mettre en place."
        )


async def ensure_prospect(
    db: AsyncSession,
    *,
    company: str | None,
    contact_name: str | None,
    email: str | None,
    country: str | None = None,
    source: str = "estimation_publique",
) -> Client | None:
    """Fiche prospect créée depuis une demande publique (idempotente par e-mail).

    Sans e-mail on ne crée rien : une fiche sans moyen de recontact n'a aucune
    valeur commerciale et polluerait le référentiel. Un client déjà connu est
    retourné tel quel — une nouvelle demande ne le rétrograde pas en prospect.
    """
    clean_email = (email or "").strip().lower()
    if not clean_email:
        return None

    from sqlalchemy import func

    existing = (
        await db.execute(
            select(Client).where(func.lower(Client.contact_email) == clean_email).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    prospect = Client(
        name=(company or "").strip() or (contact_name or "").strip() or clean_email,
        # Type par défaut : le visiteur n'a pas déclaré s'il est transitaire ou
        # chargeur, et deviner fausserait la grille de brackets proposée plus
        # tard. Le commercial tranche à la qualification.
        client_type="shipper",
        contact_name=(contact_name or "").strip() or None,
        contact_email=clean_email,
        country=((country or "").strip().upper()[:2] or None),
        is_prospect=True,
        prospect_source=source,
        is_active=True,
    )
    db.add(prospect)
    await db.flush()
    return prospect


async def notify_assigned_salesperson(db: AsyncSession, quote: Quote) -> None:
    """Prévient le commercial attitré du client qu'une estimation a été demandée.

    Ciblage **nominatif** quand le client a un commercial attitré ; à défaut,
    diffusion au rôle ``commercial`` — sans quoi une estimation déposée par un
    client sans référent ne serait vue de personne.
    """
    from app.services import notifications

    assigned_id: int | None = None
    client_name = quote.contact_company or quote.contact_name or "Client"
    if quote.commercial_client_id is not None:
        client = await db.get(Client, quote.commercial_client_id)
        if client is not None:
            assigned_id = client.assigned_user_id
            client_name = client.name

    route = f"{quote.pol_locode} → {quote.pod_locode}"
    if quote.origin == "public_request":
        title = "Nouvelle demande d'estimation"
        body = f"{client_name} — {route}, {quote.palettes_total} palette(s). Prospect à qualifier."
    else:
        title = "Nouvelle estimation tarifaire"
        body = f"{client_name} — {route}, {quote.palettes_total} palette(s)."

    await notifications.create(
        db,
        type_="estimate_ready",
        title=title,
        body=body,
        link=f"/commercial/estimations/{quote.reference}",
        target_user_id=assigned_id,
        target_role=None if assigned_id else "commercial",
    )


async def convert_to_offer(
    db: AsyncSession,
    quote: Quote,
    *,
    leg_id: int,
    title: str | None = None,
    actor_id: int | None = None,
    actor_name: str | None = None,
    actor_role: str | None = None,
):
    """Transforme une estimation en offre commerciale (1 client × 1 grille × 1 leg).

    Le prix n'est **pas recopié aveuglément** : il est recalculé sur la grille
    applicable à l'ETD du voyage retenu. Une estimation datée de plusieurs
    semaines peut porter un tarif qui n'a plus cours ; reprendre son montant tel
    quel produirait une offre au prix d'hier, indéfendable si la grille a changé.
    """
    from app.models.commercial import RateOffer
    from app.services.commercial import bracket_rate, next_offer_reference, pick_bracket
    from app.services.offer_history import record_revision
    from app.services.quoting import _match_route, resolve_grid

    if quote.commercial_client_id is None:
        raise EstimationError(
            "Cette estimation n'est rattachée à aucun client — qualifiez d'abord le prospect."
        )
    if quote.converted_offer_id is not None:
        raise EstimationError(
            f"Estimation {quote.reference} déjà transformée en offre."
        )

    leg = await db.get(Leg, leg_id)
    if leg is None:
        raise EstimationError("Voyage introuvable.")

    on_date = (leg.etd.date() if leg.etd else None) or datetime.now(UTC).date()
    grid, route = await resolve_grid(
        db,
        pol_locode=quote.pol_locode,
        pod_locode=quote.pod_locode,
        on_date=on_date,
        commercial_client_id=quote.commercial_client_id,
    )
    matched = _match_route(grid, quote.pol_locode, quote.pod_locode) or route

    palettes = quote.palettes_total or 0
    proposed_rate = None
    total = None
    picked = pick_bracket(grid.brackets, palettes) if palettes > 0 else None
    if picked is not None:
        proposed_rate = bracket_rate(
            base_rate=matched.base_rate,
            coeff=picked["coeff"],
            adjustment_index=grid.adjustment_index,
        )
        total = proposed_rate * palettes

    offer = RateOffer(
        reference=await next_offer_reference(db),
        client_id=quote.commercial_client_id,
        grid_id=grid.id,
        leg_id=leg.id,
        title=(title or "").strip()
        or f"Estimation {quote.reference} — {quote.pol_locode}→{quote.pod_locode}",
        status="en_cours",
        estimated_palettes=palettes,
        proposed_rate_eur=proposed_rate,
        total_eur=total,
    )
    db.add(offer)
    await db.flush()

    quote.converted_offer_id = offer.id
    quote.status = "accepted"
    await db.flush()

    await record_revision(
        db,
        offer,
        action="created",
        actor_id=actor_id,
        actor_name=actor_name,
        actor_role=actor_role,
        comment=f"issue de l'estimation {quote.reference}",
    )
    return offer


async def list_for_client_account(
    db: AsyncSession, client_account: ClientAccount, *, limit: int = 50
) -> list[Quote]:
    """Estimations du compte client — scope strict, jamais un identifiant en entrée."""
    return list(
        (
            await db.execute(
                select(Quote)
                .options(selectinload(Quote.lines))
                .where(Quote.client_account_id == client_account.id)
                .order_by(Quote.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
