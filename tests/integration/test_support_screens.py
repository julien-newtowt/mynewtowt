"""Support applicatif — écrans, cloisonnement, tri réservé, pièces jointes.

Le module ``tickets`` n'a AUCUN test sur ses fonctions d'écriture (création,
transitions, assignation, commentaires, escalade SLA) : ses 11 tests portent
tous sur des fonctions pures. Ce fichier couvre l'inverse.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.activity_log import ActivityLog
from app.models.notification import Notification
from app.models.support import SupportTicket
from app.models.user import User
from app.services import support
from tests.integration.conftest import FakeRequest

ADMIN = SimpleNamespace(id=1, full_name="Admin Test", username="admin", role="administrateur")
MARIN = SimpleNamespace(id=2, full_name="Marin Test", username="marin", role="marins")
AUTRE = SimpleNamespace(id=3, full_name="Autre Test", username="autre", role="operation")


class FakeUpload:
    """Substitut de ``UploadFile`` : juste ``filename`` et ``read()``."""

    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self._content = content

    async def read(self) -> bytes:
        return self._content


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64  # magic number PNG valide


async def _seed_users(db) -> None:
    db.add_all(
        [
            User(
                id=2,
                username="marin",
                email="marin@example.test",
                hashed_password="x",
                role="marins",
            ),
            User(
                id=3,
                username="autre",
                email="autre@example.test",
                hashed_password="x",
                role="operation",
            ),
        ]
    )
    await db.flush()


async def _make(db, *, reporter=MARIN, **kw) -> SupportTicket:
    defaults = {
        "reporter_id": reporter.id,
        "reporter_role": reporter.role,
        "kind": "bug",
        "severity": "genant",
        "title": "Écran figé",
        "description": "Rien ne se passe au clic.",
    }
    defaults.update(kw)
    return await support.create_request(db, **defaults)


# ───────────────────────── Routes enregistrées ─────────────────────────


def test_routes_registered() -> None:
    from app.routers import support_router

    paths = {r.path for r in support_router.router.routes}
    assert "/support" in paths
    assert "/support/archives" in paths
    assert "/support/nouveau" in paths
    assert "/support/{ref}" in paths
    assert "/support/{ref}/statut" in paths
    assert "/support/{ref}/pieces/{att_id}/telecharger" in paths


def test_static_paths_declared_before_the_catch_all() -> None:
    """``/archives`` doit précéder ``/{ref}``, sinon il serait capturé.

    FastAPI résout dans l'ordre de déclaration : une inversion ferait chercher
    une demande dont la référence serait « archives ».
    """
    from app.routers import support_router

    order = [r.path for r in support_router.router.routes]
    assert order.index("/support/archives") < order.index("/support/{ref}")
    assert order.index("/support/nouveau") < order.index("/support/{ref}")


# ───────────────────────────── Création ─────────────────────────────


@pytest.mark.asyncio
async def test_reference_is_sequential(db) -> None:
    await _seed_users(db)
    refs = [(await _make(db, title=f"n{i}")).reference for i in range(3)]
    year = refs[0].split("-")[1]
    assert refs == [f"SUP-{year}-0001", f"SUP-{year}-0002", f"SUP-{year}-0003"]


@pytest.mark.asyncio
async def test_reference_does_not_recycle_after_deletion(db) -> None:
    """Supprimer une demande intermédiaire ne réattribue pas son numéro.

    C'est le défaut « la numérotation se recycle » : avec ``COUNT + 1``,
    supprimer 0001 alors que 0002 existe redonnerait 0002. On prend ``MAX + 1``.
    """
    await _seed_users(db)
    first = await _make(db, title="a")
    second = await _make(db, title="b")
    await db.delete(first)
    await db.flush()
    third = await _make(db, title="c")
    assert third.reference != second.reference
    assert third.seq_number == 3


@pytest.mark.asyncio
async def test_reference_skips_a_taken_number(db) -> None:
    """La boucle avance quand le numéro calculé est déjà pris.

    Sabotage : on force ``_next_seq_number`` à rendre d'abord un numéro occupé.
    Sans la pré-vérification, le flush échouerait — et un flush en échec met la
    session ENTIÈRE en état « rollback requis », sans possibilité de reprise
    interne (mesuré). D'où la vérification avant insertion.
    """
    await _seed_users(db)
    existing = await _make(db, title="occupant")
    calls: list[int] = []
    real = support._next_seq_number

    async def flaky(db_, year):
        calls.append(1)
        if len(calls) == 1:
            return existing.seq_number  # numéro déjà pris → IntegrityError
        return await real(db_, year)

    support._next_seq_number = flaky
    try:
        created = await _make(db, title="apres collision")
    finally:
        support._next_seq_number = real

    assert len(calls) >= 2, "la reprise n'a pas été exercée — test à vide"
    assert created.reference != existing.reference
    rows = list((await db.execute(select(SupportTicket))).scalars().all())
    assert len({r.reference for r in rows}) == len(rows)


@pytest.mark.asyncio
async def test_create_rejects_unknown_values(db) -> None:
    await _seed_users(db)
    with pytest.raises(support.SupportError):
        await _make(db, kind="inconnu")
    with pytest.raises(support.SupportError):
        await _make(db, severity="catastrophique")
    with pytest.raises(support.SupportError):
        await _make(db, title="   ")


@pytest.mark.asyncio
async def test_create_action_captures_technical_context(db) -> None:
    """Le contexte technique vient du SERVEUR, jamais du payload."""
    from app.routers.support_router import create_action

    await _seed_users(db)
    req = FakeRequest(
        {
            "kind": "bug",
            "severity": "bloquant",
            "title": "Bouton mort",
            "description": "Le bouton Valider ne fait rien.",
            "page_url": "/escale?leg_id=3",
        }
    )
    req.headers = {"user-agent": "Firefox/128", "referer": "/escale"}
    resp = await create_action(req, db=db, user=MARIN)
    assert resp.status_code == 303

    ticket = (await db.execute(select(SupportTicket))).scalars().one()
    assert ticket.page_url == "/escale?leg_id=3"
    assert ticket.user_agent == "Firefox/128"
    assert ticket.http_referer == "/escale"
    assert ticket.app_version  # settings.app_version
    # Le rôle est FIGÉ à la création, pas relu plus tard.
    assert ticket.reporter_role == "marins"
    assert ticket.reporter_id == MARIN.id


@pytest.mark.asyncio
async def test_create_action_sanitizes_hostile_page_url(db) -> None:
    from app.routers.support_router import create_action

    await _seed_users(db)
    req = FakeRequest(
        {
            "kind": "bug",
            "severity": "mineur",
            "title": "x",
            "description": "y",
            "page_url": "https://evil.example/steal",
        }
    )
    await create_action(req, db=db, user=MARIN)
    ticket = (await db.execute(select(SupportTicket))).scalars().one()
    assert ticket.page_url is None


@pytest.mark.asyncio
async def test_create_notifies_admin_and_audits(db) -> None:
    from app.routers.support_router import create_action

    await _seed_users(db)
    req = FakeRequest({"kind": "bug", "severity": "bloquant", "title": "T", "description": "D"})
    await create_action(req, db=db, user=MARIN)

    notes = list((await db.execute(select(Notification))).scalars().all())
    assert notes and notes[0].target_role == "administrateur"
    assert notes[0].link.startswith("/support/SUP-")

    logs = list(
        (
            await db.execute(
                select(ActivityLog).where(ActivityLog.action == "support_request_create")
            )
        )
        .scalars()
        .all()
    )
    assert logs and logs[0].module == "support"


# ───────────────────────── Écrans ─────────────────────────


@pytest.mark.asyncio
async def test_list_and_detail_render(db) -> None:
    from app.routers.support_router import detail, list_current

    await _seed_users(db)
    ticket = await _make(db)
    resp = await list_current(FakeRequest(), db=db, user=MARIN)
    assert resp.status_code == 200
    assert resp.template.name == "staff/support/list.html"

    resp = await detail(ticket.reference, FakeRequest(), db=db, user=MARIN)
    assert resp.status_code == 200
    assert resp.template.name == "staff/support/detail.html"
    assert ticket.reference in resp.body.decode()


@pytest.mark.asyncio
async def test_new_form_prefills_the_screen_it_came_from(db) -> None:
    from app.routers.support_router import new_form

    req = FakeRequest()
    req.query_params = {"from": "/mrv/parametres"}
    resp = await new_form(req, db=db, user=MARIN)
    assert resp.context["from_url"] == "/mrv/parametres"

    req.query_params = {"from": "https://evil.example"}
    resp = await new_form(req, db=db, user=MARIN)
    assert resp.context["from_url"] == ""


# ───────────────────── Cloisonnement de lecture ─────────────────────


@pytest.mark.asyncio
async def test_other_user_gets_404_not_403(db) -> None:
    """404 et non 403 : un 403 confirmerait que la référence existe."""
    from app.routers.support_router import detail

    await _seed_users(db)
    ticket = await _make(db, reporter=MARIN)
    with pytest.raises(HTTPException) as exc:
        await detail(ticket.reference, FakeRequest(), db=db, user=AUTRE)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_list_is_partitioned(db) -> None:
    from app.routers.support_router import list_current

    await _seed_users(db)
    await _make(db, reporter=MARIN, title="du marin")
    await _make(db, reporter=AUTRE, title="de l'autre")

    resp = await list_current(FakeRequest(), db=db, user=MARIN)
    titles = {r.title for r in resp.context["rows"]}
    assert titles == {"du marin"}

    resp = await list_current(FakeRequest(), db=db, user=ADMIN)
    titles = {r.title for r in resp.context["rows"]}
    assert titles == {"du marin", "de l'autre"}


@pytest.mark.asyncio
async def test_admin_sees_everything(db) -> None:
    from app.routers.support_router import detail

    await _seed_users(db)
    ticket = await _make(db, reporter=MARIN)
    resp = await detail(ticket.reference, FakeRequest(), db=db, user=ADMIN)
    assert resp.status_code == 200


# ───────────────────────── Tri réservé ─────────────────────────


@pytest.mark.asyncio
async def test_reporter_cannot_triage(db) -> None:
    from app.routers.support_router import status_action

    await _seed_users(db)
    ticket = await _make(db, reporter=MARIN)
    req = FakeRequest({"status": "en_cours"})
    with pytest.raises(HTTPException) as exc:
        await status_action(ticket.reference, req, db=db, user=MARIN)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_reporter_may_answer_and_reopen(db) -> None:
    """Les deux SEULES transitions du demandeur (spec §7)."""
    from app.routers.support_router import status_action

    await _seed_users(db)
    ticket = await _make(db, reporter=MARIN)
    await support.change_status(db, ticket, "en_cours")
    await support.change_status(db, ticket, "en_attente_utilisateur")

    resp = await status_action(
        ticket.reference, FakeRequest({"status": "en_cours"}), db=db, user=MARIN
    )
    assert resp.status_code == 303
    assert ticket.status == "en_cours"

    await support.change_status(db, ticket, "resolu")
    await status_action(ticket.reference, FakeRequest({"status": "en_cours"}), db=db, user=MARIN)
    assert ticket.status == "en_cours"


@pytest.mark.asyncio
async def test_reporter_cannot_close_even_after_resolution(db) -> None:
    from app.routers.support_router import status_action

    await _seed_users(db)
    ticket = await _make(db, reporter=MARIN)
    await support.change_status(db, ticket, "en_cours")
    await support.change_status(db, ticket, "resolu")
    with pytest.raises(HTTPException) as exc:
        await status_action(ticket.reference, FakeRequest({"status": "clos"}), db=db, user=MARIN)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_rejection_requires_a_reason(db) -> None:
    from app.routers.support_router import status_action

    await _seed_users(db)
    ticket = await _make(db, reporter=MARIN)
    with pytest.raises(HTTPException) as exc:
        await status_action(ticket.reference, FakeRequest({"status": "rejete"}), db=db, user=ADMIN)
    assert exc.value.status_code == 400

    resp = await status_action(
        ticket.reference,
        FakeRequest({"status": "rejete", "resolution": "doublon de SUP-2026-0001"}),
        db=db,
        user=ADMIN,
    )
    assert resp.status_code == 303
    assert ticket.status == "rejete"
    assert ticket.resolution


@pytest.mark.asyncio
async def test_assignment_is_admin_only(db) -> None:
    from app.routers.support_router import assign_action

    await _seed_users(db)
    ticket = await _make(db, reporter=MARIN)
    with pytest.raises(HTTPException) as exc:
        await assign_action(
            ticket.reference, FakeRequest({"assigned_to_id": "1"}), db=db, user=MARIN
        )
    assert exc.value.status_code == 403

    await assign_action(ticket.reference, FakeRequest({"assigned_to_id": "1"}), db=db, user=ADMIN)
    assert ticket.assigned_to_id == 1
    assert ticket.status == "en_cours"  # l'assignation prend en charge


# ───────────────────── Commentaires internes ─────────────────────


@pytest.mark.asyncio
async def test_internal_note_absent_from_the_rendered_page(db) -> None:
    """Une note interne ne doit PAS apparaître dans le HTML servi au demandeur.

    Le filtre du service est la seule barrière ; ce test vérifie le rendu réel,
    pas seulement le contexte.
    """
    from app.routers.support_router import comment_action, detail

    await _seed_users(db)
    ticket = await _make(db, reporter=MARIN)
    await comment_action(
        ticket.reference,
        FakeRequest({"body": "SECRET-INTERNE-XYZ", "is_internal": "1"}),
        db=db,
        user=ADMIN,
    )

    html_admin = (await detail(ticket.reference, FakeRequest(), db=db, user=ADMIN)).body.decode()
    assert "SECRET-INTERNE-XYZ" in html_admin

    html_marin = (await detail(ticket.reference, FakeRequest(), db=db, user=MARIN)).body.decode()
    assert "SECRET-INTERNE-XYZ" not in html_marin


@pytest.mark.asyncio
async def test_reporter_cannot_forge_an_internal_note(db) -> None:
    """``is_internal`` posté par un non-admin est ignoré."""
    from app.routers.support_router import comment_action

    await _seed_users(db)
    ticket = await _make(db, reporter=MARIN)
    await comment_action(
        ticket.reference,
        FakeRequest({"body": "visible", "is_internal": "1"}),
        db=db,
        user=MARIN,
    )
    # `db.refresh` ne charge PAS les relations : on repasse par le service, qui
    # applique `selectinload`.
    reloaded = await support.get_by_reference(db, ticket.reference)
    assert [c.is_internal for c in reloaded.comments] == [False]


# ───────────────────────── Pièces jointes ─────────────────────────


@pytest.mark.asyncio
async def test_attachment_accepted_and_stored_outside_the_repo(db, monkeypatch, tmp_path) -> None:
    from app.services import safe_files

    monkeypatch.setattr(safe_files.settings, "upload_dir", str(tmp_path))
    await _seed_users(db)
    ticket = await _make(db, reporter=MARIN)
    att = await support.add_attachment(
        db, ticket, content=PNG, original_name="capture.png", uploaded_by_id=MARIN.id
    )
    assert att.file_mime == "image/png"
    assert att.size_bytes == len(PNG)
    # Nom aléatoire sur disque : le nom fourni n'y figure pas.
    assert "capture" not in att.file_path
    assert (tmp_path / att.file_path).is_file()


@pytest.mark.asyncio
async def test_attachment_rejected_on_extension(db, monkeypatch, tmp_path) -> None:
    from app.services import safe_files

    monkeypatch.setattr(safe_files.settings, "upload_dir", str(tmp_path))
    await _seed_users(db)
    ticket = await _make(db, reporter=MARIN)
    with pytest.raises(safe_files.UploadRejected):
        await support.add_attachment(
            db, ticket, content=PNG, original_name="malveillant.exe", uploaded_by_id=1
        )


@pytest.mark.asyncio
async def test_attachment_cap_is_enforced(db, monkeypatch, tmp_path) -> None:
    from app.services import safe_files

    monkeypatch.setattr(safe_files.settings, "upload_dir", str(tmp_path))
    await _seed_users(db)
    ticket = await _make(db, reporter=MARIN)
    for i in range(support.MAX_ATTACHMENTS):
        await support.add_attachment(
            db, ticket, content=PNG, original_name=f"c{i}.png", uploaded_by_id=1
        )
    with pytest.raises(support.SupportError):
        await support.add_attachment(
            db, ticket, content=PNG, original_name="trop.png", uploaded_by_id=1
        )


@pytest.mark.asyncio
async def test_third_party_cannot_download(db, monkeypatch, tmp_path) -> None:
    """Le point de contrôle le plus sensible du module.

    Une capture d'écran peut contenir des données d'un autre module (finance,
    RH) : ``support:C`` ne suffit pas.
    """
    from app.routers.support_router import attachment_download
    from app.services import safe_files

    monkeypatch.setattr(safe_files.settings, "upload_dir", str(tmp_path))
    await _seed_users(db)
    ticket = await _make(db, reporter=MARIN)
    att = await support.add_attachment(
        db, ticket, content=PNG, original_name="rh.png", uploaded_by_id=MARIN.id
    )

    with pytest.raises(HTTPException) as exc:
        await attachment_download(ticket.reference, att.id, db=db, user=AUTRE)
    assert exc.value.status_code == 404

    ok = await attachment_download(ticket.reference, att.id, db=db, user=MARIN)
    assert ok.status_code == 200
    ok_admin = await attachment_download(ticket.reference, att.id, db=db, user=ADMIN)
    assert ok_admin.status_code == 200


@pytest.mark.asyncio
async def test_attachment_upload_via_creation_form(db, monkeypatch, tmp_path) -> None:
    """Une pièce refusée n'annule PAS la demande."""
    from app.routers.support_router import create_action
    from app.services import safe_files

    monkeypatch.setattr(safe_files.settings, "upload_dir", str(tmp_path))
    await _seed_users(db)

    class FormWithFiles(dict):
        def getlist(self, key):
            return self.get(key, [])

    form = FormWithFiles(
        {
            "kind": "bug",
            "severity": "genant",
            "title": "avec pieces",
            "description": "d",
            "attachments": [
                FakeUpload("ok.png", PNG),
                FakeUpload("refuse.exe", PNG),
            ],
        }
    )
    req = FakeRequest()
    req._form = form
    resp = await create_action(req, db=db, user=MARIN)
    assert resp.status_code == 303

    ref = (await db.execute(select(SupportTicket.reference))).scalars().one()
    ticket = await support.get_by_reference(db, ref)
    names = [a.original_name for a in ticket.attachments]
    assert names == ["ok.png"]  # la demande survit, le .exe est écarté
    log = next(
        iter(
            (
                await db.execute(
                    select(ActivityLog).where(ActivityLog.action == "support_request_create")
                )
            )
            .scalars()
            .all()
        )
    )
    assert "refuse.exe" in (log.detail or "")  # la perte est TRACÉE, pas silencieuse


