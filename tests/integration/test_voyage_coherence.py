"""Un indicateur de voyage ne doit pas affirmer plus que la donnée ne dit.

Incohérences constatées en production le 2026-09-03 sur le leg 1CFRBR6
(Anemos, Fécamp → São Sebastião, parti le 01/09, non arrivé) :

    DISTANCE RÉELLE  400 NM       THÉORIQUE 5799 NM
    ALLONGEMENT      ×0.07        ÉCART     −5399 NM

Un **allongement** est par définition ≥ 1 : une route réelle est plus longue
que l'orthodromie, jamais quatorze fois plus courte. Le rapport affiché
comparait un trajet **partiel** à la distance du voyage **entier** — c'est un
taux d'avancement, pas un allongement. Et l'« écart » n'était que l'opposé du
reste à parcourir, que la colonne « restante » disait déjà, correctement.

Au même instant, le tableau de bord annonçait Anemos « À quai » quand sa fiche
de leg disait « En mer » : la carte de flotte déduisait son étiquette de la
seule vitesse fond (``sog < 0.5`` ⇒ « À quai »), et une vitesse **absente**
tombait dans le même cas — les gabarits envoyaient ``sog or 0``. Sur une
compagnie à la voile, un navire encalminé au milieu de l'Atlantique était donc
« à quai ». Sur la page publique, qui ne transmettait aucune vitesse, **tous**
les navires l'étaient.
"""

from __future__ import annotations

import pathlib
import re

from app.services.voyage_track import TrackMetrics

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FLEET_JS = _ROOT / "app" / "static" / "js" / "fleet-map.js"
_NAV_HTML = _ROOT / "app" / "templates" / "staff" / "navigation" / "index.html"
_MAP_TEMPLATES = (
    _ROOT / "app" / "templates" / "staff" / "dashboard.html",
    _ROOT / "app" / "templates" / "staff" / "tracking" / "index.html",
    _ROOT / "app" / "templates" / "public" / "fleet.html",
)


def _metrics(*, actual: float, theoretical: float | None, active: bool) -> TrackMetrics:
    return TrackMetrics(
        point_count=656,
        actual_nm=actual,
        theoretical_nm=theoretical,
        remaining_nm=None,
        duration_hours=62.0,
        avg_speed_kn=6.4,
        is_active=active,
    )


# ───────────── allongement : muet tant que le voyage n'est pas arrivé ─────────


def test_elongation_is_none_while_the_voyage_is_running():
    """Le cas 1CFRBR6 : 400 NM parcourus sur 5799 → surtout pas « ×0.07 »."""
    m = _metrics(actual=400.0, theoretical=5799.0, active=True)
    assert m.real_elongation is None
    # La grandeur qui a un sens en cours de route reste disponible.
    assert m.progress_ratio == 0.069


def test_elongation_is_computed_once_arrived():
    """Voyage arrivé : la route réelle dépasse l'orthodromie, l'allongement ≥ 1."""
    m = _metrics(actual=6100.0, theoretical=5799.0, active=False)
    assert m.real_elongation == round(6100.0 / 5799.0, 3)
    assert m.real_elongation > 1


def test_progress_ratio_never_exceeds_one():
    """Un navire qui a dépassé l'orthodromie reste à 100 % d'avancement."""
    m = _metrics(actual=6100.0, theoretical=5799.0, active=True)
    assert m.progress_ratio == 1.0


# ───────────── écart : muet tant que le voyage n'est pas arrivé ─────────────


def test_deviation_is_none_while_running():
    """« −5399 NM » n'était pas un écart, c'était le reste à parcourir."""
    assert _metrics(actual=400.0, theoretical=5799.0, active=True).deviation_nm is None


def test_deviation_is_the_real_gap_once_arrived():
    assert _metrics(actual=6100.0, theoretical=5799.0, active=False).deviation_nm == 301.0


def test_navigation_table_uses_the_service_property():
    """Le gabarit ne recalcule pas l'écart en ligne — sinon la règle est contournée."""
    src = _NAV_HTML.read_text(encoding="utf-8")
    assert "m.deviation_nm" in src
    assert "m.actual_nm - m.theoretical_nm" not in src


# ───────────── carte de flotte : la phase fait foi, pas la vitesse ─────────────


def test_fleet_map_never_infers_a_berth_from_speed_alone():
    """« À quai » ne se déduit plus d'un SOG bas ni d'un SOG absent."""
    src = _FLEET_JS.read_text(encoding="utf-8")
    assert "function vesselStatus(sog, phase)" in src, "la phase doit être un paramètre"
    # L'ancienne règle : tout ce qui n'était pas rapide devenait « À quai ».
    assert 'sog < 0.5) return { label: "À quai"' not in src
    # « À quai » ne subsiste que comme libellé d'une phase déclarée.
    bloc = re.search(r"PHASE_LABELS\s*=\s*\{(.+?)\};", src, re.S)
    assert bloc and "a_quai:" in bloc.group(1)


def test_fleet_map_payloads_carry_the_phase():
    """Les trois gabarits de carte transmettent la phase et une vitesse nullable."""
    for path in _MAP_TEMPLATES:
        src = path.read_text(encoding="utf-8")
        nom = path.name
        assert '"phase": phases.get(v.id)' in src, f"{nom} : phase absente du payload"
        # Une vitesse absente ne doit plus devenir 0 — c'est ce 0 qui valait
        # « à quai » pour un navire dont on ignorait simplement la vitesse.
        assert "(p.sog_kn or 0)" not in src, f"{nom} : SOG absent converti en 0"
