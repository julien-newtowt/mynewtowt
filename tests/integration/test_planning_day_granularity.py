"""Planning — granularité JOURNÉE (plus de saisie d'heure dans le formulaire leg).

Le formulaire `leg_form.html` (ETD / ETA / clôture booking) passe en
`type="date"` : la saisie d'heure disparaît de l'UX planification. Le
back-end (`parse_form_datetime`) acceptait déjà "YYYY-MM-DD" (minuit UTC)
avant ce changement — on vérifie ici qu'il continue de le faire.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest


def test_leg_form_template_uses_date_inputs_not_datetime_local():
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "staff/planning/leg_form.html")[0]
    # ETD / ETA / clôture réservation : granularité jour.
    assert 'type="date"' in src
    assert "datetime-local" not in src


def test_parse_form_datetime_accepts_date_only_as_utc_midnight():
    from app.services.planning import parse_form_datetime

    dt = parse_form_datetime("2026-06-16")
    assert dt == datetime(2026, 6, 16, tzinfo=UTC)
    assert dt.tzinfo is not None


def test_parse_form_datetime_still_requires_a_value():
    from app.services.planning import InvalidLegDates, parse_form_datetime

    with pytest.raises(InvalidLegDates):
        parse_form_datetime("")
