"""Tests — indicateurs du tableau de bord QHSE (``app.services.qhse_kpi``).

Base SQLite en mémoire, même patron que ``test_qhse_ingestion.py``. Les
signalements sont construits directement (pas via l'ingestion) pour isoler
le calcul des indicateurs de tout comportement du pipeline d'import.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — enregistre tous les modèles contre Base.metadata
from app.database import Base
from app.models.qhse import CorrectiveAction, QhseReport, RootCauseEvaluation
from app.models.user import User
from app.models.vessel import Vessel
from app.services.qhse_kpi import (
    ISSUER_ORIGIN_EXTERNAL,
    ISSUER_ORIGIN_ONBOARD,
    ISSUER_ORIGIN_SHORE,
    ISSUER_ORIGIN_UNKNOWN,
    ISSUER_ORIGINS,
    build_dashboard,
    build_quality_report,
    classify_issuer_origin,
    list_vessels_with_reports,
    trend_bars,
)

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


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
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def _seed_vessels(db) -> tuple[Vessel, Vessel]:
    anemos = Vessel(code="ANE", name="Anemos")
    artemis = Vessel(code="ART", name="Artemis")
    db.add_all([anemos, artemis])
    await db.flush()
    return anemos, artemis


async def _report(
    db,
    vessel_id,
    *,
    subject="R",
    grade="near_miss",
    issued=None,
    closed=None,
    issued_by_raw=None,
    report_source="operational",
):
    r = QhseReport(
        vessel_id=vessel_id,
        subject=subject,
        grade=grade,
        issued_date=issued or NOW,
        closed_date=closed,
        issued_by_raw=issued_by_raw,
        report_source=report_source,
    )
    db.add(r)
    await db.flush()
    return r


# ═══════════════════════════════════════ Cas vide


async def test_empty_fleet_returns_zeroed_dashboard_not_a_crash(db):
    dash = await build_dashboard(db, now=NOW)
    assert dash.total_reports == 0
    assert dash.open_count == 0
    assert dash.field_completeness_pct is None  # pas un 0% qui mentirait
    assert dash.corrective_on_time_pct is None
    assert len(dash.trend) == 12
    assert all(p.count == 0 for p in dash.trend)
    assert dash.open_items == []


# ═══════════════════════════════════════ Filtre navire


async def test_vessel_filter_isolates_the_right_fleet_slice(db):
    anemos, artemis = await _seed_vessels(db)
    await _report(db, anemos.id, subject="A1")
    await _report(db, anemos.id, subject="A2")
    await _report(db, artemis.id, subject="B1")

    fleet = await build_dashboard(db, now=NOW)
    only_anemos = await build_dashboard(db, vessel_id=anemos.id, now=NOW)

    assert fleet.total_reports == 3
    assert only_anemos.total_reports == 2


async def test_list_vessels_with_reports_excludes_vessels_without_data(db):
    anemos, artemis = await _seed_vessels(db)
    await _report(db, anemos.id)

    vessels = await list_vessels_with_reports(db)
    assert [v.id for v in vessels] == [anemos.id]  # Artemis n'a aucun signalement


# ═══════════════════════════════════════ Répartition par grade


async def test_grade_counts_cover_all_six_grades_even_at_zero(db):
    anemos, _ = await _seed_vessels(db)
    await _report(db, anemos.id, grade="accident")
    await _report(db, anemos.id, grade="accident")
    await _report(db, anemos.id, grade="observation")

    dash = await build_dashboard(db, now=NOW)
    tally = {g.grade: g.count for g in dash.grade_counts}
    assert tally == {
        "accident": 2,
        "non_conformity": 0,
        "near_miss": 0,
        "observation": 1,
        "deficiency": 0,
        "casualty": 0,
    }
    assert sum(tally.values()) == dash.total_reports


# ═══════════════════════════════════════ Ouverts / ancienneté


async def test_open_items_sorted_oldest_first_with_correct_age(db):
    anemos, _ = await _seed_vessels(db)
    old = await _report(db, anemos.id, subject="Old", issued=NOW - timedelta(days=30))
    recent = await _report(db, anemos.id, subject="Recent", issued=NOW - timedelta(days=2))
    await _report(db, anemos.id, subject="Closed", issued=NOW - timedelta(days=100), closed=NOW)

    dash = await build_dashboard(db, now=NOW)
    assert dash.open_count == 2  # le clos n'y est pas
    assert [item.id for item in dash.open_items] == [old.id, recent.id]
    assert dash.open_items[0].days_open == 30
    assert dash.open_items[1].days_open == 2


async def test_open_items_capped_at_ten(db):
    anemos, _ = await _seed_vessels(db)
    for i in range(15):
        await _report(db, anemos.id, subject=f"R{i}", issued=NOW - timedelta(days=i + 1))

    dash = await build_dashboard(db, now=NOW)
    assert dash.open_count == 15
    assert len(dash.open_items) == 10


# ═══════════════════════════════════════ Tendance 12 mois


async def test_trend_has_zero_for_months_without_reports(db):
    anemos, _ = await _seed_vessels(db)
    await _report(db, anemos.id, issued=NOW)  # mois courant seulement

    dash = await build_dashboard(db, now=NOW)
    assert len(dash.trend) == 12
    assert dash.trend[-1].count == 1  # dernier point = mois courant
    assert all(p.count == 0 for p in dash.trend[:-1])


async def test_trend_bars_geometry_is_sane(db):
    anemos, _ = await _seed_vessels(db)
    await _report(db, anemos.id, issued=NOW)
    dash = await build_dashboard(db, now=NOW)

    bars, meta = trend_bars(dash.trend)
    assert len(bars) == 12
    assert meta["width"] > 0 and meta["height"] > 0
    assert all(b["width"] >= 0 and b["height"] >= 0 for b in bars)
    # Le seul mois non-vide doit produire la barre la plus haute (ou nulle
    # si tout est à zéro — ici il y a exactement 1 signalement).
    assert max(b["height"] for b in bars) > 0


# ═══════════════════════════════════════ R1 — complétude


async def test_completeness_is_none_shaped_correctly_per_report(db):
    anemos, _ = await _seed_vessels(db)

    # 0/3 : rien de renseigné. Seul son effet (exister en base) compte ici.
    await _report(db, anemos.id, subject="bare")

    # 3/3 : cause racine, description corrective, responsable identifié.
    full = await _report(db, anemos.id, subject="full")
    db.add(CorrectiveAction(report_id=full.id, description="fixed", responsible_user_id=None))
    db.add(RootCauseEvaluation(report_id=full.id, root_cause_text="cause"))
    await db.flush()
    # responsable porté par le workflow corrective — suffit pour les 3/3
    action = (
        await db.execute(select(CorrectiveAction).where(CorrectiveAction.report_id == full.id))
    ).scalar_one()
    action.responsible_user_id = None  # laissé None volontairement : voir test suivant

    dash = await build_dashboard(db, now=NOW)
    # bare=0/3, full=2/3 (pas de responsable ici) -> moyenne = (0 + 2/3)/2 = 1/3 = 33.3%
    assert dash.field_completeness_pct == Decimal("33.3")


# ═══════════════════════════════════════ C1 — action corrective à temps


async def test_c1_excludes_unfinished_actions_from_denominator(db):
    """23 % des actions du jeu de données réel n'ont pas de date de
    finalisation (cahier des charges §3.4/§5.7) — elles doivent rester
    hors du dénominateur, jamais comptées comme "en retard"."""
    anemos, _ = await _seed_vessels(db)

    on_time = await _report(db, anemos.id, subject="on_time")
    db.add(
        CorrectiveAction(
            report_id=on_time.id,
            limit_date=date(2026, 3, 1),
            finished_date=date(2026, 2, 20),
        )
    )
    late = await _report(db, anemos.id, subject="late")
    db.add(
        CorrectiveAction(
            report_id=late.id,
            limit_date=date(2026, 3, 1),
            finished_date=date(2026, 3, 15),
        )
    )
    unfinished = await _report(db, anemos.id, subject="unfinished")
    db.add(CorrectiveAction(report_id=unfinished.id, limit_date=date(2026, 3, 1)))
    await db.flush()

    dash = await build_dashboard(db, now=NOW)
    assert dash.corrective_total_count == 3
    assert dash.corrective_finished_count == 2  # pas les 3 — l'inachevée est exclue
    assert dash.corrective_on_time_pct == 50.0  # 1 sur 2 finalisées, pas 1 sur 3


# ═══════════════════════════════════════ C2 — dénominateur = tous les rapports


async def test_c2_denominator_is_all_reports_not_just_evaluations_created(db):
    """Cahier des charges §5.7 C2 : le dénominateur est le TOTAL des
    rapports, pas seulement ceux qui ont une évaluation créée — sinon un
    rapport sans aucune évaluation disparaîtrait du calcul au lieu de
    compter comme « non complétée »."""
    anemos, _ = await _seed_vessels(db)

    evaluated = await _report(db, anemos.id, subject="evaluated")
    db.add(
        RootCauseEvaluation(report_id=evaluated.id, root_cause_text="x", finished_date=NOW.date())
    )
    await _report(db, anemos.id, subject="no_evaluation_at_all")
    await db.flush()

    dash = await build_dashboard(db, now=NOW)
    assert dash.total_reports == 2
    assert dash.root_cause_finished_count == 1
    assert dash.root_cause_completion_pct == 50.0  # 1/2, pas 1/1


# ═══════════════════════════════════════ Q2 — origine de l'émetteur

# Chaînes d'émetteur RÉELLES, relevées sur les exports FMS du 2026-09-04
# (188 lignes, Anemos + Artemis). Le test vaut par ces valeurs-là : une
# heuristique qui ne les classe pas correctement ne sert à rien.
REAL_ISSUERS = {
    "TOWT MASTER ANEMOS": ISSUER_ORIGIN_ONBOARD,
    "TOWT C/O ANEMOS": ISSUER_ORIGIN_ONBOARD,
    "TOWT C/E ANEMOS": ISSUER_ORIGIN_ONBOARD,
    "Anemos Chief Engineer": ISSUER_ORIGIN_ONBOARD,
    "TOWT ARTEMIS C/O": ISSUER_ORIGIN_ONBOARD,
    "TOWT ARTEMIS": ISSUER_ORIGIN_ONBOARD,  # nom de navire seul
    "Artemis": ISSUER_ORIGIN_ONBOARD,
    "TOWT COMPANY": ISSUER_ORIGIN_SHORE,
    "Centre de Sécurité des Navires de Brest": ISSUER_ORIGIN_EXTERNAL,
    "Transport Canada": ISSUER_ORIGIN_EXTERNAL,
    "TRANSPORT CANADA [Sync]": ISSUER_ORIGIN_EXTERNAL,
    "USCG": ISSUER_ORIGIN_EXTERNAL,
    "TOWT": ISSUER_ORIGIN_UNKNOWN,  # trop ambigu — jamais attribué d'office
}


def test_classification_of_every_real_issuer_string():
    vessel_names = frozenset({"anemos", "artemis"})
    for raw, expected in REAL_ISSUERS.items():
        assert classify_issuer_origin(issued_by_raw=raw, vessel_names=vessel_names) == expected, raw


def test_classification_is_accent_and_case_insensitive():
    """« Centre de Sécurité » et « CENTRE DE SECURITE » sont le même émetteur."""
    for raw in (
        "Centre de Sécurité des Navires de Brest",
        "CENTRE DE SECURITE DES NAVIRES DE BREST",
        "centre de securite des navires",
    ):
        assert classify_issuer_origin(issued_by_raw=raw) == ISSUER_ORIGIN_EXTERNAL


def test_empty_or_missing_issuer_is_undetermined_never_onboard():
    for raw in (None, "", "   "):
        assert classify_issuer_origin(issued_by_raw=raw) == ISSUER_ORIGIN_UNKNOWN


def test_external_authority_wins_over_a_vessel_name_in_the_same_string():
    """Un contrôle par l'État du port cite le navire ; ce n'est pas le navire
    qui signale. L'ordre des motifs est donc porteur de sens, pas cosmétique."""
    origin = classify_issuer_origin(
        issued_by_raw="Transport Canada — inspection Anemos",
        vessel_names=frozenset({"anemos"}),
    )
    assert origin == ISSUER_ORIGIN_EXTERNAL


