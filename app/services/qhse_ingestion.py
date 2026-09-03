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

**Réconciliation des ré-imports (D10)** : le FMS ré-exporte périodiquement
l'intégralité du registre, workflow CAPA compris — sans clé de
rapprochement, chaque ré-export duplique tout. ``source_code`` (hachage de la
clé naturelle vessel/date/sujet/description, cf. :func:`_compute_source_code`)
identifie une ligne déjà connue ; trouvée, elle est **mise à jour** (« photo
la plus récente », même doctrine que ``flgo_sync._upsert_reading``), jamais
dupliquée. ``QhseImportBatch`` trace quel import a créé ou rafraîchi chaque
ligne (``qhse_reports.import_batch_id``).

Chaque rapport créé ou mis à jour est ensuite passé à
``validation_engine.run_rules(db, "qhse", ...)`` — RQ01-RQ03 étaient
enregistrées dans le moteur (même patron que MRV) mais jamais exécutées
contre de vraies lignes persistées : ceci complète cette fonctionnalité sans
toucher aux quarantaines pré-insertion ci-dessus, qui répondent à un besoin
différent (refuser d'insérer) et doivent rester telles quelles.
"""

from __future__ import annotations

import hashlib
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
from app.models.qhse import CorrectiveAction, QhseImportBatch, QhseReport, RootCauseEvaluation
from app.models.user import User
from app.models.vessel import Vessel
from app.services.validation_engine import run_rules

# ══════════════════════════════════════════════════════════════ Exceptions


class QhseIngestionError(Exception):
    """Erreur métier QHSE — le futur routeur la traduira en réponse HTTP propre."""


# ══════════════════════════════════════════════════════════════ Rapport


@dataclass
class QhseImportReport:
    """Compte rendu d'un import — **écarté** et **à vérifier** ne se confondent pas.

    ``skipped`` / ``errors`` : lignes **non importées**. C'est une perte de donnée
    réglementaire, elle doit être persistée par l'appelant (cf. `qhse_router`).

    ``flagged`` / ``warnings`` : lignes **importées** mais portant un doute (motif de
    test présumé). Elles sont dans le registre, marquées, et un humain tranche —
    jamais supprimées à sa place.

    ``updated`` : ligne **reconnue** via ``source_code`` (D10) et rafraîchie plutôt
    que dupliquée — distinct d'``imported`` (première apparition de ce rapport).
    """

    imported: int = 0
    updated: int = 0
    skipped: int = 0
    flagged: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


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


def _compute_source_code(
    vessel_id: int, issued_date: datetime, subject: str, description: str | None
) -> str:
    """Clé naturelle de réconciliation (D10) — SHA-256 de meilleur effort.

    ``(vessel_id, jour d'émission, sujet normalisé, description normalisée)`` :
    aucune des 41 colonnes de l'export FMS ne porte d'identifiant stable
    (cahier des charges §3.1/§16.1). ``Subject`` seul ne suffit pas — trois
    signalements PSC réels (Artemis, même navire, même jour, sujet générique
    « PSC ») ne se distinguent que par leur ``Description`` (§3.3) ; validé
    sur les 190 lignes réelles Anemos+Artemis, 190 clés distinctes.

    L'heure n'entre pas dans la clé (seul le jour), les deux exports sources
    ne portant pas de garantie d'heure stable. ``description`` participe à la
    clé : si son texte est réellement retouché entre deux exports (plutôt que
    seulement complété via les champs CAPA/Evaluation, qui n'y entrent pas),
    la ligne sera reconnue comme nouvelle plutôt que mise à jour — limite
    assumée en l'absence d'identifiant FMS, jamais un risque de fusionner deux
    rapports réellement distincts.
    """
    parts = [str(vessel_id), issued_date.date().isoformat(), _normalize_name(subject)]
    parts.append(_normalize_name(description) if description else "")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


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


async def import_qhse_xlsx(
    db: AsyncSession,
    file_bytes: bytes,
    *,
    filename: str | None = None,
    imported_by_user_id: int | None = None,
) -> QhseImportReport:
    """Parse + importe/réconcilie un export QHSE FMS (une ligne = un rapport).

    Lève :class:`QhseIngestionError` seulement si le classeur est illisible
    (mauvais format de fichier) — toute anomalie de contenu (navire non
    résolu, dates incohérentes, motif de test) est quarantainée ligne par
    ligne dans ``QhseImportReport.errors``, jamais une exception qui
    interromprait tout le lot.

    Une ligne déjà connue (``source_code``, D10) est mise à jour, pas
    dupliquée. Chaque rapport créé ou mis à jour est ensuite passé au moteur
    de qualité générique (``validation_engine.run_rules``, scope ``qhse``) —
    en un seul appel groupé après la boucle, pas ligne par ligne : RQ01-RQ03
    ne sont pas des règles séquentielles, et un appel groupé évite N
    aller-retours.
    """
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:
        raise QhseIngestionError(f"fichier Excel illisible : {exc}") from exc

    report = QhseImportReport()
    batch = QhseImportBatch(filename=filename, imported_by_user_id=imported_by_user_id)
    db.add(batch)
    await db.flush()  # matérialise batch.id avant de l'attacher aux rapports

    touched: list[QhseReport] = []

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
            # 🔴 Un POINT DE REPRISE PAR LIGNE, et non un `rollback()` global.
            #
            # L'ancien code appelait `db.rollback()` dans ce gestionnaire : en
            # SQLAlchemy, cela annule la transaction ENTIÈRE, donc **toutes les
            # lignes déjà importées** de ce même fichier. Or `report.imported`
            # n'était pas décrémenté : l'écran annonçait « N importés » alors
            # qu'aucun n'avait survécu. Une seule ligne malformée en fin de
            # classeur détruisait ainsi silencieusement tout l'import, avec un
            # compte rendu de succès.
            #
            # Le savepoint isole l'échec : la ligne fautive est annulée, les
            # précédentes restent, et le compteur reste vrai. Même motif que
            # `packing_list.assign_bl_number`.
            try:
                async with db.begin_nested():
                    outcome = await _import_row(
                        db,
                        row=row,
                        index=index,
                        sheet_name=sheet.title,
                        row_number=row_number,
                        vessel_by_key=vessel_by_key,
                        user_by_norm=user_by_norm,
                        crew_by_norm=crew_by_norm,
                        report=report,
                        batch_id=batch.id,
                    )
            except Exception as exc:  # jamais de crash de lot — cf. docstring module
                report.errors.append(f"{sheet.title}!L{row_number} : erreur inattendue ({exc})")
                report.skipped += 1
            else:
                # Compté APRÈS la sortie réussie du savepoint, jamais avant : une
                # violation de contrainte peut surgir à sa libération, et un
                # compteur incrémenté à l'intérieur mentirait dans ce cas précis.
                if outcome is not None:
                    kind, qhse_report = outcome
                    if kind == "created":
                        report.imported += 1
                    else:
                        report.updated += 1
                    touched.append(qhse_report)

    if touched:
        await run_rules(db, "qhse", touched)

    batch.created_count = report.imported
    batch.updated_count = report.updated
    batch.skipped_count = report.skipped
    batch.flagged_count = report.flagged

    return report


async def _upsert_corrective_action(
    db: AsyncSession,
    *,
    report_id: int,
    description: str | None,
    limit_date: date | None,
    finished_date: date | None,
    responsible_user_id: int | None,
    responsible_rank: str | None,
) -> None:
    """Crée ou met à jour l'action corrective d'un rapport — jamais un doublon.

    ``report_id`` est UNIQUE (1:0..1 avec ``QhseReport``) : un ré-import qui
    créerait une seconde ligne violerait cette contrainte. Rien à faire si
    aucun champ n'est renseigné — et une ligne déjà existante n'est **jamais
    supprimée** si un export ultérieur cesse de renseigner ces champs (perte
    de donnée), photo la plus récente ou pas.
    """
    if not (description or limit_date or finished_date):
        return
    existing = (
        await db.execute(select(CorrectiveAction).where(CorrectiveAction.report_id == report_id))
    ).scalar_one_or_none()
    action = existing or CorrectiveAction(report_id=report_id)
    action.description = description
    action.limit_date = limit_date
    action.finished_date = finished_date
    action.responsible_user_id = responsible_user_id
    action.responsible_rank = responsible_rank
    action.status = "implemented" if finished_date else "open"
    if existing is None:
        db.add(action)


async def _upsert_root_cause(
    db: AsyncSession,
    *,
    report_id: int,
    root_cause_text: str | None,
    preventative_action: str | None,
    limit_date: date | None,
    finished_date: date | None,
    responsible_user_id: int | None,
    responsible_rank: str | None,
) -> None:
    """Miroir de :func:`_upsert_corrective_action` pour l'évaluation racine."""
    if not (root_cause_text or preventative_action or limit_date or finished_date):
        return
    existing = (
        await db.execute(
            select(RootCauseEvaluation).where(RootCauseEvaluation.report_id == report_id)
        )
    ).scalar_one_or_none()
    evaluation = existing or RootCauseEvaluation(report_id=report_id)
    evaluation.root_cause_text = root_cause_text
    evaluation.preventative_action = preventative_action
    evaluation.limit_date = limit_date
    evaluation.finished_date = finished_date
    evaluation.responsible_user_id = responsible_user_id
    evaluation.responsible_rank = responsible_rank
    evaluation.status = "implemented" if finished_date else "open"
    if existing is None:
        db.add(evaluation)


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
    batch_id: int,
) -> tuple[str, QhseReport] | None:
    """Importe/réconcilie une ligne. Renvoie ``("created"|"updated", rapport)``,
    ou ``None`` si quarantainée.

    Les compteurs ``imported``/``updated`` sont incrémentés par l'appelant
    **après** la sortie réussie du savepoint — pas ici : une violation de
    contrainte peut surgir à sa libération, et le compteur mentirait alors.
    """
    origin = f"{sheet_name}!L{row_number}"

    subject = _clean_text(_cell(row, index, "Subject"))
    description = _clean_text(_cell(row, index, "Description"))
    issued_date = _parse_datetime(_cell(row, index, "IssuedDate"))
    closed_date = _parse_datetime(_cell(row, index, "ClosedDate"))

    # ── Quarantaine — jamais importées (RQ01) ───────────────────────────────
    if issued_date and closed_date and closed_date < issued_date:
        report.errors.append(
            f"{origin} : ClosedDate antérieure à IssuedDate — quarantainée (RQ01)."
        )
        report.skipped += 1
        return None

    # ── 🔴 Motif de test présumé : IMPORTÉ ET MARQUÉ, jamais supprimé (RQ02) ──
    #
    # L'ancien code écartait toute ligne dont le sujet ou la description contenait
    # « test », « essai » ou « demo ». Or ces mots sont le VOCABULAIRE MÊME de
    # l'ISM : « Essai des embarcations de sauvetage », « Test du système d'alarme
    # incendie », « Essai hebdomadaire du gouvernail » sont des exercices
    # obligatoires, et leurs non-conformités sont exactement ce qu'un registre ISM
    # doit contenir. Elles disparaissaient du registre.
    #
    # La règle RQ02 elle-même dit « à confirmer AVANT import » : c'est un signal
    # destiné à un humain, que l'ingestion avait transformé en suppression.
    #
    # Une non-conformité réglementaire ne se supprime pas à la place de
    # l'utilisateur. Elle entre dans le registre, marquée `suspected_test`, et
    # quelqu'un tranche — décision réversible, plutôt qu'une perte muette.
    test_match = None
    if subject:
        test_match = _TEST_PATTERN_RE.search(subject)
    if not test_match and description:
        test_match = _TEST_PATTERN_RE.search(description)

    # ── Navire — résolution stricte, obligatoire (RQ03) ─────────────────────
    vessel_name_raw = _cell(row, index, "VesselName")
    vessel = vessel_by_key.get(str(vessel_name_raw or "").strip().lower())
    if vessel is None:
        report.errors.append(
            f"{origin} : navire « {vessel_name_raw} » non reconnu dans le référentiel — quarantainée (RQ03)."
        )
        report.skipped += 1
        return None

    if not subject or issued_date is None:
        report.errors.append(f"{origin} : Subject ou IssuedDate manquant — quarantainée.")
        report.skipped += 1
        return None

    grade = _map_grade(_cell(row, index, "Grade"))
    if grade is None:
        report.errors.append(
            f"{origin} : Grade « {_cell(row, index, 'Grade')} » non reconnu — quarantainée."
        )
        report.skipped += 1
        return None

    # ── Rapporteur — résolution meilleur effort, repli texte libre ──────────
    issued_by_raw = _clean_text(_cell(row, index, "IssuedBy"))
    reporter_user = user_by_norm.get(_normalize_name(issued_by_raw))
    reporter_crew = None if reporter_user else crew_by_norm.get(_normalize_name(issued_by_raw))

    description_added_by_raw = _clean_text(_cell(row, index, "DescriptionAddedBy"))
    description_added_by_user = user_by_norm.get(_normalize_name(description_added_by_raw))

    # ── Réconciliation (D10) — une ligne déjà connue est mise à jour, jamais
    # dupliquée. Cf. docstring de module et :func:`_compute_source_code`.
    source_code = _compute_source_code(vessel.id, issued_date, subject, description)
    existing = (
        await db.execute(select(QhseReport).where(QhseReport.source_code == source_code))
    ).scalar_one_or_none()

    kind = "updated" if existing is not None else "created"
    qhse_report = existing or QhseReport(vessel_id=vessel.id, source_code=source_code)
    qhse_report.vessel_id = vessel.id
    qhse_report.source_code = source_code
    qhse_report.subject = subject
    qhse_report.description = description
    qhse_report.grade = grade
    qhse_report.report_source = "suspected_test" if test_match else "operational"
    qhse_report.issued_date = issued_date
    qhse_report.closed_date = closed_date
    qhse_report.issued_place = _clean_place(_cell(row, index, "IssuedPlace"))
    qhse_report.issued_by_raw = None if (reporter_user or reporter_crew) else issued_by_raw
    qhse_report.reporter_user_id = reporter_user.id if reporter_user else None
    qhse_report.reporter_crew_member_id = reporter_crew.id if reporter_crew else None
    qhse_report.contact = _clean_text(_cell(row, index, "Contact"))
    qhse_report.description_added_date = _parse_datetime(_cell(row, index, "DescriptionAddedDate"))
    qhse_report.description_added_by_user_id = (
        description_added_by_user.id if description_added_by_user else None
    )
    qhse_report.import_batch_id = batch_id
    if existing is None:
        db.add(qhse_report)
    await db.flush()

    corrective_responsible_raw = _clean_text(_cell(row, index, "CorrectiveActionResponsiblePerson"))
    corrective_responsible_user = user_by_norm.get(_normalize_name(corrective_responsible_raw))
    await _upsert_corrective_action(
        db,
        report_id=qhse_report.id,
        description=_clean_text(_cell(row, index, "CorrectiveActionDescription")),
        limit_date=_parse_date(_cell(row, index, "CorrectiveActionLimitDate")),
        finished_date=_parse_date(_cell(row, index, "CorrectiveActionFinishedDate")),
        responsible_user_id=corrective_responsible_user.id if corrective_responsible_user else None,
        responsible_rank=_clean_text(_cell(row, index, "CorrectiveActionResponsibleRank")),
    )

    evaluation_responsible_raw = _clean_text(_cell(row, index, "EvaluationResponsiblePerson"))
    evaluation_responsible_user = user_by_norm.get(_normalize_name(evaluation_responsible_raw))
    await _upsert_root_cause(
        db,
        report_id=qhse_report.id,
        root_cause_text=_clean_text(_cell(row, index, "EvaluationRootCause")),
        preventative_action=_clean_text(_cell(row, index, "EvaluationPreventativeAction")),
        limit_date=_parse_date(_cell(row, index, "EvaluationLimitDate")),
        finished_date=_parse_date(_cell(row, index, "EvaluationFinishedDate")),
        responsible_user_id=(
            evaluation_responsible_user.id if evaluation_responsible_user else None
        ),
        responsible_rank=_clean_text(_cell(row, index, "EvaluationResponsibleRank")),
    )

    await db.flush()

    if test_match:
        report.flagged += 1
        verb = "IMPORTÉE" if kind == "created" else "MISE À JOUR"
        report.warnings.append(
            f"{origin} : motif « {test_match.group(0)} » — {verb} et marquée "
            "« test présumé » (RQ02). À confirmer ou écarter manuellement : « essai » "
            "et « test » sont aussi le vocabulaire des exercices ISM obligatoires."
        )
    return kind, qhse_report
