"""Garde anti-régression : les nouveaux templates Cargo compilent (Jinja)."""

from __future__ import annotations

import pytest

from app.templating import templates


@pytest.mark.parametrize(
    "name",
    [
        "staff/cargo/packing_list_history.html",
        "portal/documents.html",
        "pdf/bill_of_lading_pl.html",
        "pdf/arrival_notice.html",
    ],
)
def test_template_compiles(name):
    # get_template parse + compile le corps du template (détecte les erreurs
    # de syntaxe Jinja sans nécessiter de contexte de requête).
    assert templates.get_template(name) is not None
