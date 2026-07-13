"""Soutage (Bunker Report / BDN) — logique métier (MRV LOT 6).

Trois responsabilités :

1. **Cycle de vie** d'un :class:`~app.models.bunker.BunkerOperation` —
   ``create_draft`` / ``update_draft`` (garde auteur-seul tant que le
   brouillon n'est pas validé Master) / ``validate_master`` (bord) /
   ``apply_review_correction`` (siège — correction possible même après
   validation Master, toujours tracée par l'appelant via
   ``services.activity``).
2. **Rattachement voyage automatique** (``resolve_leg_for_bunker``) : le leg
   du navire dont l'ETD/ATD (ATD si connu, sinon ETD) est le premier
   *après* la date de livraison, dans une fenêtre paramétrable
   (seuil ``R24:fenetre_rattachement_bunker_j``, résolu via
   ``services.validation_engine.get_threshold`` — défaut 25 j, provisoire).
   Hors fenêtre (ou aucun voyage trouvé) → ``None`` (choix manuel possible
   côté écran).
3. **Contrôles structurels** — méthodes de service consommant les seuils
   existants (R16, R23) : cohérence masse vs Σ(volume×densité) des cuves,
   densité BDN hors plage, volumes vs capacités cuves (Info seulement,
   cf. Q11). Ce lot ne code **aucune** nouvelle règle dans le registre
   ``validation_engine.RULES`` — ces contrôles ne sont pas persistés en
   ``QualityCheckResult`` (réservé au lot 8, quand ces contrôles seront
   formalisés en règles du registre).

Convention d'unités (plan §2.7) : masses en tonnes, volumes en m³, densité en
t/m³ (≡ kg/L).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bunker import DEFAULT_FUEL_TYPE, BunkerOperation, BunkerTankAllocation
from app.models.leg import Leg
from app.models.vessel import Vessel
from app.models.vessel_env import VesselTank
from app.services import referential_env
from app.services.validation_engine import get_threshold

# ════════════════════════════════════════════════════════════════ Exceptions


class BunkerError(Exception):
    """Erreur métier soutage — le routeur la traduit en réponse HTTP propre."""


class DuplicateBdnError(BunkerError):
    """Le numéro de BDN existe déjà (contrainte DB + garde applicative)."""


class AuthorOnlyError(BunkerError):
    """Un brouillon n'est modifiable que par son auteur (ou tant qu'aucun
    auteur n'est connu — compatibilité anciennes lignes)."""


class BunkerFormError(BunkerError):
    """Champ de formulaire BDN présent mais invalide (→ 400 côté routeur)."""


# ═══════════════════════════════════════════════════════════════ Dataclasses


@dataclass(frozen=True)
class AllocationInput:
    """Ligne d'allocation par cuve — entrée typée de ``set_allocations``."""

    tank_id: int
    volume_m3: Decimal
    density_t_m3: Decimal


@dataclass(frozen=True)
class MassConsistencyCheck:
    """R23 (volet masse) — masse déclarée vs Σ(volume × densité) des cuves.

    Statut à 3 paliers dérivé d'un seul seuil paramétrable
    (``tolerance_bdn_flgo_t``, R23) : ``ok`` ≤ tolérance ; ``ecart_mineur``
    ≤ 2×tolérance ; ``ecart_majeur`` au-delà. Le multiplicateur ×2 est un
    choix d'implémentation raisonnable en l'absence de 2 seuils distincts
    dans le catalogue actuel — recalibrable au lot 8 (règles complètes) sans
    changer cette fonction, seulement le paramètre.
    """

    status: str  # "ok" | "ecart_mineur" | "ecart_majeur"
    declared_mass_t: Decimal
    allocated_mass_t: Decimal
    delta_t: Decimal
    tolerance_t: Decimal


@dataclass(frozen=True)
class DensityCheck:
    """R16 — densité BDN hors plage [défaut − tolérance, défaut + tolérance]."""

    flagged: bool
    density_t_m3: Decimal | None
    default_t_m3: Decimal
    tolerance_t_m3: Decimal
    low: Decimal
    high: Decimal


@dataclass(frozen=True)
class CapacityCheck:
    """Σ volumes vs Σ capacités cuves — **Info seulement**, jamais bloquant
    (capacités officielles indisponibles, Q11 ; cf. ``VesselTank.capacity_m3``
    souvent NULL)."""

    total_volume_m3: Decimal
    total_capacity_m3: Decimal | None
    exceeds: bool


