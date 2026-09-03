"""Support applicatif — archivage à 90 jours.

⚠️ Ces tests portent sur l'**ATTEIGNABILITÉ**, pas sur le filtrage. Le défaut du
module ``tickets`` n'est pas que ses tickets clos soient masqués : c'est qu'ils
deviennent **introuvables**. Son code annonce un écran « Archives » qui n'existe
pas — j'ai énuméré ses 8 routes. Une demande archivée ici doit rester
consultable, et ses pièces jointes téléchargeables.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.support import SupportTicket
from app.models.user import User
from app.services import support
from tests.integration.conftest import FakeRequest

ADMIN = SimpleNamespace(id=1, full_name="Admin", username="admin", role="administrateur")
MARIN = SimpleNamespace(id=2, full_name="Marin", username="marin", role="marins")
AUTRE = SimpleNamespace(id=3, full_name="Autre", username="autre", role="operation")

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


async def _seed_users(db) -> None:
    db.add_all(
        [
            User(id=2, username="marin", email="m@e.test", hashed_password="x", role="marins"),
            User(id=3, username="autre", email="a@e.test", hashed_password="x", role="operation"),
        ]
    )
    await db.flush()


async def _closed_since(db, days: int, *, reporter=MARIN, title="x") -> SupportTicket:
    """Demande clôturée il y a ``days`` jours (état posé directement).

    Les transitions sont couvertes ailleurs : ici on exerce le PRÉDICAT.
    """
    ticket = await support.create_request(
        db,
        reporter_id=reporter.id,
        reporter_role=reporter.role,
        kind="bug",
        severity="mineur",
        title=title,
        description="d",
    )
    ticket.status = "clos"
    ticket.closed_at = datetime.now(UTC) - timedelta(days=days)
    await db.flush()
    return ticket


# ─────────────────────── La frontière des 90 jours ───────────────────────


@pytest.mark.asyncio
async def test_at_89_days_still_in_the_current_view(db) -> None:
    from app.routers.support_router import list_archives, list_current

    await _seed_users(db)
    ticket = await _closed_since(db, 89, title="recente")

    current = await list_current(FakeRequest(), db=db, user=MARIN)
    assert ticket.reference in {r.reference for r in current.context["rows"]}

    archives = await list_archives(FakeRequest(), db=db, user=MARIN)
    assert ticket.reference not in {r.reference for r in archives.context["rows"]}


@pytest.mark.asyncio
async def test_at_91_days_moves_to_archives_and_stays_reachable(db) -> None:
    from app.routers.support_router import detail, list_archives, list_current

    await _seed_users(db)
    ticket = await _closed_since(db, 91, title="ancienne")

    current = await list_current(FakeRequest(), db=db, user=MARIN)
    assert ticket.reference not in {r.reference for r in current.context["rows"]}

    archives = await list_archives(FakeRequest(), db=db, user=MARIN)
    assert ticket.reference in {r.reference for r in archives.context["rows"]}

    # ── LE point : archivée ≠ perdue.
    resp = await detail(ticket.reference, FakeRequest(), db=db, user=MARIN)
    assert resp.status_code == 200
    assert resp.context["archived"] is True


@pytest.mark.asyncio
async def test_rejected_is_archived_from_its_own_timestamp(db) -> None:
    await _seed_users(db)
    ticket = await support.create_request(
        db,
        reporter_id=MARIN.id,
        reporter_role=MARIN.role,
        kind="question",
        severity="mineur",
        title="rejetee",
        description="d",
    )
    ticket.status = "rejete"
    ticket.rejected_at = datetime.now(UTC) - timedelta(days=91)
    await db.flush()
    assert support.is_archived(ticket) is True


@pytest.mark.asyncio
async def test_age_alone_never_archives(db) -> None:
    """Une demande OUVERTE depuis 2 ans reste dans la vue courante.

    Sinon on la perdrait de vue précisément quand elle traîne — l'inverse du
    service rendu.
    """
    from app.routers.support_router import list_archives, list_current

    await _seed_users(db)
    ticket = await support.create_request(
        db,
        reporter_id=MARIN.id,
        reporter_role=MARIN.role,
        kind="bug",
        severity="bloquant",
        title="oubliee",
        description="d",
    )
    ticket.status = "en_cours"
    ticket.triaged_at = datetime.now(UTC) - timedelta(days=730)
    await db.flush()

    current = await list_current(FakeRequest(), db=db, user=MARIN)
    assert ticket.reference in {r.reference for r in current.context["rows"]}
    archives = await list_archives(FakeRequest(), db=db, user=MARIN)
    assert archives.context["rows"] == []

    # ── LE cas où le critère terminal porte réellement : un `closed_at` ancien
    # SUBSISTE sur une demande qui n'est plus terminale. Un archivage fondé sur
    # l'âge seul l'engloutirait.
    #
    # Sans ce cas, le test passait même en remplaçant `terminal_at(ticket)` par
    # `ensure_utc(ticket.closed_at)` — constaté au sabotage : la garde était
    # vérifiée à vide, parce que la demande ci-dessus n'a pas de `closed_at`.
    stale = await support.create_request(
        db,
        reporter_id=MARIN.id,
        reporter_role=MARIN.role,
        kind="bug",
        severity="mineur",
        title="rouverte",
        description="d",
    )
    stale.closed_at = datetime.now(UTC) - timedelta(days=400)
    stale.status = "en_cours"
    await db.flush()
    assert support.is_archived(stale) is False

    archives = await list_archives(FakeRequest(), db=db, user=MARIN)
    assert stale.reference not in {r.reference for r in archives.context["rows"]}
    current = await list_current(FakeRequest(), db=db, user=MARIN)
    assert stale.reference in {r.reference for r in current.context["rows"]}


# ─────────────────────── Les deux expressions du prédicat ───────────────────────


@pytest.mark.asyncio
async def test_sql_and_python_archive_predicates_agree(db) -> None:
    """``is_archived`` (Python) et ``_archived_clause`` (SQL) disent la même chose.

    Deux expressions de la même règle = risque de divergence. Ce test est le
    garde-fou.
    """
    await _seed_users(db)
    for days in (1, 45, 89, 90, 91, 200):
        await _closed_since(db, days, title=f"j{days}")
    # Une non terminale, pour couvrir le cas où le critère d'état domine.
    open_one = await support.create_request(
        db,
        reporter_id=MARIN.id,
        reporter_role=MARIN.role,
        kind="bug",
        severity="mineur",
        title="ouverte",
        description="d",
    )
    open_one.closed_at = datetime.now(UTC) - timedelta(days=500)
    await db.flush()

    # Un SEUL instant de référence, passé aux deux prédicats. Sans cela chacun
    # appelle son propre ``datetime.now()`` : la demande plantée à exactement
    # 90 jours tombe alors d'un côté ou de l'autre du seuil selon les
    # microsecondes écoulées entre les deux appels, et le test devient flaky
    # sans qu'aucune divergence réelle n'existe.
    now = datetime.now(UTC)
    sql_archived = {
        r.reference
        for r in (await db.execute(select(SupportTicket).where(support._archived_clause(now))))
        .scalars()
        .all()
    }
    all_rows = list((await db.execute(select(SupportTicket))).scalars().all())
    py_archived = {r.reference for r in all_rows if support.is_archived(r, now=now)}
    assert (
        sql_archived == py_archived
    ), f"divergence SQL/Python — SQL={sorted(sql_archived)} Python={sorted(py_archived)}"


# ─────────────────────── Lecture seule ───────────────────────


@pytest.mark.asyncio
async def test_archived_refuses_comment_and_status_change(db) -> None:
    from app.routers.support_router import comment_action, status_action

    await _seed_users(db)
    ticket = await _closed_since(db, 91)

    with pytest.raises(HTTPException) as exc:
        await comment_action(
            ticket.reference, FakeRequest({"body": "encore un mot"}), db=db, user=MARIN
        )
    assert exc.value.status_code == 409

    with pytest.raises(HTTPException) as exc:
        await status_action(
            ticket.reference, FakeRequest({"status": "en_cours"}), db=db, user=ADMIN
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_terminal_but_recent_is_also_read_only(db) -> None:
    """Une demande clôturée hier est déjà en lecture seule — pas besoin d'attendre."""
    from app.routers.support_router import comment_action

    await _seed_users(db)
    ticket = await _closed_since(db, 1)
    with pytest.raises(HTTPException) as exc:
        await comment_action(ticket.reference, FakeRequest({"body": "x"}), db=db, user=MARIN)
    assert exc.value.status_code == 409


