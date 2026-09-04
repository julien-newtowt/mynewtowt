"""Une arrivée déclarée ne suffit pas à faire d'un voyage un voyage terminé.

Constat du 2026-09-04 sur le leg RERUN→BRSSO : une ATA saisie par erreur, un
jour après le départ. L'écran d'escale affirmait alors « Restant 0 NM » et
« Allongement ×0.02 » sur un trajet de 119 NM relevés pour une orthodromie de
6287 NM.

Les deux nombres étaient faux pour la même raison : ils étaient gardés par le
**statut** (`is_active`) et non par l'invariant que la docstring de
`real_elongation` énonce pourtant — un allongement est ≥ 1, une route réelle
n'est jamais plus courte que la géodésique. Une ATA erronée faisait tomber le
statut et rouvrait le trou.

Ces tests verrouillent l'invariant lui-même.
"""

from __future__ import annotations

from app.services.voyage_track import TrackMetrics


def _metrics(**kw) -> TrackMetrics:
    base = dict(
        point_count=215,
        actual_nm=119.0,
        theoretical_nm=6287.0,
        remaining_nm=None,
        duration_hours=22.0,
        avg_speed_kn=5.4,
        is_active=False,
        declared_arrived=True,
    )
    base.update(kw)
    return TrackMetrics(**base)  # type: ignore[arg-type]


def test_le_cas_constate_est_signale_comme_incoherent():
    """119 NM relevés pour 6287 d'orthodromie, voyage déclaré arrivé."""
    assert _metrics().arrival_contradicted_by_track is True


def test_aucun_allongement_n_est_affiche_sur_une_arrivee_contredite():
    """×0.02 n'est pas un allongement : c'est un contresens. Mieux vaut rien."""
    assert _metrics().real_elongation is None


def test_un_voyage_reellement_termine_garde_son_allongement():
    """Le correctif ne doit pas éteindre l'indicateur quand il a un sens."""
    m = _metrics(actual_nm=6800.0)

    assert m.arrival_contradicted_by_track is False
    assert m.real_elongation == round(6800.0 / 6287.0, 3)
    assert m.real_elongation > 1


def test_une_route_exactement_orthodromique_reste_valide():
    """Borne : ``actual == theoretical`` vaut 1, ce n'est pas une contradiction."""
    m = _metrics(actual_nm=6287.0)

    assert m.arrival_contradicted_by_track is False
    assert m.real_elongation == 1.0


def test_un_leg_en_mer_n_est_pas_concerne():
    """En cours de route, `actual` est partiel par construction — pas un défaut.

    Ce cas était déjà traité (l'écran affiche « Avancement ») ; on vérifie que
    le nouveau signal ne vient pas le requalifier en incohérence.
    """
    m = _metrics(is_active=True, declared_arrived=False)

    assert m.arrival_contradicted_by_track is False
    assert m.real_elongation is None  # inchangé : un taux d'avancement, pas un allongement
    assert m.progress_ratio == round(119.0 / 6287.0, 3)


def test_sans_orthodromie_connue_on_ne_conclut_rien():
    """Pas de théorique, pas de comparaison possible — donc pas d'accusation."""
    assert _metrics(theoretical_nm=None).arrival_contradicted_by_track is False


def test_sans_trace_gps_on_ne_conclut_rien():
    """Un voyage arrivé sans aucun point relevé n'est pas contredit : il est muet."""
    assert _metrics(actual_nm=0.0, point_count=0).arrival_contradicted_by_track is False