@dataclass(frozen=True)
class BunkerChecks:
    """Regroupe les 3 contrôles structurels pour un affichage écran unique."""

    mass: MassConsistencyCheck
    density: DensityCheck
    capacity: CapacityCheck


# ════════════════════════════════════════════════════════ Seuils par défaut
# Fail-closed ultime si ``get_threshold`` renvoie ``None`` (paramètre inconnu
# du catalogue ET absent des défauts codés — ne devrait jamais arriver pour
# ces 3 seuils, tous seedés au lot 2/6).

_DEFAULT_TOLERANCE_BDN_FLGO_T = Decimal("2")
_DEFAULT_DENSITE_T_M3 = Decimal("0.845")
_DEFAULT_DENSITE_TOLERANCE_T_M3 = Decimal("0.015")
_DEFAULT_FENETRE_RATTACHEMENT_J = Decimal("25")


def _ensure_utc(value: datetime) -> datetime:
    """Normalise un datetime naïf en UTC (saisie bord supposée UTC, cf.
    ``onboard_router._maybe_dt``) — ne change rien si déjà timezone-aware."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def parse_decimal(raw: str | None, field: str, *, required: bool = False) -> Decimal | None:
    """Parse une chaîne de formulaire en ``Decimal`` — vocabulaire commun aux
    2 écrans (bord/siège) et aux fonctions de ce module.

    Vide + non obligatoire → ``None``. Vide + obligatoire ou valeur non vide
    mais invalide → :class:`BunkerFormError` (400 côté routeur). Accepte la
    virgule décimale (saisie FR).
    """
    cleaned = (raw or "").strip()
    if not cleaned:
        if required:
            raise BunkerFormError(f"{field} est obligatoire.")
        return None
    try:
        return Decimal(cleaned.replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise BunkerFormError(f"Valeur invalide pour {field} : {raw!r}") from exc


# ══════════════════════════════════════════════════ Rattachement voyage auto


async def resolve_leg_for_bunker(
    db: AsyncSession, vessel: Vessel, delivery_dt: datetime
) -> int | None:
    """Rattache un soutage au voyage qui suit l'escale de livraison.

    Le leg du navire dont la référence de départ (ATD si connu, sinon ETD)
    est la **première après** ``delivery_dt``, à condition que l'écart reste
    dans la fenêtre ``fenetre_rattachement_bunker_j`` (seuil R24, override
    par navire possible). Hors fenêtre, ou aucun leg trouvé → ``None`` (état
    normal — choix manuel possible côté écran).
    """
    delivery_dt = _ensure_utc(delivery_dt)
    effective_date = func.coalesce(Leg.atd, Leg.etd)
    stmt = (
        select(Leg)
        .where(Leg.vessel_id == vessel.id)
        .where(Leg.status != "cancelled")
        .where(effective_date > delivery_dt)
        .order_by(effective_date.asc())
        .limit(1)
    )
    candidate = (await db.execute(stmt)).scalar_one_or_none()
    if candidate is None:
        return None

    ref_date = candidate.atd or candidate.etd
    ref_date = _ensure_utc(ref_date)
    tv = await get_threshold(db, "R24", "fenetre_rattachement_bunker_j", vessel_id=vessel.id)
    window_j = tv.value if tv is not None else _DEFAULT_FENETRE_RATTACHEMENT_J
    if (ref_date - delivery_dt) > timedelta(days=float(window_j)):
        return None
    return candidate.id


# ═══════════════════════════════════════════════════════════════ Allocations


async def vessel_tanks_by_id(db: AsyncSession, vessel_id: int) -> dict[int, VesselTank]:
    """Cuves d'un navire, indexées par id (pour affichage / contrôle capacité)."""
    tanks = await referential_env.get_vessel_tanks(db, vessel_id)
    return {t.id: t for t in tanks}


