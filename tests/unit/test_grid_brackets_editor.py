"""Édition des tranches de remplissage — un bouton par ligne, ajout et retrait.

L'écran ne proposait que deux gestes implicites : vider une ligne pour la
supprimer, et remplir l'une des trois lignes vierges du bas pour ajouter. Rien
ne le disait à l'écran, et au-delà de trois ajouts il fallait enregistrer puis
recharger.

Ces tests verrouillent le rendu dont dépend ``bracket-rows.js`` : sans les
crochets ``data-*``, le script est inerte et l'écran régresse en silence.
"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from app.models.commercial import (
    MAX_PAYMENT_TERMS,
    PAYMENT_TRIGGER_LABELS,
    PAYMENT_TRIGGERS,
    RATE_OPTION_UNIT_LABELS,
    RATE_OPTION_UNITS,
    Client,
    RateGrid,
)
from app.templating import brand_for_lang, templates


class _FakeState:
    csrf_token = "test-csrf-token"


def _render(status: str = "draft") -> str:
    """Rend l'écran avec un **vrai** ``RateGrid``, pas un objet de fiction.

    Un ``SimpleNamespace`` aux attributs inventés testerait sa propre fiction et
    survivrait à un renommage de colonne — le défaut exact qui avait laissé
    passer un `AttributeError` sur l'export DOCX. Le modèle est instancié sans
    être persisté : le gabarit ne lit que ses attributs.
    """
    grid = RateGrid(
        id=7,
        reference="RG-2026-0001",
        status=status,
        valid_from=date(2026, 1, 1),
        brackets_json=json.dumps(
            [
                {"key": "q50", "label": "< 50 palettes", "max_qty": 50, "coeff": 1.10},
                {"key": "q100", "label": "50 à 100", "max_qty": 100, "coeff": 1.00},
            ]
        ),
    )
    grid.client = Client(id=1, name="BigCoffee", client_type="freight_forwarder")
    grid.lines = []
    grid.options = []
    grid.payment_terms = []

    # Rendu hors requête : le context processor n'injecte ni `brand` ni `lang`.
    return templates.get_template("staff/commercial/grid_detail.html").render(
        request=SimpleNamespace(
            state=_FakeState(), url=SimpleNamespace(path="/commercial"), scope={}
        ),
        grid=grid,
        vessel=None,
        route_ports=[],
        option_units=RATE_OPTION_UNITS,
        option_unit_labels=RATE_OPTION_UNIT_LABELS,
        payment_triggers=PAYMENT_TRIGGERS,
        payment_trigger_labels=PAYMENT_TRIGGER_LABELS,
        max_payment_terms=MAX_PAYMENT_TERMS,
        lang="fr",
        brand=brand_for_lang("fr"),
        user=SimpleNamespace(
            id=1,
            username="ops",
            role="commercial",
            full_name="Ops",
            language="fr",
            assigned_vessel_id=None,
        ),
    )


def test_chaque_tranche_porte_ses_deux_boutons():
    """Un « + » et une « ✕ » en face de chaque ligne, ligne vierge comprise."""
    html = _render()

    # 2 tranches existantes + 1 ligne vierge conservée pour le cas sans JS.
    assert html.count("data-bracket-row") == 3 + 1  # +1 : le gabarit <template>
    assert html.count("data-add-bracket") == 4
    assert html.count("data-remove-bracket") == 4


def test_les_crochets_du_script_sont_presents():
    """Sans ces identifiants, ``bracket-rows.js`` ne s'accroche à rien."""
    html = _render()

    assert 'id="bracket-rows"' in html
    assert 'id="bracket-row-tpl"' in html
    assert "/static/js/bracket-rows.js" in html


def test_le_gabarit_porte_les_trois_champs_ensemble():
    """Les champs sont postés en listes parallèles : la ligne s'insère entière.

    Cloner une ligne à laquelle il manquerait un champ décalerait tout le
    tableau d'un cran — chaque coefficient changerait de tranche, en silence.
    """
    html = _render()
    tpl = html.split('id="bracket-row-tpl"', 1)[1].split("</template>", 1)[0]

    assert 'name="bracket_label"' in tpl
    assert 'name="bracket_max_qty"' in tpl
    assert 'name="bracket_coeff"' in tpl


def test_le_script_de_page_ne_chasse_pas_ceux_du_layout():
    """``{{ super() }}`` — le bloc head du layout staff porte 4 scripts.

    Le surcharger sans appeler ``super()`` ferait disparaître la sidebar,
    l'horloge, les menus et le sélecteur de langue de cette seule page : une
    panne discrète, visible nulle part ailleurs.
    """
    html = _render()

    for script in ("sidebar.js", "clock.js", "topbar-menus.js", "lang-switch.js"):
        assert f"/static/js/{script}" in html


def test_une_grille_active_n_est_pas_editable():
    """Grille verrouillée : ni tableau d'édition, ni boutons."""
    html = _render(status="active")

    assert "data-add-bracket" not in html
    assert 'id="bracket-rows"' not in html