def test_identified_person_links_take_precedence_over_free_text():
    """Un nom réellement rapproché du référentiel est plus sûr qu'un motif."""
    assert classify_issuer_origin(issued_by_raw=None, has_crew_link=True) == ISSUER_ORIGIN_ONBOARD
    assert classify_issuer_origin(issued_by_raw=None, has_user_link=True) == ISSUER_ORIGIN_SHORE


async def test_origin_counts_cover_all_origins_even_at_zero(db):
    anemos, _ = await _seed_vessels(db)
    await _report(db, anemos.id, subject="A", issued_by_raw="TOWT MASTER ANEMOS")
    await _report(db, anemos.id, subject="B", issued_by_raw="TOWT COMPANY")
    await _report(db, anemos.id, subject="C", issued_by_raw="Transport Canada")

    dash = await build_dashboard(db, now=NOW)
    tally = {o.origin: o.count for o in dash.origin_counts}
    assert set(tally) == set(ISSUER_ORIGINS)  # `indetermine` présente même à 0
    assert tally == {
        ISSUER_ORIGIN_ONBOARD: 1,
        ISSUER_ORIGIN_SHORE: 1,
        ISSUER_ORIGIN_EXTERNAL: 1,
        ISSUER_ORIGIN_UNKNOWN: 0,
    }
    assert sum(tally.values()) == dash.total_reports