def parse_allocation_rows(
    form: Mapping[str, str],
    tanks: Sequence[VesselTank],
    default_density: Decimal | None = None,
) -> list[AllocationInput]:
    """Lit les lignes d'allocation d'un formulaire — une ligne par cuve du navire.

    Champs attendus par cuve : ``volume_m3__{tank_id}`` (obligatoire pour
    que la ligne compte) et ``density_t_m3__{tank_id}`` (optionnel — retombe
    sur ``default_density``, typiquement la densité globale du BDN). Une
    cuve sans volume saisi est simplement omise (le soutage n'a pas
    forcément touché toutes les cuves du navire).
    """
    rows: list[AllocationInput] = []
    for tank in tanks:
        raw_vol = (form.get(f"volume_m3__{tank.id}") or "").strip()
        if not raw_vol:
            continue
        volume = parse_decimal(raw_vol, f"volume ({tank.tank_code})", required=True)
        density = parse_decimal(form.get(f"density_t_m3__{tank.id}"), f"densité ({tank.tank_code})")
        if density is None:
            density = default_density if default_density is not None else _DEFAULT_DENSITE_T_M3
        rows.append(AllocationInput(tank_id=tank.id, volume_m3=volume, density_t_m3=density))
    return rows


async def set_allocations(
    db: AsyncSession, bunker: BunkerOperation, rows: Sequence[AllocationInput]
) -> list[BunkerTankAllocation]:
    """Remplace les lignes d'allocation d'un soutage (upsert par cuve + purge).

    Valide que chaque ``tank_id`` appartient bien au navire du soutage
    (défense en profondeur — le formulaire ne devrait proposer que les cuves
    du navire) et l'unicité (une cuve ne peut être allouée deux fois pour le
    même soutage, cf. ``UNIQUE(bunker_id, tank_id)``).
    """
    seen: set[int] = set()
    for row in rows:
        if row.tank_id in seen:
            raise BunkerError(f"La cuve {row.tank_id} est allouée plusieurs fois pour ce soutage.")
        seen.add(row.tank_id)

    if seen:
        valid_tank_ids = set((await vessel_tanks_by_id(db, bunker.vessel_id)).keys())
        unknown = seen - valid_tank_ids
        if unknown:
            raise BunkerError(
                f"Cuve(s) {sorted(unknown)} n'appartenant pas au navire de ce soutage."
            )

    existing = list(
        (
            await db.execute(
                select(BunkerTankAllocation).where(BunkerTankAllocation.bunker_id == bunker.id)
            )
        )
        .scalars()
        .all()
    )
    by_tank = {a.tank_id: a for a in existing}
    kept_ids: set[int] = set()
    result: list[BunkerTankAllocation] = []
    for row in rows:
        alloc = by_tank.get(row.tank_id)
        if alloc is None:
            alloc = BunkerTankAllocation(bunker_id=bunker.id, tank_id=row.tank_id)
            db.add(alloc)
        alloc.volume_m3 = row.volume_m3
        alloc.density_t_m3 = row.density_t_m3
        result.append(alloc)
        kept_ids.add(row.tank_id)
    for alloc in existing:
        if alloc.tank_id not in kept_ids:
            await db.delete(alloc)
    await db.flush()
    return result


# ═══════════════════════════════════════════════════════ Formulaire d'en-tête

# Champs texte / décimaux modifiables de l'en-tête BDN — communs aux écrans
# bord (brouillon, auteur seul) et siège (correction post-validation, tracée
# par l'appelant). ``bdn_number`` est volontairement absent : un numéro de
# BDN est le numéro du document légal, il ne se corrige pas en place (créer
# un nouveau soutage si erreur de saisie avant toute validation).
_STR_FIELDS: tuple[str, ...] = ("fuel_type", "port_locode", "supplier_name")
_DECIMAL_FIELDS: tuple[str, ...] = (
    "mass_t",
    "sulfur_content_pct",
    "density_15c_t_m3",
    "viscosity_cst",
    "water_content_pct",
    "lower_heating_value",
    "higher_heating_value",
    "ef_ttw_co2",
)
# Champs obligatoires : une valeur vide soumise est ignorée (ne vide jamais
# le champ) plutôt que de casser l'intégrité du BDN.
_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"mass_t", "density_15c_t_m3", "port_locode", "fuel_type"}
)


