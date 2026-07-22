"""QHSE — pipeline d'ingestion Excel (Phase 0).

Parse les exports FMS actuels (``QHSE Reports {Anemos,Artemis}.xlsx``, 41
colonnes à plat, une ligne par rapport — cf. cahier des charges §3.1/§16.1)
et les normalise vers le schéma ``app.models.qhse`` en résolvant les entités
existantes plutôt qu'en dupliquant du texte libre (§3.5, §2.1.B) :

- ``VesselName`` → ``vessels.id`` (résolution stricte par ``code``/``name`` ;
  navire non reconnu = ligne en erreur, jamais d'import silencieux — RQ03).
- ``IssuedBy``/``DescriptionAddedBy``/``*ResponsiblePerson`` → tentative de
  résolution vers ``users``/``crew_members`` par nom normalisé (accents/
  casse/espaces). Repli en texte libre (``issued_by_raw``) uniquement pour
  ``IssuedBy`` — les tiers externes (USCG, Class, MaraSoft...) n'ont pas de
  compte MyTOWT.
- ``IssuedPlace`` → nettoyage des artefacts de synchronisation (``[Sync]``/
  ``[Sync1]``) ; pas de résolution vers ``ports``/``legs`` en Phase 0 (aucun
  champ fiable dans l'export pour le faire proprement — cf. plan Phase 0).
- Séquences ``_x000D_`` (artefact d'export Excel/XML) strippées des champs
  texte libre.
- Quarantaine (ligne rejetée, jamais importée) : ``ClosedDate`` antérieure à
  ``IssuedDate``, ou ``Subject``/``Description`` contenant un motif de test
  (test/essai/demo) — cf. le record "Essai de non conformité" identifié dans
  l'analyse (§3.5). Les mêmes anomalies restent détectables après import via
  les règles de qualité RQ01/RQ02 (``app.services.qhse_validation_rules``),
  pour les rapports qui entreraient par un autre chemin plus tard (saisie
  manuelle, API FMS).

Simplification Phase 0 assumée : ``CorrectiveAction``/``RootCauseEvaluation``
résolvent ``responsible_user_id`` (meilleur effort) mais pas
``proposed_by``/``approved_by``/``implemented_by`` (laissés ``None``) — ces
champs ont un taux de remplissage déjà très faible dans la source (cahier des
charges §3.4) et leur résolution fine est différée à la Phase 1.

Jamais d'exception qui interrompt tout le lot (même principe que
``flgo_sync.import_flgo_xlsx``) : une ligne en anomalie est comptée et
décrite dans ``QhseImportReport.errors``, jamais un crash de l'import.
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import openpyxl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crew import CrewMember
from app.models.qhse import CorrectiveAction, QhseReport, RootCauseEvaluation
from app.models.user import User
from app.models.vessel import Vessel

# ══════════════════════════════════════════════════════════════ Exceptions


class QhseIngestionError(Exception):
    """Erreur métier QHSE — le futur routeur la traduira en réponse HTTP propre."""


# ══════════════════════════════════════════════════════════════ Rapport


@dataclass
class QhseImportReport:
    imported: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════ Normalisation texte

_SYNC_SUFFIX_RE = re.compile(r"\[\s*sync\d*\s*\]", re.IGNORECASE)
_X000D_RE = re.compile(r"_x000[dD]_")
_TEST_PATTERN_RE = re.compile(r"\b(test|essai|demo)\b", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def _clean_text(raw: Any) -> str | None:
    """Nettoie un champ texte libre : strip ``_x000D_``, espaces multiples."""
    if raw is None:
        return None
    text = _X000D_RE.sub("\n", str(raw))
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text or None


def _clean_place(raw: Any) -> str | None:
    """Nettoie ``IssuedPlace`` : artefacts ``[Sync]``/``[Sync1]`` + espaces."""
    if raw is None:
        return None
    text = _SYNC_SUFFIX_RE.sub("", str(raw))
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text or None


def _normalize_name(raw: str | None) -> str:
    """Nom → clé de correspondance : NFKD, accents retirés, casefold, espaces.

    Même logique que ``services.marad_sync._norm_name`` — dupliquée ici
    volontairement plutôt que de toucher un module MRV/crew-sync sans
    rapport avec ce module (cf. plan Phase 0).
    """
    if not raw:
        return ""
    decomposed = unicodedata.normalize("NFKD", raw)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _WHITESPACE_RE.sub(" ", stripped).strip().casefold()


# ══════════════════════════════════════════════════════════════ Dates

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _parse_datetime(raw: Any) -> datetime | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day, tzinfo=UTC)
    text = str(raw).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_date(raw: Any) -> date | None:
    dt = _parse_datetime(raw)
    return dt.date() if dt else None


# ══════════════════════════════════════════════════════════════ Grade mapping

# Valeurs brutes observées dans l'export (§3.3) → enum interne (QHSE_GRADES).
_GRADE_MAP: dict[str, str] = {
    "accident / material breakdown": "accident",
    "accident/material breakdown": "accident",
    "non conformity": "non_conformity",
    "near miss / hazard": "near_miss",
    "near miss/hazard": "near_miss",
    "observation": "observation",
    "deficiency": "deficiency",
    "casualty": "casualty",
}


def _map_grade(raw: Any) -> str | None:
    if raw is None:
        return None
    return _GRADE_MAP.get(str(raw).strip().lower())


# ══════════════════════════════════════════════════════════════ En-têtes xlsx

# Les 41 colonnes de l'export FMS (cahier des charges §3.1/§16.1) — ordre non
# garanti à l'ingestion, on résout par nom d'en-tête plutôt que par position.
_HEADER_ALIASES: dict[str, str] = {
    "subject": "Subject",
    "code": "Code",
    "description": "Description",
    "issuedby": "IssuedBy",
    "contact": "Contact",
    "issuedplace": "IssuedPlace",
    "grade": "Grade",
    "issueddate": "IssuedDate",
    "closeddate": "ClosedDate",
    "vesselname": "VesselName",
    "descriptionaddeddate": "DescriptionAddedDate",
    "descriptionaddedby": "DescriptionAddedBy",
    "correctiveactiondescription": "CorrectiveActionDescription",
    "correctiveactionlimitdate": "CorrectiveActionLimitDate",
    "correctiveactionfinisheddate": "CorrectiveActionFinishedDate",
    "correctiveactionresponsibleperson": "CorrectiveActionResponsiblePerson",
    "correctiveactionresponsiblerank": "CorrectiveActionResponsibleRank",
    "evaluationrootcause": "EvaluationRootCause",
    "evaluationpreventativeaction": "EvaluationPreventativeAction",
    "evaluationlimitdate": "EvaluationLimitDate",
    "evaluationfinisheddate": "EvaluationFinishedDate",
    "evaluationresponsibleperson": "EvaluationResponsiblePerson",
    "evaluationresponsiblerank": "EvaluationResponsibleRank",
}


def _build_header_index(header_row: tuple) -> dict[str, int]:
    """Colonne canonique → index, en tolérant l'ordre/la casse de l'export."""
    index: dict[str, int] = {}
    for i, cell in enumerate(header_row):
        if cell is None:
            continue
        key = str(cell).strip().lower().replace(" ", "").replace("_", "")
        canonical = _HEADER_ALIASES.get(key)
        if canonical:
            index[canonical] = i
    return index


def _cell(row: tuple, index: dict[str, int], column: str) -> Any:
    i = index.get(column)
    return row[i] if i is not None and i < len(row) else None


# ══════════════════════════════════════════════════════════════ Import


async def import_qhse_xlsx(db: AsyncSession, file_bytes: bytes) -> QhseImportReport:
    """Parse + importe un export QHSE FMS (une ligne = un rapport).

    Lève :class:`QhseIngestionError` seulement si le classeur est illisible
    (mauvais format de fichier) — toute anomalie de contenu (navire non
    résolu, dates incohérentes, motif de test) est quarantainée ligne par
    ligne dans ``QhseImportReport.errors``, jamais une exception qui
    interromprait tout le lot.
    """
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:
        raise QhseIngestionError(f"fichier Excel illisible : {exc}") from exc

    report = QhseImportReport()

    # Index navires/personnes chargé une fois (flotte + effectifs restent
    # petits — évite le N+1 par ligne).
    vessels = (await db.execute(select(Vessel))).scalars().all()
    vessel_by_key = {v.code.strip().lower(): v for v in vessels}
    vessel_by_key.update({v.name.strip().lower(): v for v in vessels})

    users = (await db.execute(select(User))).scalars().all()
    user_by_norm = {_normalize_name(u.full_name): u for u in users if u.full_name}

    crew = (await db.execute(select(CrewMember))).scalars().all()
    crew_by_norm = {_normalize_name(c.full_name): c for c in crew}

    for sheet in wb.worksheets:
        rows_iter = sheet.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            continue
        index = _build_header_index(header_row)
        if "Subject" not in index or "VesselName" not in index:
            # Feuille non reconnue (pas le format attendu) — ignorée, pas une erreur.
            continue

        for row_number, row in enumerate(rows_iter, start=2):
            if row is None or all(c is None for c in row):
                continue
            try:
                await _import_row(
                    db,
                    row=row,
                    index=index,
                    sheet_name=sheet.title,
                    row_number=row_number,
                    vessel_by_key=vessel_by_key,
                    user_by_norm=user_by_norm,
                    crew_by_norm=crew_by_norm,
                    report=report,
                )
            except Exception as exc:  # jamais de crash de lot — cf. docstring module
                await db.rollback()
                report.errors.append(f"{sheet.title}!L{row_number} : erreur inattendue ({exc})")
                report.skipped += 1

    return report


async def _import_row(
    db: AsyncSession,
    *,
    row: tuple,
    index: dict[str, int],
    sheet_name: str,
    row_number: int,
    vessel_by_key: dict[str, Vessel],
    user_by_norm: dict[str, User],
    crew_by_norm: dict[str, CrewMember],
    report: QhseImportReport,
) -> None:
    origin = f"{sheet_name}!L{row_number}"

    subject = _clean_text(_cell(row, index, "Subject"))
    description = _clean_text(_cell(row, index, "Description"))
    issued_date = _parse_datetime(_cell(row, index, "IssuedDate"))
    closed_date = _parse_datetime(_cell(row, index, "ClosedDate"))

    # ── Quarantaine — jamais importées (RQ01/RQ02) ─────────────────────────
    if issued_date and closed_date and closed_date < issued_date:
        report.errors.append(
            f"{origin} : ClosedDate antérieure à IssuedDate — quarantainée (RQ01)."
        )
        report.skipped += 1
        return
    test_match = None
    if subject:
        test_match = _TEST_PATTERN_RE.search(subject)
    if not test_match and description:
        test_match = _TEST_PATTERN_RE.search(description)
    if test_match:
        report.errors.append(
            f"{origin} : motif de test détecté (« {test_match.group(0)} ») — quarantainée (RQ02)."
        )
        report.skipped += 1
        return

    # ── Navire — résolution stricte, obligatoire (RQ03) ─────────────────────
    vessel_name_raw = _cell(row, index, "VesselName")
    vessel = vessel_by_key.get(str(vessel_name_raw or "").strip().lower())
    if vessel is None:
        report.errors.append(
            f"{origin} : navire « {vessel_name_raw} » non reconnu dans le référentiel — quarantainée (RQ03)."
        )
        report.skipped += 1
        return

    if not subject or issued_date is None:
        report.errors.append(f"{origin} : Subject ou IssuedDate manquant — quarantainée.")
        report.skipped += 1
        return

    grade = _map_grade(_cell(row, index, "Grade"))
    if grade is None:
        report.errors.append(
            f"{origin} : Grade « {_cell(row, index, 'Grade')} » non reconnu — quarantainée."
        )
        report.skipped += 1
        return

    # ── Rapporteur — résolution meilleur effort, repli texte libre ──────────
    issued_by_raw = _clean_text(_cell(row, index, "IssuedBy"))
    reporter_user = user_by_norm.get(_normalize_name(issued_by_raw))
    reporter_crew = None if reporter_user else crew_by_norm.get(_normalize_name(issued_by_raw))

    description_added_by_raw = _clean_text(_cell(row, index, "DescriptionAddedBy"))
    description_added_by_user = user_by_norm.get(_normalize_name(description_added_by_raw))

    qhse_report = QhseReport(
        vessel_id=vessel.id,
        subject=subject,
        description=description,
        grade=grade,
        report_source="operational",
        issued_date=issued_date,
        closed_date=closed_date,
        issued_place=_clean_place(_cell(row, index, "IssuedPlace")),
        issued_by_raw=None if (reporter_user or reporter_crew) else issued_by_raw,
        reporter_user_id=reporter_user.id if reporter_user else None,
        reporter_crew_member_id=reporter_crew.id if reporter_crew else None,
        contact=_clean_text(_cell(row, index, "Contact")),
        description_added_date=_parse_datetime(_cell(row, index, "DescriptionAddedDate")),
        description_added_by_user_id=(
            description_added_by_user.id if description_added_by_user else None
        ),
    )
    db.add(qhse_report)
    await db.flush()

    corrective_description = _clean_text(_cell(row, index, "CorrectiveActionDescription"))
    corrective_limit = _parse_date(_cell(row, index, "CorrectiveActionLimitDate"))
    corrective_finished = _parse_date(_cell(row, index, "CorrectiveActionFinishedDate"))
    if corrective_description or corrective_limit or corrective_finished:
        responsible_raw = _clean_text(_cell(row, index, "CorrectiveActionResponsiblePerson"))
        responsible_user = user_by_norm.get(_normalize_name(responsible_raw))
        db.add(
            CorrectiveAction(
                report_id=qhse_report.id,
                description=corrective_description,
                limit_date=corrective_limit,
                finished_date=corrective_finished,
                responsible_user_id=responsible_user.id if responsible_user else None,
                responsible_rank=_clean_text(_cell(row, index, "CorrectiveActionResponsibleRank")),
                status="implemented" if corrective_finished else "open",
            )
        )

    root_cause_text = _clean_text(_cell(row, index, "EvaluationRootCause"))
    preventative_action = _clean_text(_cell(row, index, "EvaluationPreventativeAction"))
    evaluation_limit = _parse_date(_cell(row, index, "EvaluationLimitDate"))
    evaluation_finished = _parse_date(_cell(row, index, "EvaluationFinishedDate"))
    if root_cause_text or preventative_action or evaluation_limit or evaluation_finished:
        responsible_raw = _clean_text(_cell(row, index, "EvaluationResponsiblePerson"))
        responsible_user = user_by_norm.get(_normalize_name(responsible_raw))
        db.add(
            RootCauseEvaluation(
                report_id=qhse_report.id,
                root_cause_text=root_cause_text,
                preventative_action=preventative_action,
                limit_date=evaluation_limit,
                finished_date=evaluation_finished,
                responsible_user_id=responsible_user.id if responsible_user else None,
                responsible_rank=_clean_text(_cell(row, index, "EvaluationResponsibleRank")),
                status="implemented" if evaluation_finished else "open",
            )
        )

    await db.flush()
    report.imported += 1
