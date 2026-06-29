"""ESC-08 (tranche) — métriques de navigation du leg sur la vue d'escale.

La carte n'est affichée que s'il existe des points GPS (``point_count``) ; sans
position, ``compute_metrics`` renvoie des métriques nulles (carte masquée).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace


def test_compute_metrics_empty_positions_is_hidden():
    from app.services.voyage_track import compute_metrics

    leg = SimpleNamespace(
        atd=None,
        ata=None,
        etd=datetime(2026, 1, 1, tzinfo=UTC),
        eta=datetime(2026, 1, 5, tzinfo=UTC),
        distance_nm=None,
        vessel_id=1,
    )
    m = compute_metrics([], leg)
    assert m.point_count == 0  # → carte masquée côté template
    assert m.actual_nm == 0.0
    assert m.real_elongation is None


def test_escale_template_has_nav_metrics_card():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/escale/index.html")[0]
    assert "nav_metrics" in src
    assert "Distance réelle" in src
    assert "Allongement" in src
