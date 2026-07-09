"""FLGO (Marad) — synchronisation LECTURE SEULE + rapprochements (MRV LOT 7).

Réutilise le socle ``app/utils/marad.py`` (même auth/token/base URL que la
sync crew, cf. ``docs/integrations/marad-crew-readonly.md``) pour deux voies
d'alimentation convergentes vers le **même upsert idempotent** :

1. **API Marad en direct** (:func:`sync_flgo_from_api`) — ``GET
   /api/FlgoAction`` (schéma confirmé, cf. ``app.utils.marad.list_flgo``).
   Strictement GET, aucune écriture vers Marad.
2. **Import xlsx de repli** (:func:`import_flgo_xlsx`) — parse les exports
   IHM Marad (« Main sheet », cellules composites ``"14.6 m3 (12.76 t)"``),
   même format que ``Anemos_All*.xlsx`` / ``FLGO {Anemos,Artemis}.xlsx``.

Les deux voies produisent des :class:`~app.models.flgo.FlgoReading` +
:class:`~app.models.flgo.FlgoTankCompartmentVolume` par le même chemin
d'upsert (:func:`_upsert_reading`) — clé naturelle ``(vessel_id,
reading_datetime, action_type, product_name)`` : un re-sync (API ou xlsx) ne
crée jamais de doublon, seulement des mises à jour si les valeurs ont changé.

Rapprochements service-level (R17/R24/R25, sans enregistrer de nouvelle
règle dans ``validation_engine`` — réservé au lot 8, même principe que
``services.bunkering`` au lot 6) : :func:`flgo_nearest_reading`,
:func:`flgo_matches_for_bunker`, :func:`check_internal_consistency`. Ces
fonctions **signalent, ne corrigent jamais** — elles ne modifient ni
``FlgoReading`` ni ``BunkerOperation``.
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import openpyxl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.bunker import BunkerOperation
from app.models.flgo import FlgoReading, FlgoTankCompartmentVolume
from app.models.vessel import Vessel
from app.models.vessel_env import TANK_CODES
from app.services.validation_engine import get_threshold
from app.utils import marad

# ════════════════════════════════════════════════════════════════ Exceptions


class FlgoSyncError(Exception):
    """Erreur métier FLGO — le routeur la traduit en réponse HTTP propre."""


class FlgoCompositeCellError(FlgoSyncError):
    """Cellule composite Marad illisible (``"X m3 (Y t)"`` attendu)."""


# ══════════════════════════════════════════════════════ Dérivation tank_code

# Sous-ensemble numérique de vessel_env.TANK_CODES (exclut le fourre-tout
# "other") — les seuls numéros de compartiment reconnus comme cuve nommée.
_NUMERIC_TANK_CODES: frozenset[str] = frozenset(c for c in TANK_CODES if c != "other")

# Deux formats de libellé de compartiment observés (même 9 compartiments
# physiques, présentation différente par navire, cf. inventaire des exports) :
#   Anemos  : "14 - GO DB B"            (numéro en PRÉFIXE)
#   Artemis : "GO BD B     Ref:14"      (numéro en SUFFIXE "Ref:NN")
_REF_SUFFIX_RE = re.compile(r"ref\s*:?\s*(\d+)", re.IGNORECASE)
_LEADING_NUMBER_RE = re.compile(r"^\s*(\d+)\s*-")


def derive_tank_code(compartment_code: str) -> str:
    """Dérive le ``tank_code`` (14/15/16/17/other) depuis un libellé Marad.

    Jamais saisi à la main : correspondance directe avec
    ``vessel_tanks.tank_code`` par le numéro de compartiment, qu'il soit en
    préfixe (``"14 - GO DB B"``) ou en suffixe ``"Ref:NN"`` (``"GO BD B
    Ref:14"``). Un compartiment sans numéro reconnu (ou hors 14/15/16/17)
    retombe sur ``"other"`` — jamais d'exception (cohérent avec le principe
    « signale, ne bloque jamais » de ce lot).
    """
    text = (compartment_code or "").strip()
    m = _REF_SUFFIX_RE.search(text)
    if m is None:
        m = _LEADING_NUMBER_RE.match(text)
    if m is None:
        return "other"
    number = m.group(1)
    return number if number in _NUMERIC_TANK_CODES else "other"


# ═══════════════════════════════════════════════════ Cellules composites xlsx

# "14.6 m3 (12.76 t)" → volume=14.6, mass=12.76 ; "0 m3 (0 t)" → 0/0 ;
# masse optionnelle : "14.6 m3" (sans parenthèse) → volume=14.6, mass=None.
# Tolère "m3" et "m³", et la virgule décimale (export FR).
_COMPOSITE_CELL_RE = re.compile(
    r"^\s*(?P<volume>\d+(?:[.,]\d+)?)\s*m[3³]\s*"
    r"(?:\(\s*(?P<mass>\d+(?:[.,]\d+)?)\s*t\s*\))?\s*$",
    re.IGNORECASE,
)

# Placeholders "pas de mesure" constatés dans les exports IHM réels (ex.
# ``FLGO Artemis.xlsx`` : plus d'un millier de cellules "-" sur des
# compartiments non relevés à une date donnée). Traités comme une cellule
# vide (compartiment non renseigné) — PAS comme une cellule malformée :
# c'est un défaut de mesure connu, pas une anomalie de format à signaler.
_NO_DATA_PLACEHOLDERS: frozenset[str] = frozenset({"-", "–", "—", "n/a", "na"})


@dataclass(frozen=True)
class CompositeCell:
    """Valeur d'une cellule composite Marad parsée."""

    volume_m3: Decimal
    mass_t: Decimal | None


