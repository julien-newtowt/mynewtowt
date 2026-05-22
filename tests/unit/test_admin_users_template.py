"""Garde anti-régression : les formulaires admin/users doivent porter le
token CSRF.

Bug historique : la création d'utilisateur 403-ait silencieusement car
le <form> n'incluait pas <input name="_csrf">. Le middleware CSRF exige
soit le header x-csrf-token, soit ce champ. Ce test re-rend le template
et vérifie la présence du token dans les 3 forms (create, toggle, reset).
"""
from __future__ import annotations

from types import SimpleNamespace

from app.templating import templates


class _FakeState:
    csrf_token = "test-csrf-token-123"


class _FakeURL:
    path = "/admin/users"


def _fake_request():
    # Starlette Request-like minimal pour le rendu Jinja
    return SimpleNamespace(
        state=_FakeState(),
        url=_FakeURL(),
        cookies={},
        headers={},
        query_params={},
        scope={"type": "http"},
    )


def _render(edit_user=None) -> str:
    tpl = templates.get_template("staff/admin/users.html")
    fake_vessel = SimpleNamespace(id=1, code="ANE", name="Anemos")
    fake_user = SimpleNamespace(
        id=1, username="admin", full_name="Admin", email="a@x.com",
        role="administrateur", language="fr", is_active=True,
        mfa_enabled=False, must_change_password=False, assigned_vessel_id=None,
    )
    return tpl.render(
        request=_fake_request(),
        user=fake_user,
        users=[fake_user],
        roles=("administrateur", "operation", "marins"),
        vessels=[fake_vessel],
        vessel_codes={1: "ANE"},
        languages=["fr", "en", "es", "pt-br", "vi"],
        edit_user=edit_user,
        lang="fr",
        brand={"nom_court": "NEWTOWT"},
        t=lambda *a, **k: "",
    )


def test_create_form_has_csrf_token():
    html = _render(edit_user=None)
    assert 'name="_csrf"' in html
    assert 'action="/admin/users"' in html


def test_create_form_has_language_and_vessel_fields():
    html = _render(edit_user=None)
    assert 'name="language"' in html
    assert 'name="assigned_vessel_id"' in html


def test_toggle_and_reset_forms_have_csrf():
    html = _render(edit_user=None)
    # Au moins 3 occurrences du champ CSRF : create + toggle + reset
    assert html.count('name="_csrf"') >= 3


def test_edit_form_prefills_and_has_csrf():
    target = SimpleNamespace(
        id=2, username="capt_dupont", full_name="Capt. Dupont",
        email="capt@x.com", role="marins", language="en",
        is_active=True, mfa_enabled=True, must_change_password=False,
        assigned_vessel_id=1,
    )
    html = _render(edit_user=target)
    assert 'name="_csrf"' in html
    assert 'action="/admin/users/2/edit"' in html
    assert "capt_dupont" in html  # username affiché (immutable)
