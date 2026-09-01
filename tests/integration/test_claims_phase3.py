"""Claims — reprise UX Phase 3 (docs/design/03-reprise-ux-legacy.md §9.1).

Couvre les constats C-F4/C-F5/C-F6 :
  - C-F5 : badges de statut stylés (plus de classe ``badge-{{ claim.status }}``
    inexistante — même mapping que ``claims/index.html``).
  - C-F6 : formulaires secondaires (assurance, provision, position cale)
    repliés dans ``<details class="form-disclosure">``.
  - C-F4 : mutations répétitives (note, statut) en HTMX — 204 + HX-Trigger
    ``claimsRefresh`` sous ``hx-request``, 303 classique sinon (même motif
    que le cockpit escale, ``escale_router._mutation_response``).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models.claim import Claim, ClaimTimelineEntry
from app.routers.claims_router import claim_add_note, claim_update_status
from tests.integration.conftest import FakeRequest


async def _claim(db, **overrides) -> Claim:
    defaults = {
        "reference": "CLM-2026-0001",
        "claim_type": "cargo",
        "title": "Avarie cale 2",
        "description": "Eau de mer",
        "status": "open",
        "occurred_at": datetime(2026, 4, 5, 10, 0, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    c = Claim(**defaults)
    db.add(c)
    await db.flush()
    return c


# ───────────────────────── Template — marqueurs structurants ─────────────────────────


def test_claims_detail_template_markers():
    """Le detail ne référence plus la classe inexistante et porte les marqueurs
    du motif HTMX (conteneur rafraîchi + formulaires secondaires repliés)."""
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/claims/detail.html")[0]
    # C-F5 — plus de badge-{{ claim.status }} littéral (classe CSS inexistante
    # pour 5 statuts sur 6) : le mapping conditionnel de l'index est repris.
    assert "badge-{{ claim.status }}" not in src
    assert "badge-overdue" in src
    assert "badge-completed" in src
    assert "badge-error" in src
    assert "badge-inprogress" in src
    # C-F4 — conteneur rafraîchi sans rechargement (HTMX hx-select sur la page).
    assert 'id="claim-sections"' in src
    assert "claimsRefresh" in src
    assert 'hx-select="#claim-sections"' in src
    assert 'hx-trigger="claimsRefresh from:body"' in src
    # C-F6 — formulaires secondaires repliés.
    assert "form-disclosure" in src
    assert src.count("form-disclosure") >= 3  # assurance, provision, position cale (+ pièce)
    # La timeline et sa saisie de note restent dépliées (usage fréquent) —
    # pas de <details> autour du formulaire de note.
    note_idx = src.index('action="/claims/{{ claim.id }}/notes"')
    assert "<details" not in src[max(0, note_idx - 400) : note_idx]


def test_claims_index_template_markers():
    """L'index conserve son mapping de badges et distingue mieux les icônes
    de statut (C-F5) — sans casser le fallback existant."""
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/claims/index.html")[0]
    assert "badge-overdue" in src
    assert "badge-completed" in src
    assert "badge-error" in src
    assert "badge-inprogress" in src
    assert "empty-state" in src


@pytest.mark.parametrize("name", ["index.html", "detail.html", "new.html", "stats.html"])
def test_all_claims_templates_compile(name):
    """Les 4 templates du module claims compilent (parsing Jinja sans erreur)."""
    from app.templating import templates

    templates.env.get_template(f"staff/claims/{name}")


# ───────────────────────── Mutations HTMX (204 + refresh) ─────────────────────────


@pytest.mark.asyncio
async def test_add_note_htmx_returns_204_with_claims_refresh(db, staff_user):
    """Sous HTMX : 204 + HX-Trigger toast/claimsRefresh (pas de rechargement)."""
    claim = await _claim(db)
    req = FakeRequest()
    req.headers["hx-request"] = "true"

    resp = await claim_add_note(claim.id, req, body="Suivi expertise", db=db, user=staff_user)

    assert resp.status_code == 204
    trigger = resp.headers["HX-Trigger"]
    assert "claimsRefresh" in trigger
    assert "toast" in trigger
    entries = (
        (
            await db.execute(
                select(ClaimTimelineEntry).where(ClaimTimelineEntry.claim_id == claim.id)
            )
        )
        .scalars()
        .all()
    )
    assert any(t.body == "Suivi expertise" for t in entries)


@pytest.mark.asyncio
async def test_add_note_without_htmx_redirects(db, staff_user):
    """Sans JS : le repli 303 classique reste intact."""
    claim = await _claim(db)
    req = FakeRequest()  # pas de header hx-request

    resp = await claim_add_note(claim.id, req, body="Note classique", db=db, user=staff_user)

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/claims/{claim.id}"


@pytest.mark.asyncio
async def test_update_status_htmx_returns_204(db, staff_user):
    """Changement de statut sous HTMX : 204 + escaleRefresh-like claimsRefresh."""
    claim = await _claim(db, status="open")
    req = FakeRequest()
    req.headers["hx-request"] = "true"

    resp = await claim_update_status(
        claim.id,
        req,
        new_status="in_review",
        note="Passage en expertise",
        settled_eur=None,
        db=db,
        user=staff_user,
    )

    assert resp.status_code == 204
    assert "claimsRefresh" in resp.headers["HX-Trigger"]
    assert claim.status == "in_review"


@pytest.mark.asyncio
async def test_update_status_without_htmx_redirects(db, staff_user):
    """Sans JS : le changement de statut reste un 303 classique."""
    claim = await _claim(db, status="open")
    req = FakeRequest()

    resp = await claim_update_status(
        claim.id,
        req,
        new_status="rejected",
        note=None,
        settled_eur=None,
        db=db,
        user=staff_user,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/claims/{claim.id}"
    assert claim.status == "rejected"