def apply_header_form(bunker: BunkerOperation, form: Mapping[str, str]) -> None:
    """Applique les champs d'en-tête présents dans ``form`` (coercition typée).

    Une valeur vide sur un champ optionnel l'efface (``None``) ; une valeur
    vide sur un champ obligatoire est ignorée (conserve la valeur en base).
    Lève :class:`BunkerFormError` sur une valeur non vide mais invalide — ne
    corrompt jamais silencieusement une donnée réglementaire.
    """
    for f in _STR_FIELDS:
        if f not in form:
            continue
        raw = (form.get(f) or "").strip()
        if not raw:
            if f in _REQUIRED_FIELDS:
                continue
            setattr(bunker, f, None)
            continue
        setattr(bunker, f, raw.upper() if f == "port_locode" else raw)

    for f in _DECIMAL_FIELDS:
        if f not in form:
            continue
        raw = (form.get(f) or "").strip()
        if not raw:
            if f in _REQUIRED_FIELDS:
                continue
            setattr(bunker, f, None)
            continue
        setattr(bunker, f, parse_decimal(raw, f, required=True))

    if "delivery_datetime_utc" in form:
        raw = (form.get("delivery_datetime_utc") or "").strip()
        if raw:
            try:
                bunker.delivery_datetime_utc = datetime.fromisoformat(raw).replace(tzinfo=UTC)
            except ValueError as exc:
                raise BunkerFormError(f"Date de livraison invalide : {raw!r}") from exc


# ═══════════════════════════════════════════════════════════════ Cycle de vie


def _assert_author_and_draft(bunker: BunkerOperation, user_id: int) -> None:
    """Garde applicative D11 : un brouillon n'est modifiable que par son auteur."""
    if bunker.status != "brouillon":
        raise BunkerError("Ce soutage est déjà validé Master — non modifiable côté bord.")
    if bunker.author_user_id is not None and bunker.author_user_id != user_id:
        raise AuthorOnlyError("Seul l'auteur du brouillon peut le modifier.")