async def test_empty_fleet_still_exposes_the_four_origins(db):
    dash = await build_dashboard(db, now=NOW)
    assert [o.origin for o in dash.origin_counts] == list(ISSUER_ORIGINS)
    assert all(o.count == 0 for o in dash.origin_counts)


# ═══════════════════════════════════════ Qualité — quoi corriger


async def test_quality_report_names_each_issue(db):
    anemos, _ = await _seed_vessels(db)
    r = await _report(db, anemos.id, subject="incomplet")

    quality = await build_quality_report(db)
    assert quality.total_reports == 1
    assert quality.total_flagged == 1
    item = quality.items[0]
    assert item.id == r.id
    assert item.vessel_code == "ANE"
    # Aucun workflow créé : les manques sont nommés, pas agrégés. Le
    # responsable n'y figure PAS — constat structurel compté à part.
    assert item.issues == ["missing_root_cause", "missing_corrective_description"]
    assert quality.issue_counts["missing_root_cause"] == 1
    assert quality.responsible_missing_count == 1


async def test_missing_responsible_never_puts_a_row_in_the_list(db):
    """Le champ manque sur 100 % des lignes de l'export « historique par
    navire » (aucune colonne de responsable) : le lister par ligne mettait les
    90 signalements réels dans la liste, dont 53 sans rien d'autre à corriger.
    Compté, expliqué, jamais listé à ce titre."""
    anemos, _ = await _seed_vessels(db)
    complete_but_unowned = await _report(db, anemos.id, subject="sans responsable")
    db.add(CorrectiveAction(report_id=complete_but_unowned.id, description="fait"))
    db.add(RootCauseEvaluation(report_id=complete_but_unowned.id, root_cause_text="cause"))
    await db.flush()

    quality = await build_quality_report(db)
    assert quality.responsible_missing_count == 1
    assert quality.total_flagged == 0  # rien d'autre à corriger sur cette ligne
    assert quality.items == []
    assert "missing_responsible" not in quality.issue_counts


