"""Phase 2 de la reprise UX — espace client + hygiène.

Cf. docs/design/03-reprise-ux-legacy.md §10.3, constats K-1/K-2/K-3/K-5/K-7 +
§9.1 C-F1 (volet UI uniquement).

Couvre :
1. K-1a — changement de mot de passe client (`/me/account/password`) : refus
   sur mauvais mot de passe actuel, hash changé sur succès.
2. K-1b/c — plus aucun `href="#"` dans `account.html` (liens honnêtes).
3. K-2 — entrée « Facturation » dans la sidebar client.
4. K-3 — copie corrigée sur `/devis` dans les 5 catalogues i18n.
5. K-5 — repères de navigation (topbar) + bloc « En mer actuellement ».
6. C-F1 (volet UI) — garde `can_access` autour des liens sinistres côté bord.
7. K-7 — template mort `public/_layout.html` supprimé et non référencé.
"""

from __future__ import annotations

import pytest

from app.auth import hash_password, verify_password
from app.models.client_account import ClientAccount
from app.templating import templates
from tests.integration.conftest import FakeRequest


async def _client_account(db, email="pwd-phase2@example.test", password="CorrectHorsePassword123"):
    account = ClientAccount(
        email=email,
        hashed_password=hash_password(password),
        company_name="Acme Import",
    )
    db.add(account)
    await db.flush()
    return account


# ───────────────────── 1. K-1a — changement de mot de passe ─────────────────


@pytest.mark.asyncio
async def test_password_change_wrong_current_password_refused(db):
    from app.routers.client_auth_router import client_password_change

    account = await _client_account(db)
    old_hash = account.hashed_password

    resp = await client_password_change(
        FakeRequest(),
        current_password="NotTheRightOne123",
        new_password="BrandNewPassword123",
        confirm_password="BrandNewPassword123",
        client=account,
        db=db,
    )
    assert resp.status_code == 400
    # Le hash n'a pas bougé.
    assert account.hashed_password == old_hash
    assert verify_password("CorrectHorsePassword123", account.hashed_password)


@pytest.mark.asyncio
async def test_password_change_mismatched_confirmation_refused(db):
    from app.routers.client_auth_router import client_password_change

    account = await _client_account(db, email="pwd-mismatch@example.test")
    resp = await client_password_change(
        FakeRequest(),
        current_password="CorrectHorsePassword123",
        new_password="BrandNewPassword123",
        confirm_password="SomethingElse123456",
        client=account,
        db=db,
    )
    assert resp.status_code == 400
    assert verify_password("CorrectHorsePassword123", account.hashed_password)


@pytest.mark.asyncio
async def test_password_change_too_short_refused(db):
    from app.routers.client_auth_router import client_password_change

    account = await _client_account(db, email="pwd-short@example.test")
    resp = await client_password_change(
        FakeRequest(),
        current_password="CorrectHorsePassword123",
        new_password="short1",
        confirm_password="short1",
        client=account,
        db=db,
    )
    assert resp.status_code == 400
    assert verify_password("CorrectHorsePassword123", account.hashed_password)