def parse_composite_cell(raw: str | None) -> CompositeCell:
    """Parse une cellule composite ``"14.6 m3 (12.76 t)"`` de l'export IHM.

    Lève :class:`FlgoCompositeCellError` sur un format inattendu ou une
    valeur numérique invalide — **jamais** de crash silencieux : l'appelant
    (:func:`import_flgo_xlsx`) collecte ces erreurs dans son rapport plutôt
    que d'interrompre tout l'import. Une cellule vide est aussi une erreur
    ici (l'appelant décide de la traiter en "compartiment non renseigné" en
    amont, avant d'appeler cette fonction — cf. usage dans
    :func:`_parse_xlsx_data_row`).
    """
    text = (raw or "").strip()
    if not text:
        raise FlgoCompositeCellError("cellule vide")
    m = _COMPOSITE_CELL_RE.match(text)
    if not m:
        raise FlgoCompositeCellError(f"format inattendu : {raw!r}")
    try:
        volume = Decimal(m.group("volume").replace(",", "."))
        mass_raw = m.group("mass")
        mass = Decimal(mass_raw.replace(",", ".")) if mass_raw is not None else None
    except InvalidOperation as exc:
        raise FlgoCompositeCellError(f"valeur numérique invalide : {raw!r}") from exc
    return CompositeCell(volume_m3=volume, mass_t=mass)


# ══════════════════════════════════════════════════════════════════ Utilitaires


