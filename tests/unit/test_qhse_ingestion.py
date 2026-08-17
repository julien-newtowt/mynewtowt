"""Tests — pipeline d'ingestion QHSE (Phase 0).

Couvre : import réussi + résolution navire/rapporteur/nettoyage IssuedPlace,
quarantaine (ClosedDate < IssuedDate, motif de test, navire non résolu), et
l'exécution du moteur de qualité générique (``validation_engine.run_rules``)
sur le scope ``qhse`` (RQ01-RQ03) — même moteur SQLite en mémoire que
``test_mrv_dataset.py``.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

import openpyxl
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — enregistre tous les modèles contre Base.metadata
from app.database import Base
from app.models.qhse import QhseReport
from app.models.vessel import Vessel
from app.services.qhse_ingestion import import_qhse_xlsx
from app.services.validation_engine import RULES, invalidate_cache, run_rules, seed_reference_data

_HEADER = [
    "Subject",
    "Code",
    "Description",
    "IssuedBy",
    "Contact",
    "IssuedPlace",
    "Grade",
    "IssuedDate",
    "ClosedDate",
    "VesselName",
    "DescriptionAddedDate",
    "DescriptionAddedBy",
]


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _rec):  # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    invalidate_cache()
    await seed_reference_data(session)
    invalidate_cache()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()
        invalidate_cache()


def _workbook(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(_HEADER)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def test_import_qhse_xlsx_happy_path_and_quarantine(db):
    db.add(Vessel(code="ANE", name="Anemos"))
    await db.flush()

    rows = [
        # Valide : à importer, avec artefact [Sync] sur IssuedPlace à nettoyer.
        [
            "Mooring near miss",
            None,
            "Rope slipped_x000D_during mooring ops",
            "TOWT MASTER ANEMOS",
            None,
            "At sea[Sync1]",
            "Near Miss / Hazard",
            datetime(2026, 1, 10),
            datetime(2026, 1, 15),
            "Anemos",
            None,
            None,
        ],
        # Quarantaine RQ01 : ClosedDate antérieure à IssuedDate.
        [
            "Essai de non conformité",
            None,
            "test record",
            "QA",
            None,
            None,
            "Non Conformity",
            datetime(2026, 2, 1),
            datetime(2026, 1, 31),
            "Anemos",
            None,
            None,
        ],
        # Quarantaine RQ03 : navire non reconnu.
        [
            "Deck slip",
            None,
            "slippery deck",
            "Crew",
            None,
            None,
            "Observation",
            datetime(2026, 3, 1),
            None,
            "Unknown Vessel",
            None,
            None,
        ],
    ]
    report = await import_qhse_xlsx(db, _workbook(rows))

    assert report.imported == 1
    assert report.skipped == 2
    assert len(report.errors) == 2
    assert any("RQ01" in e or "ClosedDate" in e for e in report.errors)
    assert any("non reconnu" in e for e in report.errors)

    saved = (await db.execute(select(QhseReport))).scalars().all()
    assert len(saved) == 1
    assert saved[0].issued_place == "At sea"  # artefact [Sync1] retiré
    assert saved[0].grade == "near_miss"
    assert saved[0].description is not None
    assert "_x000D_" not in saved[0].description


def test_qhse_rules_registered():
    assert {"RQ01", "RQ02", "RQ03"}.issubset(RULES.keys())


async def test_run_rules_qhse_scope_flags_bad_subject(db):
    """Vérifie que le moteur générique détecte bien RQ01/RQ03 sur un sujet
    duck-typé qui n'est jamais passé par l'ingestion (ex. saisie manuelle
    future) — la réutilisation de ``validation_engine`` fonctionne bien pour
    le scope ``qhse`` sans aucune modification du moteur."""
    good = QhseReport(
        vessel_id=1,
        subject="Normal report",
        grade="observation",
        issued_date=datetime(2026, 1, 1, tzinfo=UTC),
        closed_date=datetime(2026, 1, 5, tzinfo=UTC),
    )
    bad_dates = QhseReport(
        vessel_id=1,
        subject="Bad dates",
        grade="observation",
        issued_date=datetime(2026, 1, 5, tzinfo=UTC),
        closed_date=datetime(2026, 1, 1, tzinfo=UTC),
    )
    bad_vessel = QhseReport(
        vessel_id=None,
        subject="No vessel",
        grade="observation",
        issued_date=datetime(2026, 1, 1, tzinfo=UTC),
    )

    summary = await run_rules(db, "qhse", [good, bad_dates, bad_vessel])

    assert summary.total == 9  # 3 sujets x 3 règles (RQ01/RQ02/RQ03)
    assert summary.failed >= 2  # au moins RQ01 (bad_dates) + RQ03 (bad_vessel)


# ═══════════════════════════════════════════════════════════════════════════
#            Les deux défauts destructeurs — corrigés le 2026-08-17
# ═══════════════════════════════════════════════════════════════════════════
#
# 1. 🔴 `db.rollback()` dans la boucle d'import annulait la transaction ENTIÈRE,
#    donc toutes les lignes déjà importées du même fichier — sans décrémenter
#    `report.imported`. Une seule ligne malformée en fin de classeur détruisait
#    silencieusement tout l'import, avec un compte rendu de succès.
#
# 2. 🔴 Le filtre par mot-clé `\b(test|essai|demo)\b` écartait définitivement
#    toute non-conformité dont le sujet contenait ces mots. Or ils sont le
#    vocabulaire même de l'ISM : « Essai des embarcations de sauvetage »,
#    « Test du système d'alarme incendie » sont des exercices OBLIGATOIRES.
#    Ces non-conformités disparaissaient du registre.


def _row(subject, *, place=None, vessel="Anemos", day=10, grade="Observation"):
    return [
        subject,
        None,
        "desc",
        "Crew",
        None,
        place,
        grade,
        datetime(2026, 1, day),
        None,
        vessel,
        None,
        None,
    ]


def _explode_on(monkeypatch, sentinel: str):
    """Fait échouer l'import d'UNE ligne, repérée par son ``IssuedPlace``.

    ⚠️ Pourquoi un monkeypatch et non une valeur trop longue : SQLite **n'applique
    pas** les longueurs `String(n)`. Un sujet de 400 caractères passe donc en test
    alors qu'il échouerait sous Postgres — et le test réussirait **à vide**, sans
    jamais exercer le chemin d'erreur. C'est le piège qui a été rencontré en
    écrivant ces tests.
    """
    from app.services import qhse_ingestion

    original = qhse_ingestion._clean_place

    def _maybe_explode(raw):
        if raw == sentinel:
            raise RuntimeError("erreur inattendue simulée")
        return original(raw)

    monkeypatch.setattr(qhse_ingestion, "_clean_place", _maybe_explode)


async def test_a_failing_row_does_not_destroy_the_rows_already_imported(db, monkeypatch):
    """🔴 Défaut 1. Le cœur de la correction.

    Avant, le `rollback()` global emportait la première ligne — déjà importée et
    déjà comptée — dès qu'une ligne ultérieure échouait.
    """
    db.add(Vessel(code="ANE", name="Anemos"))
    await db.flush()
    _explode_on(monkeypatch, "BOOM")

    rows = [
        _row("Mooring near miss", day=10),
        _row("Ligne fautive", place="BOOM", day=11),
        _row("Deck slip", day=12),
    ]
    report = await import_qhse_xlsx(db, _workbook(rows))

    # Garde anti-vacuité : sans échec réel, ce test ne prouverait rien.
    assert report.skipped == 1, "aucune ligne n'a échoué — le test ne teste rien"
    assert any("inattendue" in e for e in report.errors)

    saved = {r.subject for r in (await db.execute(select(QhseReport))).scalars().all()}
    assert "Mooring near miss" in saved, "une ligne déjà importée a été détruite"
    assert "Deck slip" in saved, "les lignes suivantes n'ont pas été importées"
    assert "Ligne fautive" not in saved


async def test_the_counter_never_exceeds_what_is_actually_in_the_database(db, monkeypatch):
    """L'invariant qui rendait l'ancien défaut invisible : `imported` doit toujours
    égaler le nombre de lignes réellement présentes."""
    db.add(Vessel(code="ANE", name="Anemos"))
    await db.flush()
    _explode_on(monkeypatch, "BOOM")

    rows = [_row(f"Report {i}", day=i) for i in (1, 2, 3)]
    rows.append(_row("Ligne fautive", place="BOOM", day=20))
    report = await import_qhse_xlsx(db, _workbook(rows))

    saved = (await db.execute(select(QhseReport))).scalars().all()
    assert report.imported == len(saved) == 3
    assert report.skipped == 1


async def test_a_mandatory_ism_drill_is_imported_not_dropped(db):
    """🔴 Défaut 2. « Essai des embarcations de sauvetage » est un exercice ISM
    obligatoire — sa non-conformité doit ENTRER dans le registre."""
    db.add(Vessel(code="ANE", name="Anemos"))
    await db.flush()

    rows = [
        [
            "Essai des embarcations de sauvetage — bossoir grippé",
            None,
            "Le bossoir bâbord n'a pas pivoté lors de l'essai mensuel",
            "TOWT MASTER ANEMOS",
            None,
            "Fécamp",
            "Non Conformity",
            datetime(2026, 4, 3),
            None,
            "Anemos",
            None,
            None,
        ],
        [
            "Test du système d'alarme incendie",
            None,
            "Deux détecteurs muets en cale 2",
            "Crew",
            None,
            None,
            "Non Conformity",
            datetime(2026, 4, 4),
            None,
            "Anemos",
            None,
            None,
        ],
    ]
    report = await import_qhse_xlsx(db, _workbook(rows))

    saved = (await db.execute(select(QhseReport))).scalars().all()
    assert len(saved) == 2, "des non-conformités ISM légitimes ont été supprimées"
    assert report.imported == 2
    assert report.skipped == 0, "une non-conformité réglementaire a été écartée"

    # …mais marquées, et signalées : la décision d'écarter reste à un humain.
    assert report.flagged == 2
    assert len(report.warnings) == 2
    assert all(r.report_source == "suspected_test" for r in saved)
    assert any("essai" in w.lower() for w in report.warnings)


async def test_a_flagged_row_is_never_counted_as_skipped(db):
    """« Importée à confirmer » et « écartée » ne se confondent pas : les
    additionner masquerait la perte réelle."""
    db.add(Vessel(code="ANE", name="Anemos"))
    await db.flush()

    rows = [
        [
            "Essai gouvernail",
            None,
            "jeu anormal",
            "Crew",
            None,
            None,
            "Observation",
            datetime(2026, 5, 1),
            None,
            "Anemos",
            None,
            None,
        ],
        # Vraie perte : navire inconnu.
        [
            "Deck slip",
            None,
            "d",
            "Crew",
            None,
            None,
            "Observation",
            datetime(2026, 5, 2),
            None,
            "Navire Fantome",
            None,
            None,
        ],
    ]
    report = await import_qhse_xlsx(db, _workbook(rows))

    assert (report.imported, report.flagged, report.skipped) == (1, 1, 1)
    assert len(report.errors) == 1 and "non reconnu" in report.errors[0]
    assert len(report.warnings) == 1 and "IMPORTÉE" in report.warnings[0]


async def test_a_normal_report_is_not_flagged(db):
    """⚠️ Garde anti-sur-correction : ne pas marquer tout le monde."""
    db.add(Vessel(code="ANE", name="Anemos"))
    await db.flush()

    rows = [
        [
            "Mooring near miss",
            None,
            "rope slipped",
            "Crew",
            None,
            None,
            "Observation",
            datetime(2026, 6, 1),
            None,
            "Anemos",
            None,
            None,
        ]
    ]
    report = await import_qhse_xlsx(db, _workbook(rows))

    saved = (await db.execute(select(QhseReport))).scalars().all()
    assert report.flagged == 0 and report.warnings == []
    assert saved[0].report_source == "operational"


async def test_incoherent_dates_are_still_quarantined(db):
    """⚠️ RQ01 reste une quarantaine : une date de clôture antérieure à
    l'émission n'est pas un doute de vocabulaire, c'est une donnée impossible."""
    db.add(Vessel(code="ANE", name="Anemos"))
    await db.flush()

    rows = [
        [
            "Bad dates",
            None,
            "d",
            "Crew",
            None,
            None,
            "Observation",
            datetime(2026, 7, 5),
            datetime(2026, 7, 1),
            "Anemos",
            None,
            None,
        ]
    ]
    report = await import_qhse_xlsx(db, _workbook(rows))

    assert report.imported == 0 and report.skipped == 1
    assert (await db.execute(select(QhseReport))).scalars().all() == []


def test_the_loss_is_persisted_and_not_only_counted():
    """🔴 La perte doit survivre à la fermeture de l'onglet.

    L'ancienne trace n'écrivait que des nombres : le détail des lignes écartées
    vivait dans la réponse HTTP. Une non-conformité perdue devenait introuvable.
    """
    import inspect

    from app.routers import qhse_router

    src = inspect.getsource(qhse_router.qhse_import)
    assert "LIGNES NON IMPORTÉES" in src
    assert "report.errors" in src
    # Troncature annoncée : une liste coupée en silence ferait croire à un
    # inventaire complet des pertes.
    assert "tronqué" in src
    assert qhse_router._MAX_LOGGED_ROWS > 0


def test_the_result_screen_distinguishes_loss_from_doubt():
    """Trois compteurs, pas deux — et un avertissement quand il y a perte."""
    import re

    from app.templating import templates

    raw = templates.env.loader.get_source(templates.env, "staff/qhse/import_result.html")[0]
    src = re.sub(r"\{#.*?#\}", "", raw, flags=re.DOTALL)
    assert "report.flagged" in src
    assert "report.warnings" in src
    assert "qhse_import_result_loss_notice" in src
