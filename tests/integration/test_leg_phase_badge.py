"""Sentinelle — le badge de phase navire couvre les cinq phases de ``Leg.phase``.

Constat des Opérations le 2026-09-03 : Anemos (1CFRBR6) et Artemis (2CFRGP6)
s'affichaient tous deux « À quai » alors qu'aucun de leurs legs n'avait d'ATA.

``escale_router`` et ``onboard_router`` recalculaient la phase à la main sur
**deux** états — ``"en_mer" if (atd and not ata) else "a_quai"`` — et le gabarit
du badge ne connaissait que ces deux libellés. Tout ce qui n'était pas « en
mer » devenait donc « À quai » : un leg planifié sans ATD déclaré, un leg
terminé, un leg annulé. Trois situations sur cinq affichées faux.

C'est le piège que le CLAUDE.md documente pour les statuts d'équipage : un
statut non traité tombe dans le ``{% else %}`` du gabarit et affiche une
information fausse — l'inverse du défaut qu'on prétend corriger. Ce test le
verrouille pour les phases de leg.
"""

from __future__ import annotations

import pathlib
import re

from app.services.planning import LEG_PHASE_LABELS, LEG_PHASES

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_BADGE = _ROOT / "app" / "templates" / "staff" / "_leg_filter.html"
_CSS = _ROOT / "app" / "static" / "css" / "kairos.css"
_ROUTERS = (
    _ROOT / "app" / "routers" / "escale_router.py",
    _ROOT / "app" / "routers" / "onboard_router.py",
)


def test_every_phase_has_a_label():
    """``LEG_PHASE_LABELS`` est la source unique : aucune phase sans libellé."""
    manquantes = [p for p in LEG_PHASES if not LEG_PHASE_LABELS.get(p)]
    assert not manquantes, f"phases sans libellé : {manquantes}"


def test_badge_reads_labels_from_the_single_source():
    """Le gabarit ne recopie pas les libellés — il appelle ``leg_phase_label``.

    Les recopier rouvrirait le défaut : une phase ajoutée à ``LEG_PHASES`` sans
    passage dans le gabarit s'afficherait sous un libellé faux.
    """
    src = _BADGE.read_text(encoding="utf-8")
    assert "leg_phase_label(vessel_status)" in src
    # Le binaire « à quai sinon en mer » ne doit pas revenir.
    assert "== 'a_quai' %}" not in src, "badge redevenu binaire"


def test_badge_has_an_icon_for_every_phase():
    """Chaque phase a une icône : sans entrée, le badge tombait sur un repli."""
    src = _BADGE.read_text(encoding="utf-8")
    bloc = re.search(r"phase_icons\s*=\s*\{(.+?)\}", src, re.S)
    assert bloc, "table phase_icons absente du gabarit"
    for phase in LEG_PHASES:
        assert f"'{phase}'" in bloc.group(1), f"icône manquante pour « {phase} »"


def test_css_styles_every_phase():
    """Une phase sans classe CSS s'affiche sans fond et se lit mal."""
    css = _CSS.read_text(encoding="utf-8")
    for phase in LEG_PHASES:
        assert f".vessel-status-badge.{phase}" in css, f"classe CSS manquante : {phase}"


def test_routers_do_not_recompute_the_phase():
    """``Leg.phase`` est la dérivation unique — personne ne la refait à la main."""
    for path in _ROUTERS:
        src = path.read_text(encoding="utf-8")
        assert "vessel_status = selected_leg.phase" in src, f"{path.name} n'utilise pas Leg.phase"
        assert '"en_mer" if (' not in src, f"{path.name} recalcule la phase à la main"