# ─────────────────────── Cloisonnement dans les archives ───────────────────────


@pytest.mark.asyncio
async def test_partition_applies_to_archives_too(db) -> None:
    from app.routers.support_router import list_archives

    await _seed_users(db)
    await _closed_since(db, 91, reporter=MARIN, title="du marin")
    await _closed_since(db, 91, reporter=AUTRE, title="de l'autre")

    resp = await list_archives(FakeRequest(), db=db, user=MARIN)
    assert {r.title for r in resp.context["rows"]} == {"du marin"}

    resp = await list_archives(FakeRequest(), db=db, user=ADMIN)
    assert {r.title for r in resp.context["rows"]} == {"du marin", "de l'autre"}


# ─────────────────────── Pièces jointes des archives ───────────────────────


@pytest.mark.asyncio
async def test_archived_attachments_remain_downloadable(db, monkeypatch, tmp_path) -> None:
    """L'archivage ne doit PAS devenir une perte d'accès silencieuse."""
    from app.routers.support_router import attachment_download
    from app.services import safe_files

    monkeypatch.setattr(safe_files.settings, "upload_dir", str(tmp_path))
    await _seed_users(db)
    ticket = await support.create_request(
        db,
        reporter_id=MARIN.id,
        reporter_role=MARIN.role,
        kind="bug",
        severity="mineur",
        title="avec piece",
        description="d",
    )
    att = await support.add_attachment(
        db, ticket, content=PNG, original_name="preuve.png", uploaded_by_id=MARIN.id
    )
    # On archive APRÈS avoir attaché (l'ajout est refusé sur une demande terminale).
    ticket.status = "clos"
    ticket.closed_at = datetime.now(UTC) - timedelta(days=120)
    await db.flush()

    assert support.is_archived(ticket) is True
    resp = await attachment_download(ticket.reference, att.id, db=db, user=MARIN)
    assert resp.status_code == 200
    # Et un tiers reste exclu, archives ou pas.
    with pytest.raises(HTTPException) as exc:
        await attachment_download(ticket.reference, att.id, db=db, user=AUTRE)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_attachment_refused_once_terminal(db, monkeypatch, tmp_path) -> None:
    from app.services import safe_files

    monkeypatch.setattr(safe_files.settings, "upload_dir", str(tmp_path))
    await _seed_users(db)
    ticket = await _closed_since(db, 1)
    with pytest.raises(support.SupportError):
        await support.add_attachment(
            db, ticket, content=PNG, original_name="tard.png", uploaded_by_id=MARIN.id
        )