def _ensure_utc(value: datetime) -> datetime:
    """Normalise un datetime naïf en UTC (Marad ne documente pas de fuseau —
    même convention que ``services.bunkering._ensure_utc``)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _clean(val: Any) -> str | None:
    """Chaîne nettoyée, ou None si vide (même esprit que ``marad_sync._clean``,
    dupliqué ici pour ne pas coupler les deux services entre eux)."""
    if not isinstance(val, str):
        return None
    s = val.strip()
    return s or None


def _field(rec: dict, key: str) -> Any:
    """Valeur du champ ``key``, insensible à la casse (même garde que
    ``marad_sync._field`` : le serveur Marad sérialise en camelCase ou
    PascalCase selon le tenant — jamais présumer de la casse)."""
    if key in rec:
        return rec[key]
    kl = key.lower()
    for k, v in rec.items():
        if isinstance(k, str) and k.lower() == kl:
            return v
    return None


def _to_decimal(val: Any) -> Decimal | None:
    if val is None or val == "":
        return None
    try:
        return Decimal(str(val).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _parse_api_datetime(raw: str | None) -> datetime | None:
    """Date API Marad — ISO 8601, parfois sans fuseau (ex. ``"...T22:13:13.443"``).
    Normalisée UTC (cf. :func:`_ensure_utc` — Marad n'exprime pas de fuseau)."""
    if not raw:
        return None
    try:
        return _ensure_utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except ValueError:
        return None


# Formats observés dans les exports IHM ("Operation date" en texte) —
# DD/MM/YYYY HH:MM, avec ou sans les secondes.
_XLSX_DATETIME_FORMATS: tuple[str, ...] = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M")


def _parse_xlsx_datetime(raw: Any) -> datetime | None:
    """Cellule "Operation date" — soit un ``datetime`` déjà typé par
    openpyxl (cellule Excel formatée date), soit un texte ``DD/MM/YYYY HH:MM``
    (constaté dans les exports fournis)."""
    if isinstance(raw, datetime):
        return _ensure_utc(raw)
    text = (str(raw) if raw is not None else "").strip()
    if not text:
        return None
    for fmt in _XLSX_DATETIME_FORMATS:
        try:
            return _ensure_utc(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


def _normalize_action_type(raw: str | None) -> str | None:
    cleaned = _clean(raw)
    return cleaned.lower() if cleaned else None


# ═══════════════════════════════════════════════════════════ Upsert idempotent


@dataclass
class CompartmentInput:
    compartment_code: str
    volume_m3: Decimal
    mass_t: Decimal | None


async def _upsert_reading(
    db: AsyncSession,
    *,
    vessel_id: int,
    action_type: str,
    product_name: str,
    reading_datetime: datetime,
    total_volume_m3: Decimal,
    total_rob_m3: Decimal | None,
    remarks: str | None,
    source: str,
    compartments: Sequence[CompartmentInput],
) -> tuple[FlgoReading, bool]:
    """Upsert idempotent d'un relevé — clé naturelle ``(vessel_id,
    reading_datetime, action_type, product_name)``.

    Un relevé déjà présent est **mis à jour** uniquement si une valeur a
    changé (volumes, ROB, remarques, compartiments) — jamais dupliqué,
    conforme à l'anti-doublon naturel de :class:`FlgoReading`. Les
    compartiments sont **remplacés en bloc** à chaque upsert (purge + recréation
    — même pattern que ``services.bunkering.set_allocations``) : c'est la
    photographie Marad la plus récente qui prévaut, jamais une fusion
    partielle qui laisserait un compartiment obsolète.

    Renvoie ``(reading, created)`` — ``created=True`` si c'est une insertion.
    """
    dt = _ensure_utc(reading_datetime)
    existing = (
        await db.execute(
            select(FlgoReading).where(
                FlgoReading.vessel_id == vessel_id,
                FlgoReading.reading_datetime == dt,
                FlgoReading.action_type == action_type,
                FlgoReading.product_name == product_name,
            )
        )
    ).scalar_one_or_none()

    created = existing is None
    reading = existing or FlgoReading(
        vessel_id=vessel_id,
        action_type=action_type,
        product_name=product_name,
        reading_datetime=dt,
        source=source,
    )
    reading.total_volume_m3 = total_volume_m3
    reading.total_rob_m3 = total_rob_m3
    reading.remarks = remarks
    reading.source = source
    if created:
        db.add(reading)
        await db.flush()  # matérialise reading.id avant les compartiments (FK)
    else:
        # Remplace les compartiments existants (photographie la plus récente).
        # Requête explicite (jamais ``reading.compartments`` — relation
        # lazy non chargée : y accéder hors ``selectinload`` planterait sur
        # une AsyncSession, même garde que ``services.bunkering.set_allocations``).
        existing_compartments = (
            (
                await db.execute(
                    select(FlgoTankCompartmentVolume).where(
                        FlgoTankCompartmentVolume.flgo_reading_id == reading.id
                    )
                )
            )
            .scalars()
            .all()
        )
        for c in existing_compartments:
            await db.delete(c)
        await db.flush()

    for comp in compartments:
        db.add(
            FlgoTankCompartmentVolume(
                flgo_reading_id=reading.id,
                compartment_code=comp.compartment_code,
                tank_code=derive_tank_code(comp.compartment_code),
                volume_m3=comp.volume_m3,
                mass_t=comp.mass_t,
            )
        )
    await db.flush()
    return reading, created


# ═══════════════════════════════════════════════════════════ Voie 1 — API Marad

# Fenêtre de repli par défaut si aucune n'est passée explicitement — cf.
# ``settings.marad_flgo_lookback_days`` (surchargeable en .env sans
# redéploiement de code, résolue dans :func:`sync_flgo_from_api`).

# Clés candidates du volume total selon l'ActionType du record API (schéma
# ActionPerContainers non confirmé, cf. app.utils.marad.list_flgo — cette
# liste ordonnée reflète le nommage constaté sur les 9 colonnes de l'export
# IHM et sur les 3 champs "TotalVolume*M3" du schéma tidy de l'API).
_VOLUME_FIELD_BY_ACTION: dict[str, str] = {
    "measurement": "TotalVolumeMeasuredM3",
    "received": "TotalVolumeReceivedM3",
    "delivered": "TotalVolumeDeliveredM3",
}
_ALL_VOLUME_FIELDS: tuple[str, ...] = (
    "TotalVolumeMeasuredM3",
    "TotalVolumeReceivedM3",
    "TotalVolumeDeliveredM3",
)

# Clés candidates pour un item de ActionPerContainers — schéma NON confirmé
# (cf. app.utils.marad.list_flgo docstring) : lecture tolérante multi-candidats,
# jamais d'exception si aucune ne correspond (l'item est alors ignoré).
_CONTAINER_NAME_KEYS: tuple[str, ...] = (
    "ContainerName",
    "CompartmentName",
    "Name",
    "Container",
    "Compartment",
)
_CONTAINER_VOLUME_KEYS: tuple[str, ...] = (
    "VolumeM3",
    "Volume",
    "VolumeMeasuredM3",
    "Volume_m3",
)
_CONTAINER_MASS_KEYS: tuple[str, ...] = ("MassT", "Mass", "MassMeasuredT", "Mass_t")


def _extract_api_volume(rec: dict, action_type: str) -> Decimal | None:
    preferred = _VOLUME_FIELD_BY_ACTION.get(action_type)
    if preferred is not None:
        v = _to_decimal(_field(rec, preferred))
        if v is not None:
            return v
    for key in _ALL_VOLUME_FIELDS:
        v = _to_decimal(_field(rec, key))
        if v is not None:
            return v
    return None


def _extract_api_compartments(rec: dict) -> list[CompartmentInput]:
    """Lit ``ActionPerContainers`` de façon tolérante (schéma non confirmé).

    Un item qui n'est pas un dict, ou sans nom/volume reconnaissable, est
    silencieusement ignoré (jamais d'exception) — c'est le repli xlsx qui
    porte la garantie de détail par cuve tant que ce schéma n'est pas
    formellement confirmé par l'éditeur.
    """
    raw = _field(rec, "ActionPerContainers")
    if not isinstance(raw, list):
        return []
    out: list[CompartmentInput] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = None
        for k in _CONTAINER_NAME_KEYS:
            name = _clean(_field(item, k))
            if name:
                break
        volume = None
        for k in _CONTAINER_VOLUME_KEYS:
            volume = _to_decimal(_field(item, k))
            if volume is not None:
                break
        if not name or volume is None:
            continue
        mass = None
        for k in _CONTAINER_MASS_KEYS:
            mass = _to_decimal(_field(item, k))
            if mass is not None:
                break
        out.append(CompartmentInput(compartment_code=name, volume_m3=volume, mass_t=mass))
    return out


@dataclass
class FlgoSyncResult:
    configured: bool
    fetched: int = 0
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    note: str = ""
    vessels_synced: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "fetched": self.fetched,
            "imported": self.imported,
            "updated": self.updated,
            "skipped": self.skipped,
            "errors": self.errors,
            "note": self.note,
            "vessels_synced": self.vessels_synced,
        }


async def sync_flgo_from_api(
    db: AsyncSession,
    *,
    vessels: Sequence[Vessel] | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    """Synchronise les relevés FLGO depuis l'API Marad — LECTURE SEULE (GET).

    Un appel par navire (``vesselName`` requis par ``/api/FlgoAction``, cf.
    ``app.utils.marad.list_flgo``). ``vessels`` par défaut = tous les navires
    connus en base. Fenêtre par défaut = ``settings.marad_flgo_lookback_days``
    jours glissants (pas de curseur de delta confirmé côté Marad pour cet
    endpoint — un upsert idempotent rend la resynchronisation totale de la
    fenêtre sans risque de doublon).

    No-op propre (``configured=False``) si ``MARAD_API_TOKEN`` n'est pas
    configuré. Un enregistrement fautif (date illisible, volume absent…)
    n'interrompt jamais le batch (compté dans ``skipped``/``errors``) — mais
    une panne du client API lui-même (exception non catchée par
    ``app.utils.marad``, ex. simulée en test) **se propage** : c'est au
    routeur (``app/routers/marad_router.py``) de la traduire en 502, jamais
    en 500 nu.
    """
    if not marad.enabled():
        return FlgoSyncResult(
            configured=False,
            note="MARAD_API_TOKEN non configuré — intégration FLGO inactive.",
        ).as_dict()

    if vessels is None:
        vessels = list((await db.execute(select(Vessel).order_by(Vessel.code))).scalars().all())

    now = datetime.now(UTC)
    resolved_from = date_from or (now - timedelta(days=float(settings.marad_flgo_lookback_days)))
    resolved_to = date_to or now
    fmt = "%Y-%m-%dT%H:%M:%SZ"

    result = FlgoSyncResult(configured=True)
    for vessel in vessels:
        vessel_name = (vessel.name or "").strip().lower()
        if not vessel_name:
            continue
        payload = await marad.list_flgo(
            vessel_name=vessel_name,
            date_from=resolved_from.strftime(fmt),
            date_to=resolved_to.strftime(fmt),
        )
        records = payload if isinstance(payload, list) else []
        if records:
            result.vessels_synced.append(vessel.code)
        result.fetched += len(records)
        for rec in records:
            if not isinstance(rec, dict):
                result.skipped += 1
                continue
            try:
                action_type = _normalize_action_type(_field(rec, "ActionType"))
                product_name = _clean(_field(rec, "ProductName"))
                reading_dt = _parse_api_datetime(_clean(_field(rec, "Date")))
                total_volume = _extract_api_volume(rec, action_type or "")
                if (
                    not action_type
                    or not product_name
                    or reading_dt is None
                    or total_volume is None
                ):
                    result.skipped += 1
                    continue
                _, created = await _upsert_reading(
                    db,
                    vessel_id=vessel.id,
                    action_type=action_type,
                    product_name=product_name,
                    reading_datetime=reading_dt,
                    total_volume_m3=total_volume,
                    total_rob_m3=_to_decimal(_field(rec, "TotalROBM3")),
                    remarks=None,
                    source="api",
                    compartments=_extract_api_compartments(rec),
                )
                if created:
                    result.imported += 1
                else:
                    result.updated += 1
            except Exception:  # un enregistrement fautif ne stoppe pas le batch
                result.errors += 1

    result.note = "Sync read-only Marad → flgo_readings (clé naturelle, non destructeur)."
    return result.as_dict()


# ══════════════════════════════════════════════════════ Voie 2 — import xlsx


@dataclass
class FlgoImportReport:
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


# Repérage des sections "Category: ..." / nom de produit / en-tête de tableau
# dans l'export IHM ("Main sheet") — cf. module docstring pour la structure
# observée (Anemos_All*.xlsx, FLGO {Anemos,Artemis}.xlsx).
_CATEGORY_MARKER_RE = re.compile(r"^\s*category\s*:", re.IGNORECASE)
_HEADER_DATE_LABEL = "operation date"
_HEADER_TOTAL_VOLUME_LABEL = "total volume"
_HEADER_ROB_LABEL = "rob"
_HEADER_REMARKS_LABEL = "remarks"


@dataclass(frozen=True)
class _XlsxHeader:
    action_type_col: int
    date_col: int
    compartment_cols: list[tuple[int, str]]  # (index, libellé brut)
    total_volume_col: int
    rob_col: int
    remarks_col: int | None


def _match_header_row(row: tuple) -> _XlsxHeader | None:
    """Reconnaît la ligne d'en-tête du tableau ("" | "Operation date" | ...
    compartiments ... | "Total volume [m3]" | "ROB [m3]" | "Remarks" | "Docs")."""
    texts = [(_clean(str(c)) or "").lower() if c is not None else "" for c in row]
    try:
        date_col = next(i for i, t in enumerate(texts) if t == _HEADER_DATE_LABEL)
    except StopIteration:
        return None
    action_type_col = date_col - 1
    if action_type_col < 0:
        return None
    try:
        total_volume_col = next(
            i
            for i, t in enumerate(texts)
            if i > date_col and t.startswith(_HEADER_TOTAL_VOLUME_LABEL)
        )
    except StopIteration:
        return None
    rob_col = next(
        (
            i
            for i, t in enumerate(texts)
            if i > total_volume_col and t.startswith(_HEADER_ROB_LABEL)
        ),
        total_volume_col + 1,
    )
    remarks_col = next(
        (i for i, t in enumerate(texts) if i > rob_col and t.startswith(_HEADER_REMARKS_LABEL)),
        None,
    )
    compartment_cols = [
        (i, str(row[i]).strip())
        for i in range(date_col + 1, total_volume_col)
        if row[i] is not None and str(row[i]).strip()
    ]
    if not compartment_cols:
        return None
    return _XlsxHeader(
        action_type_col=action_type_col,
        date_col=date_col,
        compartment_cols=compartment_cols,
        total_volume_col=total_volume_col,
        rob_col=rob_col,
        remarks_col=remarks_col,
    )


def _row_label(vessel: Vessel, row_index: int, action_type: str | None, date_raw: Any) -> str:
    return f"{vessel.code} ligne {row_index} ({action_type or '?'} {date_raw!r})"


def _parse_xlsx_data_row(
    vessel: Vessel,
    row_index: int,
    row: tuple,
    header: _XlsxHeader,
    product_name: str,
    report: FlgoImportReport,
) -> tuple[str, str, datetime, Decimal, Decimal | None, str | None, list[CompartmentInput]] | None:
    """Parse une ligne de données xlsx — ``None`` si ligne d'espacement
    (pas d'action_type) ; erreurs bloquantes de la ligne (date/volume total
    illisibles) ajoutées à ``report.errors`` puis ``None``. Les compartiments
    individuellement malformés sont, eux, listés SANS annuler la ligne
    entière (cf. §Tests du lot : "cas malformé → erreur listée pas crash")."""
    action_type = _normalize_action_type(
        str(row[header.action_type_col]) if row[header.action_type_col] is not None else None
    )
    if not action_type:
        return None  # ligne d'espacement / hors tableau — pas une erreur

    date_raw = row[header.date_col] if header.date_col < len(row) else None
    reading_dt = _parse_xlsx_datetime(date_raw)
    if reading_dt is None:
        report.errors.append(
            f"{_row_label(vessel, row_index, action_type, date_raw)} : date illisible"
        )
        report.skipped += 1
        return None

    total_volume_raw = row[header.total_volume_col] if header.total_volume_col < len(row) else None
    total_volume = _to_decimal(total_volume_raw)
    if total_volume is None:
        report.errors.append(
            f"{_row_label(vessel, row_index, action_type, date_raw)} : "
            f"volume total illisible ({total_volume_raw!r})"
        )
        report.skipped += 1
        return None

    total_rob = None
    if header.rob_col < len(row):
        total_rob = _to_decimal(row[header.rob_col])

    remarks = None
    if header.remarks_col is not None and header.remarks_col < len(row):
        remarks = (
            _clean(str(row[header.remarks_col])) if row[header.remarks_col] is not None else None
        )

    compartments: list[CompartmentInput] = []
    for col_idx, label in header.compartment_cols:
        if col_idx >= len(row):
            continue
        raw_cell = row[col_idx]
        if raw_cell is None or (isinstance(raw_cell, str) and not raw_cell.strip()):
            continue  # compartiment non renseigné — pas une erreur (données creuses)
        if isinstance(raw_cell, str) and raw_cell.strip() in _NO_DATA_PLACEHOLDERS:
            continue  # "pas de mesure" (constaté en prod, ex. Artemis) — pas une erreur
        try:
            cell = parse_composite_cell(str(raw_cell))
        except FlgoCompositeCellError as exc:
            report.errors.append(
                f"{_row_label(vessel, row_index, action_type, date_raw)} : "
                f"compartiment {label!r} — {exc}"
            )
            continue
        compartments.append(
            CompartmentInput(compartment_code=label, volume_m3=cell.volume_m3, mass_t=cell.mass_t)
        )

    return action_type, product_name, reading_dt, total_volume, total_rob, remarks, compartments


async def import_flgo_xlsx(db: AsyncSession, vessel: Vessel, file_bytes: bytes) -> FlgoImportReport:
    """Parse + upsert un export IHM Marad FLGO (repli manuel, source=xlsx_import).

    Format reconnu : classeur « Main sheet » à en-têtes multi-niveaux, une ou
    plusieurs sections ``Category: ...`` / nom de produit, cellules
    composites ``"14.6 m3 (12.76 t)"`` par compartiment (même format que
    ``Anemos_All*.xlsx`` / ``FLGO {Anemos,Artemis}.xlsx``). Balaye TOUT le
    classeur (toutes les feuilles, plusieurs blocs produit possibles par
    feuille) plutôt que de supposer une unique feuille/bloc — robuste à des
    exports multi-produits futurs.

    Lève :class:`FlgoSyncError` seulement si le classeur est illisible
    (mauvais format de fichier) — toute anomalie de contenu (ligne/cellule
    malformée) est collectée dans le rapport, jamais une exception.
    """
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:
        raise FlgoSyncError(f"fichier Excel illisible : {exc}") from exc

    report = FlgoImportReport()
    for ws in wb.worksheets:
        current_product: str | None = None
        current_header: _XlsxHeader | None = None
        expect_product_next = False
        for row_index, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if not row:
                continue
            col1 = _clean(str(row[1])) if len(row) > 1 and row[1] is not None else None
            if col1 and _CATEGORY_MARKER_RE.match(col1):
                expect_product_next = True
                continue
            if expect_product_next and col1:
                current_product = col1
                expect_product_next = False
                continue
            header = _match_header_row(row)
            if header is not None:
                current_header = header
                continue
            if current_header is None or current_product is None:
                continue
            parsed = _parse_xlsx_data_row(
                vessel, row_index, row, current_header, current_product, report
            )
            if parsed is None:
                continue
            (
                action_type,
                product_name,
                reading_dt,
                total_volume,
                total_rob,
                remarks,
                compartments,
            ) = parsed
            _, created = await _upsert_reading(
                db,
                vessel_id=vessel.id,
                action_type=action_type,
                product_name=product_name,
                reading_datetime=reading_dt,
                total_volume_m3=total_volume,
                total_rob_m3=total_rob,
                remarks=remarks,
                source="xlsx_import",
                compartments=compartments,
            )
            if created:
                report.imported += 1
            else:
                report.updated += 1

    return report


# ═══════════════════════════════════════════════ Rapprochements service-level
# (R17/R24/R25 — SIGNALENT, ne corrigent jamais ; aucune règle enregistrée
# dans validation_engine.RULES dans ce lot, cf. module docstring et
# services.bunkering pour le même principe au lot 6.)

_DEFAULT_TOLERANCE_FLGO_ECART_TEMPS_H = Decimal("120")
_DEFAULT_DELAI_FLGO_BUNKERING_J = Decimal("5")
_DEFAULT_TOLERANCE_FLGO_INTERNE_M3 = Decimal("2")


@dataclass(frozen=True)
class FlgoNearestMatch:
    """R17 — relevé FLGO le plus proche en date d'un ``dt`` donné (Departure/
    Arrival). ``within_tolerance=False`` (au-delà de ``tolerance_hours``, ou
    aucun relevé) → le rapprochement doit être déclassé Info par l'appelant
    (lot 8) plutôt qu'utilisé comme preuve ROB fiable."""

    reading: FlgoReading | None
    delta_hours: Decimal | None
    within_tolerance: bool
    tolerance_hours: Decimal


async def flgo_nearest_reading(db: AsyncSession, vessel: Vessel, dt: datetime) -> FlgoNearestMatch:
    """Relevé FLGO le plus proche en datetime pour ``vessel`` — R17.

    Restreint aux relevés porteurs d'un ROB (``total_rob_m3`` non NULL) —
    seul le ROB FLGO a un sens pour le rapprochement R17 (ROB déclaré
    MyTOWT vs ROB FLGO). Tolérance ``tolerance_flgo_ecart_temps_h``
    (défaut 120 h ≈ 5 j, override par navire possible).
    """
    target = _ensure_utc(dt)
    tv = await get_threshold(db, "R17", "tolerance_flgo_ecart_temps_h", vessel_id=vessel.id)
    tolerance = tv.value if tv is not None else _DEFAULT_TOLERANCE_FLGO_ECART_TEMPS_H

    rows = (
        (
            await db.execute(
                select(FlgoReading).where(
                    FlgoReading.vessel_id == vessel.id,
                    FlgoReading.total_rob_m3.isnot(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return FlgoNearestMatch(
            reading=None, delta_hours=None, within_tolerance=False, tolerance_hours=tolerance
        )

    nearest = min(rows, key=lambda r: abs(_ensure_utc(r.reading_datetime) - target))
    delta = abs(_ensure_utc(nearest.reading_datetime) - target)
    delta_hours = Decimal(delta.total_seconds()) / Decimal(3600)
    return FlgoNearestMatch(
        reading=nearest,
        delta_hours=delta_hours,
        within_tolerance=delta_hours <= tolerance,
        tolerance_hours=tolerance,
    )


@dataclass(frozen=True)
class FlgoBunkerMatch:
    """R24 — soutage BDN sans lecture FLGO "Received" correspondante sous
    ``delai_flgo_bunkering_j`` jours (fenêtre symétrique autour de la date de
    livraison — cf. ``BunkerTankAllocation.ecart_jours_bdn_flgo`` observé
    dans l'inventaire, un écart en jours **absolu**, jamais signé)."""

    candidates: tuple[FlgoReading, ...]
    window_days: Decimal
    matched: bool


async def flgo_matches_for_bunker(db: AsyncSession, bunker: BunkerOperation) -> FlgoBunkerMatch:
    """Lectures FLGO "Received" dans la fenêtre ``delai_flgo_bunkering_j``
    autour de la date de livraison d'un soutage — R24."""
    tv = await get_threshold(db, "R24", "delai_flgo_bunkering_j", vessel_id=bunker.vessel_id)
    window_days = tv.value if tv is not None else _DEFAULT_DELAI_FLGO_BUNKERING_J
    delivery = _ensure_utc(bunker.delivery_datetime_utc)
    window = timedelta(days=float(window_days))
    lo, hi = delivery - window, delivery + window

    rows = (
        (
            await db.execute(
                select(FlgoReading)
                .where(
                    FlgoReading.vessel_id == bunker.vessel_id,
                    FlgoReading.action_type == "received",
                    FlgoReading.reading_datetime >= lo,
                    FlgoReading.reading_datetime <= hi,
                )
                .order_by(FlgoReading.reading_datetime)
            )
        )
        .scalars()
        .all()
    )
    return FlgoBunkerMatch(candidates=tuple(rows), window_days=window_days, matched=bool(rows))


@dataclass(frozen=True)
class FlgoConsistencyCheck:
    """R25 — cohérence interne d'un relevé : Σ compartiments vs volume total
    déclaré. ``flagged=True`` = écart hors tolérance — **signalé, jamais
    corrigé** (aucune écriture sur ``FlgoReading``)."""

    flagged: bool
    total_declared_m3: Decimal
    total_compartments_m3: Decimal
    delta_m3: Decimal
    tolerance_m3: Decimal


async def check_internal_consistency(
    db: AsyncSession,
    reading: FlgoReading,
    compartments: Sequence[FlgoTankCompartmentVolume] | None = None,
) -> FlgoConsistencyCheck:
    """Σ ``compartments.volume_m3`` vs ``reading.total_volume_m3`` — R25.

    ``compartments`` peut être passé pré-chargé (écran liste, évite le N+1)
    — sinon requêté ici. Ne modifie jamais ``reading`` : c'est un contrôle
    de lecture, le signalement revient à l'appelant (écran / futur lot 8).
    """
    if compartments is None:
        compartments = (
            (
                await db.execute(
                    select(FlgoTankCompartmentVolume).where(
                        FlgoTankCompartmentVolume.flgo_reading_id == reading.id
                    )
                )
            )
            .scalars()
            .all()
        )
    total_compartments = sum((c.volume_m3 for c in compartments), Decimal("0"))
    declared = reading.total_volume_m3 or Decimal("0")
    delta = abs(declared - total_compartments)

    tv = await get_threshold(db, "R25", "tolerance_flgo_interne_m3", vessel_id=reading.vessel_id)
    tolerance = tv.value if tv is not None else _DEFAULT_TOLERANCE_FLGO_INTERNE_M3

    return FlgoConsistencyCheck(
        flagged=delta > tolerance,
        total_declared_m3=declared,
        total_compartments_m3=total_compartments,
        delta_m3=delta,
        tolerance_m3=tolerance,
    )