async def test_quality_report_excludes_a_complete_report(db):
    anemos, _ = await _seed_vessels(db)
    # `responsible_user_id` porte une FK réellement appliquée sous SQLite :
    # l'utilisateur doit exister, un id arbitraire échouerait.
    owner = User(
        username="qhse",
        email="qhse@example.test",
        hashed_password="x",
        role="administrateur",
        full_name="Resp QHSE",
    )
    db.add(owner)
    await db.flush()

    full = await _report(db, anemos.id, subject="complet")
    db.add(CorrectiveAction(report_id=full.id, description="fait", responsible_user_id=None))
    # Responsable posé sur l'évaluation : l'accountability existe quelque part,
    # pas nécessairement sur les deux workflows (même règle que R1).
    db.add(
        RootCauseEvaluation(
            report_id=full.id, root_cause_text="cause", responsible_user_id=owner.id
        )
    )
    await db.flush()

    quality = await build_quality_report(db)
    assert quality.total_reports == 1
    assert quality.total_flagged == 0
    assert quality.items == []


async def test_suspected_test_is_listed_first_because_it_needs_a_decision(db):
    """Les autres motifs demandent une saisie ; celui-là demande un arbitrage."""
    anemos, _ = await _seed_vessels(db)
    await _report(db, anemos.id, subject="ancien", issued=NOW - timedelta(days=100))
    await _report(
        db,
        anemos.id,
        subject="test presume",
        issued=NOW - timedelta(days=1),
        report_source="suspected_test",
    )

    quality = await build_quality_report(db)
    assert quality.items[0].subject == "test presume"
    assert "suspected_test" in quality.items[0].issues
    assert quality.issue_counts["suspected_test"] == 1


async def test_closed_before_issued_is_detected(db):
    anemos, _ = await _seed_vessels(db)
    await _report(
        db,
        anemos.id,
        subject="dates incoherentes",
        issued=NOW,
        closed=NOW - timedelta(days=5),
    )

    quality = await build_quality_report(db)
    assert "closed_before_issued" in quality.items[0].issues
    assert quality.issue_counts["closed_before_issued"] == 1


async def test_quality_report_respects_the_vessel_filter(db):
    anemos, artemis = await _seed_vessels(db)
    await _report(db, anemos.id, subject="A")
    await _report(db, artemis.id, subject="B")

    fleet = await build_quality_report(db)
    only_artemis = await build_quality_report(db, vessel_id=artemis.id)
    assert fleet.total_reports == 2
    assert only_artemis.total_reports == 1
    assert [it.vessel_code for it in only_artemis.items] == ["ART"]


async def test_quality_report_on_empty_fleet_returns_zeroed_counts(db):
    quality = await build_quality_report(db)
    assert quality.total_reports == 0
    assert quality.total_flagged == 0
    assert quality.items == []
    # Les compteurs existent quand même — un motif absent vaut 0, pas None.
    assert quality.issue_counts["suspected_test"] == 0