async def create_draft(
    db: AsyncSession,
    *,
    vessel: Vessel,
    author_user_id: int,
    bdn_number: str,
    port_locode: str,
    delivery_datetime_utc: datetime,
    mass_t: Decimal,
    density_15c_t_m3: Decimal,
    fuel_type: str = DEFAULT_FUEL_TYPE,
    sulfur_content_pct: Decimal | None = None,
    viscosity_cst: Decimal | None = None,
    water_content_pct: Decimal | None = None,
    lower_heating_value: Decimal | None = None,
    higher_heating_value: Decimal | None = None,
    ef_ttw_co2: Decimal | None = None,
    supplier_name: str | None = None,
    leg_id: int | None = None,
    allocations: Sequence[AllocationInput] | None = None,
) -> BunkerOperation:
    """Crée un brouillon de soutage — rattachement voyage auto sauf override.

    ``leg_id`` fourni = choix manuel (aucune résolution auto) ; ``None`` =
    ``resolve_leg_for_bunker`` décide (peut renvoyer ``None`` lui-même, hors
    fenêtre — état normal). Unicité BDN vérifiée en 2 temps : pré-check
    applicatif (message propre dans le cas courant) + contrainte DB dans un
    SAVEPOINT (``begin_nested``) pour couvrir une éventuelle course
    concurrente sans corrompre la transaction ambiante.
    """
    bdn_clean = (bdn_number or "").strip()
    if not bdn_clean:
        raise BunkerError("Le numéro de BDN est obligatoire.")
    locode_clean = (port_locode or "").strip().upper()
    if len(locode_clean) != 5:
        raise BunkerError("Le LOCODE du port de livraison doit compter 5 caractères.")

    existing = (
        await db.execute(select(BunkerOperation).where(BunkerOperation.bdn_number == bdn_clean))
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateBdnError(f"Un soutage avec le numéro de BDN {bdn_clean!r} existe déjà.")

    delivery_dt = _ensure_utc(delivery_datetime_utc)
    resolved_leg_id = (
        leg_id if leg_id is not None else await resolve_leg_for_bunker(db, vessel, delivery_dt)
    )

    bunker = BunkerOperation(
        leg_id=resolved_leg_id,
        vessel_id=vessel.id,
        bdn_number=bdn_clean,
        port_locode=locode_clean,
        delivery_datetime_utc=delivery_dt,
        fuel_type=(fuel_type or DEFAULT_FUEL_TYPE).strip() or DEFAULT_FUEL_TYPE,
        mass_t=mass_t,
        density_15c_t_m3=density_15c_t_m3,
        sulfur_content_pct=sulfur_content_pct,
        viscosity_cst=viscosity_cst,
        water_content_pct=water_content_pct,
        lower_heating_value=lower_heating_value,
        higher_heating_value=higher_heating_value,
        ef_ttw_co2=ef_ttw_co2,
        supplier_name=(supplier_name or "").strip() or None,
        status="brouillon",
        author_user_id=author_user_id,
    )
    try:
        async with db.begin_nested():
            db.add(bunker)
            await db.flush()
    except IntegrityError as exc:
        raise DuplicateBdnError(
            f"Un soutage avec le numéro de BDN {bdn_clean!r} existe déjà."
        ) from exc

    if allocations:
        await set_allocations(db, bunker, list(allocations))
    return bunker


async def update_draft(
    db: AsyncSession,
    bunker: BunkerOperation,
    *,
    user_id: int,
    form: Mapping[str, str] | None = None,
    allocations: Sequence[AllocationInput] | None = None,
    manual_leg_id: int | None = None,
    auto_leg_vessel: Vessel | None = None,
) -> BunkerOperation:
    """Modifie un brouillon — réservé à l'auteur (``_assert_author_and_draft``).

    ``manual_leg_id`` (choix explicite dans le formulaire) prime sur
    ``auto_leg_vessel`` (déclenche une nouvelle résolution auto, utile quand
    la date de livraison ou le navire changent). Si ni l'un ni l'autre n'est
    fourni, ``leg_id`` n'est pas retouché.
    """
    _assert_author_and_draft(bunker, user_id)
    if form:
        apply_header_form(bunker, form)
    if manual_leg_id is not None:
        bunker.leg_id = manual_leg_id
    elif auto_leg_vessel is not None:
        bunker.leg_id = await resolve_leg_for_bunker(
            db, auto_leg_vessel, bunker.delivery_datetime_utc
        )
    if allocations is not None:
        await set_allocations(db, bunker, allocations)
    await db.flush()
    return bunker


async def validate_master(db: AsyncSession, bunker: BunkerOperation, validator) -> BunkerOperation:
    """Validation Master (bord) — verrouille le soutage côté bord.

    Pas de garde auteur-seul ici (contrairement à ``update_draft``) : la
    validation Master est un acte de commandement, pas réservé à qui a
    initialement saisi le brouillon.
    """
    if bunker.status == "valide_master":
        raise BunkerError("Ce soutage est déjà validé Master.")
    bunker.status = "valide_master"
    bunker.validated_master_at = datetime.now(UTC)
    bunker.validated_master_by = getattr(validator, "id", None)
    await db.flush()

    # LOT 8 — déclencheur qualité : la validation Master exécute les règles
    # scope ``bunker`` (R16/R23/R24) et route les alertes (R24 → admin).
    # Best-effort : un échec du contrôle ne bloque JAMAIS la validation d'un
    # soutage (les règles signalent, elles n'empêchent pas l'acte de bord) ;
    # sans catalogue seedé (tests unitaires, dev nu), no-op propre.
    try:
        from app.services import validation_rules_catalog as _vrc

        await _vrc.run_bunker_rules_and_route(db, bunker)
    except Exception:
        pass
    return bunker


async def apply_review_correction(
    db: AsyncSession,
    bunker: BunkerOperation,
    *,
    form: Mapping[str, str],
    manual_leg_id: int | None = None,
    clear_leg: bool = False,
) -> BunkerOperation:
    """Correction siège (``mrv:M``) — possible même après validation Master.

    Aucune garde auteur/statut (à la différence de ``update_draft``) : c'est
    une fonction de contrôle de second niveau. **L'appelant DOIT tracer**
    l'action via ``services.activity.record`` — cette fonction ne le fait
    pas elle-même (elle ne connaît pas l'identité HTTP de l'appelant).
    """
    apply_header_form(bunker, form)
    if clear_leg:
        bunker.leg_id = None
    elif manual_leg_id is not None:
        bunker.leg_id = manual_leg_id
    await db.flush()
    return bunker


# ═══════════════════════════════════════════════════════ Contrôles structurels


async def check_mass_consistency(
    db: AsyncSession, bunker: BunkerOperation, allocations: Sequence[BunkerTankAllocation]
) -> MassConsistencyCheck:
    """R23 (volet masse) — masse déclarée vs Σ(volume_m3 × density_t_m3)."""
    allocated = sum((a.volume_m3 * a.density_t_m3 for a in allocations), Decimal("0"))
    declared = bunker.mass_t or Decimal("0")
    delta = abs(declared - allocated)
    tv = await get_threshold(db, "R23", "tolerance_bdn_flgo_t", vessel_id=bunker.vessel_id)
    tolerance = tv.value if tv is not None else _DEFAULT_TOLERANCE_BDN_FLGO_T
    if delta <= tolerance:
        status = "ok"
    elif delta <= tolerance * 2:
        status = "ecart_mineur"
    else:
        status = "ecart_majeur"
    return MassConsistencyCheck(
        status=status,
        declared_mass_t=declared,
        allocated_mass_t=allocated,
        delta_t=delta,
        tolerance_t=tolerance,
    )


async def check_density(db: AsyncSession, bunker: BunkerOperation) -> DensityCheck:
    """R16 — densité BDN dans [densite_defaut ± densite_tolerance] (0,845 ± 0,015)."""
    default_tv = await get_threshold(db, "R16", "densite_defaut_t_m3", vessel_id=bunker.vessel_id)
    tol_tv = await get_threshold(db, "R16", "densite_tolerance_t_m3", vessel_id=bunker.vessel_id)
    default = default_tv.value if default_tv is not None else _DEFAULT_DENSITE_T_M3
    tolerance = tol_tv.value if tol_tv is not None else _DEFAULT_DENSITE_TOLERANCE_T_M3
    low, high = default - tolerance, default + tolerance
    density = bunker.density_15c_t_m3
    flagged = density is None or not (low <= density <= high)
    return DensityCheck(
        flagged=flagged,
        density_t_m3=density,
        default_t_m3=default,
        tolerance_t_m3=tolerance,
        low=low,
        high=high,
    )


def check_capacity(
    allocations: Sequence[BunkerTankAllocation], tanks_by_id: dict[int, VesselTank]
) -> CapacityCheck:
    """Σ volumes vs Σ capacités cuves — **Info seulement** (Q11), jamais bloquant."""
    total_volume = sum((a.volume_m3 for a in allocations), Decimal("0"))
    capacities = [
        tanks_by_id[a.tank_id].capacity_m3
        for a in allocations
        if tanks_by_id.get(a.tank_id) is not None and tanks_by_id[a.tank_id].capacity_m3 is not None
    ]
    total_capacity = sum(capacities, Decimal("0")) if capacities else None
    exceeds = total_capacity is not None and total_volume > total_capacity
    return CapacityCheck(
        total_volume_m3=total_volume, total_capacity_m3=total_capacity, exceeds=exceeds
    )


async def evaluate_bunker(
    db: AsyncSession,
    bunker: BunkerOperation,
    allocations: Sequence[BunkerTankAllocation],
    tanks_by_id: dict[int, VesselTank] | None = None,
) -> BunkerChecks:
    """Exécute les 3 contrôles structurels — un seul appel pour les écrans."""
    mass = await check_mass_consistency(db, bunker, allocations)
    density = await check_density(db, bunker)
    capacity = check_capacity(allocations, tanks_by_id or {})
    return BunkerChecks(mass=mass, density=density, capacity=capacity)


# ═══════════════════════════════ Interface d'exposition — grand livre (lots 3/9)


async def bunkered_t_lookup(db: AsyncSession, leg_id: int) -> Decimal:
    """Masse totale soutée rattachée à un voyage — **seuls les soutages
    validés Master** comptent (un brouillon n'est pas une donnée fiable pour
    un calcul réglementaire).

    Interface prévue pour le calcul ROB chaîné du lot 3
    (``inter_event_compute``) et le grand livre d'émissions du lot 9
    (``emission_ledger``) : ces deux lots consomment le soutage d'un voyage
    sans connaître le détail du modèle ``BunkerOperation`` — ils appellent
    cette seule fonction. Volontairement **sans dépendance vers
    ``nav_events``** (hors périmètre de ce lot, développé en parallèle) : la
    fonction ne prend qu'un ``leg_id`` (``legs`` existe déjà) et ne renvoie
    qu'un total agrégé, laissant tout le calcul inter-événements au lot 3.

    Renvoie ``Decimal("0")`` si aucun soutage validé n'est rattaché — jamais
    ``None`` (un total est toujours sommable sans cas particulier côté
    appelant).
    """
    rows = (
        (
            await db.execute(
                select(BunkerOperation.mass_t)
                .where(BunkerOperation.leg_id == leg_id)
                .where(BunkerOperation.status == "valide_master")
            )
        )
        .scalars()
        .all()
    )
    return sum((m for m in rows if m is not None), Decimal("0"))
