"""CSRF middleware behaviour."""
from __future__ import annotations

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from starlette.testclient import TestClient

from app.csrf import CSRF_COOKIE, CSRFMiddleware


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CSRFMiddleware)

    @app.get("/page", response_class=HTMLResponse)
    async def page(request: Request) -> str:
        return f'<input name="_csrf" value="{request.state.csrf_token}">'

    @app.post("/page")
    async def submit(
        request: Request,
        email: str = Form(...),
        password: str = Form(...),
    ) -> dict[str, str]:
        return {"email": email, "password": password}

    return app


def test_first_get_sets_csrf_cookie_and_state_matches() -> None:
    client = TestClient(_make_app())
    r = client.get("/page")
    assert r.status_code == 200
    cookie = r.cookies.get(CSRF_COOKIE)
    assert cookie is not None
    # The rendered HTML must contain the SAME token as the cookie value.
    assert f'value="{cookie}"' in r.text


def test_post_without_token_rejected() -> None:
    client = TestClient(_make_app())
    client.get("/page")  # pose le cookie
    # Submit without _csrf field
    r = client.post("/page", data={"email": "a@b.c", "password": "secret"})
    assert r.status_code == 403
    assert "CSRF validation failed" in r.text


def test_post_with_matching_csrf_field_succeeds() -> None:
    """Regression for the bug where reading form() in middleware consumed
    the body and FastAPI's Form(...) saw empty fields."""
    client = TestClient(_make_app())
    r = client.get("/page")
    token = r.cookies.get(CSRF_COOKIE)
    assert token

    r = client.post(
        "/page",
        data={"_csrf": token, "email": "alice@example.com", "password": "s3cret"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"email": "alice@example.com", "password": "s3cret"}


def test_post_with_matching_csrf_header_succeeds() -> None:
    client = TestClient(_make_app())
    r = client.get("/page")
    token = r.cookies.get(CSRF_COOKIE)
    assert token

    r = client.post(
        "/page",
        data={"email": "alice@example.com", "password": "s3cret"},
        headers={"x-csrf-token": token},
    )
    assert r.status_code == 200


def test_get_is_safe_method() -> None:
    client = TestClient(_make_app())
    r = client.get("/page")
    assert r.status_code == 200


def test_first_visit_form_works_via_state_token() -> None:
    """The hidden CSRF field must contain a token that matches the cookie
    set by the same response — even on the very first visit when no cookie
    was sent in the request."""
    client = TestClient(_make_app())
    r = client.get("/page")
    token_from_html = r.text.split('value="')[1].split('"')[0]
    token_from_cookie = r.cookies.get(CSRF_COOKIE)
    assert token_from_html == token_from_cookie

    # Now use the token to submit — it must be accepted.
    r = client.post(
        "/page",
        data={"_csrf": token_from_html, "email": "a@b.c", "password": "x"},
    )
    assert r.status_code == 200
