"""EVO-04 (socle) — scoring heuristique de pertinence des actualités."""

from __future__ import annotations


def test_score_relevant_news_high():
    from app.services.news_scoring import priority_label, score_news_item

    s = score_news_item("Cargo à voile décarboné", "wind propulsion shipping")
    assert s >= 60
    assert priority_label(s) == "haute"


def test_score_excludes_false_positives():
    from app.services.news_scoring import score_news_item

    # « sport » ne doit pas matcher « port » ; « transport » non plus.
    assert score_news_item("Résultats de football", "sport") == 0
    assert score_news_item("Le transport de marchandises") == 0


def test_score_word_prefix_matches():
    from app.services.news_scoring import score_news_item

    assert score_news_item("Nouveau terminal portuaire à Fécamp") == 15  # 'port' via portuaire
    assert score_news_item("Réglementation MRV / CII de l'IMO") == 15


def test_priority_labels():
    from app.services.news_scoring import priority_label

    assert priority_label(60) == "haute"
    assert priority_label(30) == "moyenne"
    assert priority_label(29) == "faible"
    assert priority_label(0) == "faible"


def test_veille_template_shows_priority():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/veille/index.html")[0]
    assert "scores.get(item.id)" in src
    assert "priorité" in src