@pytest.mark.asyncio
async def test_attachment_add_route_with_a_real_uploadfile(db, monkeypatch, tmp_path) -> None:
    """La route d'ajout après création, avec un vrai ``UploadFile``.

    Le routeur exige `isinstance(upload, UploadFile)` — un faux objet ne suffit
    donc pas ici, et c'est voulu : `hasattr` ne restreignait pas le type et
    laissait passer une valeur texte.
    """
    import io as _io

    from fastapi import UploadFile

    from app.routers.support_router import attachment_add
    from app.services import safe_files

    monkeypatch.setattr(safe_files.settings, "upload_dir", str(tmp_path))
    await _seed_users(db)
    ticket = await _make(db, reporter=MARIN)

    upload = UploadFile(file=_io.BytesIO(PNG), filename="apres-coup.png")
    req = FakeRequest({"attachment": upload})
    resp = await attachment_add(ticket.reference, req, db=db, user=MARIN)
    assert resp.status_code == 303

    reloaded = await support.get_by_reference(db, ticket.reference)
    assert [a.original_name for a in reloaded.attachments] == ["apres-coup.png"]


@pytest.mark.asyncio
async def test_attachment_add_refuses_a_text_field(db, monkeypatch, tmp_path) -> None:
    """Un champ texte envoyé là où un fichier est attendu est refusé, pas subi."""
    from app.routers.support_router import attachment_add
    from app.services import safe_files

    monkeypatch.setattr(safe_files.settings, "upload_dir", str(tmp_path))
    await _seed_users(db)
    ticket = await _make(db, reporter=MARIN)

    req = FakeRequest({"attachment": "pas-un-fichier.png"})
    with pytest.raises(HTTPException) as exc:
        await attachment_add(ticket.reference, req, db=db, user=MARIN)
    assert exc.value.status_code == 400


# ───────────────────────── Petit tableau de bord (admin) ─────────────────────────


@pytest.mark.asyncio
async def test_stats_blocking_and_oldest(db) -> None:
    """Les deux compteurs ajoutés au tableau de bord : bloquantes ouvertes, ancienneté."""
    await _seed_users(db)

    await _make(db, severity="bloquant")

    old = await _make(db, severity="genant")
    old.created_at = datetime.now(UTC) - timedelta(days=10, hours=1)
    await db.flush()

    closed = await _make(db, severity="mineur")
    closed.status = "clos"
    closed.closed_at = datetime.now(UTC)
    await db.flush()

    result = await support.stats(db)
    assert result.open_count == 2
    assert result.blocking_open == 1
    assert result.oldest_open_days == 10


@pytest.mark.asyncio
async def test_stats_oldest_is_none_without_open_requests(db) -> None:
    """Aucune demande ouverte : l'ancienneté n'a pas de sens, pas un faux zéro."""
    await _seed_users(db)
    closed = await _make(db, severity="mineur")
    closed.status = "clos"
    closed.closed_at = datetime.now(UTC)
    await db.flush()

    result = await support.stats(db)
    assert result.open_count == 0
    assert result.blocking_open == 0
    assert result.oldest_open_days is None
