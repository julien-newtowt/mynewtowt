"""Unit — polylignes SVG des conditions de cale (fonction pure)."""

from __future__ import annotations

from app.services.hold_conditions import SVG_HEIGHT, SVG_PAD, SVG_WIDTH, svg_points


def _coords(points: str) -> list[tuple[float, float]]:
    return [tuple(map(float, p.split(","))) for p in points.split()]


def test_svg_points_empty_when_less_than_two_values():
    assert svg_points([]) == ""
    assert svg_points([21.0]) == ""
    assert svg_points([None, None, 21.0]) == ""


def test_svg_points_normalizes_within_viewbox():
    pts = _coords(svg_points([10.0, 20.0, 15.0, 30.0]))
    assert len(pts) == 4
    for x, y in pts:
        assert SVG_PAD <= x <= SVG_WIDTH - SVG_PAD
        assert SVG_PAD <= y <= SVG_HEIGHT - SVG_PAD
    # min de la série → bas du cadre ; max → haut du cadre.
    ys = [y for _, y in pts]
    assert ys[0] == max(ys)  # 10.0 est le minimum
    assert ys[3] == min(ys)  # 30.0 est le maximum


def test_svg_points_skips_none_but_keeps_x_spacing():
    pts = _coords(svg_points([10.0, None, 20.0]))
    assert len(pts) == 2
    # le point 20.0 (indice 2/2) est à l'extrême droite du cadre
    assert pts[1][0] == SVG_WIDTH - SVG_PAD


def test_svg_points_flat_series_stays_in_frame():
    pts = _coords(svg_points([18.0, 18.0, 18.0]))
    ys = {y for _, y in pts}
    assert len(ys) == 1  # ligne horizontale
    (y,) = ys
    assert SVG_PAD <= y <= SVG_HEIGHT - SVG_PAD