@pytest.mark.asyncio
async def test_password_change_success_updates_hash_and_redirects(db):
    from app.routers.client_auth_router import client_password_change

    account = await _client_account(db, email="pwd-ok@example.test")

    resp = await client_password_change(
        FakeRequest(),
        current_password="CorrectHorsePassword123",
        new_password="BrandNewPassword123",
        confirm_password="BrandNewPassword123",
        client=account,
        db=db,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/me/account?password_changed=1"
    assert verify_password("BrandNewPassword123", account.hashed_password)
    assert not verify_password("CorrectHorsePassword123", account.hashed_password)


@pytest.mark.asyncio
async def test_password_change_rate_limited_after_five_failures(db):
    from app.routers.client_auth_router import client_password_change

    account = await _client_account(db, email="pwd-ratelimit@example.test")
    for _ in range(5):
        resp = await client_password_change(
            FakeRequest(),
            current_password="wrong-one-123456",
            new_password="BrandNewPassword123",
            confirm_password="BrandNewPassword123",
            client=account,
            db=db,
        )
        assert resp.status_code == 400

    # 6e tentative (même avec le bon mot de passe cette fois) → 429.
    resp = await client_password_change(
        FakeRequest(),
        current_password="CorrectHorsePassword123",
        new_password="BrandNewPassword123",
        confirm_password="BrandNewPassword123",
        client=account,
        db=db,
    )
    assert resp.status_code == 429
    assert verify_password("CorrectHorsePassword123", account.hashed_password)


def test_password_change_form_route_renders():
    """La page GET existe et étend bien le layout client."""
    src = templates.env.loader.get_source(templates.env, "client/password_change.html")[0]
    assert 'extends "client/_layout.html"' in src
    assert 'action="/me/account/password"' in src
    assert 'name="_csrf"' in src


# ───────────────────── 2. K-1b/c — liens honnêtes du compte ─────────────────


def test_account_template_has_no_dead_links():
    src = templates.env.loader.get_source(templates.env, "client/account.html")[0]
    assert 'href="#"' not in src
    assert 'href="/me/account/password"' in src
    # Export / suppression : honnête, sur demande, pas d'automatisation.
    assert src.count('href="/contact"') == 2


# ───────────────────── 3. K-2 — entrée Facturation ─────────────────────────


def test_sidebar_has_invoices_entry():
    src = templates.env.loader.get_source(templates.env, "client/_layout.html")[0]
    assert "/me/invoices" in src


def test_topbar_labels_cover_real_client_pages():
    src = templates.env.loader.get_source(templates.env, "client/_topbar.html")[0]
    for path in (
        "/me/invoices",
        "/me/track/",
        "/me/estimations",
        "/me/brand",
        "/me/notifications",
        "/me/messages",
        "/me/account/password",
    ):
        assert path in src


# ───────────────────── 4. K-3 — copie /devis corrigée ──────────────────────


@pytest.mark.parametrize("lang", ["fr", "en", "es", "pt-br", "vi"])
def test_devis_copy_no_longer_claims_pricing(lang):
    mod_name = lang.replace("-", "_")
    mod = __import__(f"app.i18n.{mod_name}", fromlist=["CATALOG"])
    catalog = mod.CATALOG

    assert "devis_hero_lead_client" in catalog
    assert "devis_logged_in_post" in catalog
    assert "devis_goto_estimations" in catalog

    hero = catalog["devis_hero_lead_client"]
    post = catalog["devis_logged_in_post"]
    # Le texte fautif (« s'applique si elle/vous en avez une/existe ») a disparu :
    # plus aucune formulation ne laisse entendre qu'un prix est calculé ici.
    for fault in (
        "s'applique si elle existe",
        "applies if it exists",
        "si vous en avez une",
        "si tienes una",
        "se houver",
        "nếu bạn có",
    ):
        assert fault not in hero
        assert fault not in post


def test_devis_form_links_logged_in_client_to_estimations():
    src = templates.env.loader.get_source(templates.env, "public/devis_form.html")[0]
    assert "/me/estimations" in src
    assert "devis_goto_estimations" in src


# ───────────────────── 5. K-5 — dashboard « En mer actuellement » ──────────


@pytest.mark.asyncio
async def test_dashboard_at_sea_block_only_for_at_sea_bookings(db):
    from datetime import UTC, datetime, timedelta

    from app.models.booking import Booking
    from app.models.leg import Leg
    from app.models.port import Port
    from app.models.vessel import Vessel
    from app.routers.client_dashboard_router import dashboard

    account = await _client_account(db, email="atsea@example.test")
    vessel = Vessel(name="Anemos", code="1")
    pol = Port(locode="FRFEC", name="Fécamp", country="FR")
    pod = Port(locode="BRSSO", name="Santos", country="BR")
    db.add_all([vessel, pol, pod])
    await db.flush()
    base = datetime(2026, 8, 1, tzinfo=UTC)
    eta = base + timedelta(days=12)
    leg = Leg(
        leg_code="1CFRBR6",
        vessel_id=vessel.id,
        departure_port_id=pol.id,
        arrival_port_id=pod.id,
        etd_ref=base,
        eta_ref=eta,
        etd=base,
        eta=eta,
    )
    db.add(leg)
    await db.flush()
    db.add_all(
        [
            Booking(
                reference="BK-ATSEA1",
                leg_id=leg.id,
                client_account_id=account.id,
                status="at_sea",
            ),
            Booking(
                reference="BK-CONFIRMED1",
                leg_id=leg.id,
                client_account_id=account.id,
                status="confirmed",
            ),
        ]
    )
    await db.flush()

    resp = await dashboard(FakeRequest(), client=account, db=db)
    ctx = resp.context
    crossings = ctx["at_sea_crossings"]
    assert len(crossings) == 1
    assert crossings[0]["reference"] == "BK-ATSEA1"
    assert crossings[0]["eta"] == eta
    assert crossings[0]["pol"].locode == "FRFEC"
    assert crossings[0]["pod"].locode == "BRSSO"


@pytest.mark.asyncio
async def test_dashboard_no_at_sea_crossings_is_empty_list(db):
    from app.routers.client_dashboard_router import dashboard

    account = await _client_account(db, email="noone-at-sea@example.test")
    resp = await dashboard(FakeRequest(), client=account, db=db)
    assert resp.context["at_sea_crossings"] == []


def test_dashboard_template_renders_at_sea_block():
    src = templates.env.loader.get_source(templates.env, "client/dashboard.html")[0]
    assert "at_sea_crossings" in src
    assert "/me/track/" in src
    assert "btn btn-primary btn-sm" in src


# ───────────────────── 6. C-F1 — garde claims côté bord (volet UI) ─────────


def test_onboard_navigation_guards_claims_links():
    src = templates.env.loader.get_source(templates.env, "staff/onboard/navigation.html")[0]
    assert 'can_access(_role, "claims")' in src
    # Les deux liens sinistres doivent être à l'intérieur de la garde, pas
    # seulement quelque part dans le fichier.
    guard_idx = src.index('can_access(_role, "claims")')
    endif_idx = src.index("{% endif %}", guard_idx)
    guarded_block = src[guard_idx:endif_idx]
    assert "/claims?leg_id=" in guarded_block
    assert "/claims/new?leg_id=" in guarded_block


# ───────────────────── 7. K-7 — template mort supprimé ─────────────────────


def test_dead_public_layout_removed_and_unreferenced():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    assert not (root / "app" / "templates" / "public" / "_layout.html").exists()

    for html_file in (root / "app" / "templates").rglob("*.html"):
        text = html_file.read_text(encoding="utf-8")
        if "public/_layout.html" in text and html_file.name != "_layout_v2.html":
            pytest.fail(f"{html_file} references the removed public/_layout.html")

    for py_file in (root / "app").rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        assert '"public/_layout.html"' not in text


# ─────────────── Revue PR #167 — invalidation de session (pwv) ───────────────

# Mot de passe factice à entropie volontairement quasi nulle (gitleaks) —
# sa seule contrainte est la longueur minimale d'inscription.
_PWV_DUMMY_PASSWORD = "x" * 11 + "A1"


@pytest.mark.asyncio
async def test_password_change_invalidates_older_sessions(db):
    """Un cookie émis avant le changement de mot de passe est rejeté après."""
    from app.auth import AuthInvalid, create_client_session, get_current_client
    from app.routers.client_auth_router import client_password_change

    account = await _client_account(db, email="pwv@example.test", password=_PWV_DUMMY_PASSWORD)
    account.is_verified = True
    await db.flush()
    old_token = create_client_session(account.id, account.hashed_password)
    # Sanity : le cookie versionné est accepté tant que le hash n'a pas changé.
    assert (await get_current_client(session_cookie=old_token, db=db)).id == account.id

    resp = await client_password_change(
        FakeRequest(),
        current_password=_PWV_DUMMY_PASSWORD,
        new_password="BrandNewPassword123",
        confirm_password="BrandNewPassword123",
        client=account,
        db=db,
    )
    assert resp.status_code == 303
    # L'ancien cookie (pwv du hash précédent) est invalidé…
    with pytest.raises(AuthInvalid):
        await get_current_client(session_cookie=old_token, db=db)
    # …le nouveau (posé sur la redirection) et un cookie re-émis passent.
    new_token = create_client_session(account.id, account.hashed_password)
    assert (await get_current_client(session_cookie=new_token, db=db)).id == account.id
    assert "set-cookie" in {k.lower() for k in resp.headers}


@pytest.mark.asyncio
async def test_legacy_session_without_pwv_still_accepted(db):
    """Compatibilité : un cookie sans ``pwv`` (antérieur) reste accepté."""
    from app.auth import create_client_session, get_current_client

    account = await _client_account(db, email="legacy-cookie@example.test")
    account.is_verified = True
    await db.flush()
    legacy_token = create_client_session(account.id)  # pas de hashed_password
    assert (await get_current_client(session_cookie=legacy_token, db=db)).id == account.id


@pytest.mark.asyncio
async def test_password_change_errors_are_translated(db):
    """Revue PR #167 : les erreurs suivent la langue du client (plus de FR figé)."""
    from app.routers.client_auth_router import client_password_change

    account = await _client_account(db, email="lang-en@example.test")
    account.language = "en"
    await db.flush()
    resp = await client_password_change(
        FakeRequest(),
        current_password="definitely-not-the-password",
        new_password="BrandNewPassword123",
        confirm_password="BrandNewPassword123",
        client=account,
        db=db,
    )
    assert resp.status_code == 400
    body = resp.body.decode()
    assert "Current password is incorrect." in body
    assert "Mot de passe actuel incorrect." not in body
