"""Garde anti-régression : les templates « Vente à bord » et « Caisse » compilent."""

from __future__ import annotations

import pytest

from app.templating import templates


@pytest.mark.parametrize(
    "name",
    [
        "staff/onboard_sales/hub.html",
        "staff/onboard_sales/catalogue.html",
        "staff/onboard_sales/vessel.html",
        "staff/onboard_sales/sale.html",
        "staff/onboard_sales/checkout.html",
        "staff/onboard_sales/registre.html",
        "staff/cashbox/index.html",
        "staff/cashbox/detail.html",
        "staff/cashbox/cash_count_form.html",
        "staff/cashbox/cash_count_detail.html",
    ],
)
def test_template_compiles(name):
    assert templates.get_template(name) is not None
