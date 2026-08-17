"""Motif partagé « valeur dérivée → override → justification obligatoire ».

Ce socle est réutilisé par **deux lots** (date *shipped on board* du BL, durées de
contrat des relèves), c'est pourquoi il est testé pour lui-même et non seulement à
travers ses appelants.

Ce que ces tests protègent :

1. **une justification vide ou creuse est refusée** — c'est la règle qui donne sa
   valeur au journal « en cas de contrôle » ;
2. **la dérivée n'est jamais recopiée dans l'override** — sinon « corrigé
   volontairement à cette valeur » devient indistinguable de « pas corrigé » ;
3. **une dérivation sans donnée source ne renvoie pas de valeur** — mieux vaut pas
   de date qu'une date fausse sur un connaissement ;
4. **la provenance est portée jusqu'à l'affichage** — une date corrigée à la main
   et une date lue dans la timeline n'ont pas la même valeur probante.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services import derived_override as d

# ───────────────────────── justification ─────────────────────────


@pytest.mark.parametrize("bad", [None, "", "   ", "\n\t "])
def test_an_empty_justification_is_refused(bad):
    with pytest.raises(d.JustificationRequired):
        d.clean_justification(bad)


@pytest.mark.parametrize(
    "creuse",
    ["ok", "OK.", "erreur", "correction", "n/a", "-", "cf. mail", "Voir mail", "test"],
)
def test_a_hollow_justification_is_refused(creuse):
    """🔴 Le cœur de la règle : remplir le champ n'est pas justifier.

    Ces valeurs seraient lues en contrôle des mois plus tard par quelqu'un qui
    n'était pas là. Elles ne répondent à aucune question.
    """
    with pytest.raises(d.JustificationRequired):
        d.clean_justification(creuse)


def test_a_too_short_justification_is_refused():
    with pytest.raises(d.JustificationRequired) as e:
        d.clean_justification("trop court", min_length=30)
    assert "trop courte" in str(e.value)


def test_a_real_justification_is_accepted_and_normalised():
    out = d.clean_justification("  Chargement   terminé le 12 au soir,\n SOF corrigé  ")
    assert out == "Chargement terminé le 12 au soir, SOF corrigé"


def test_the_message_names_the_field_being_corrected():
    """Le refus doit dire QUOI justifier, sinon l'utilisateur ne sait pas quoi écrire."""
    with pytest.raises(d.JustificationRequired) as e:
        d.clean_justification("", field="la date de mise à bord")
    assert "la date de mise à bord" in str(e.value)


def test_the_minimum_length_is_a_parameter_not_a_dogma():
    """⚠️ Garde anti-sur-correction : un appelant peut assouplir le seuil."""
    assert d.clean_justification("court mais", min_length=5) == "court mais"


# ───────────────────────── résolution ─────────────────────────


def test_without_an_override_the_derived_value_wins():
    r = d.resolve(override=None, derived=date(2026, 8, 12))
    assert r.value == date(2026, 8, 12)
    assert r.source == d.DERIVED
    assert r.is_override is False
    assert r.reason is None


def test_an_override_wins_and_carries_its_reason():
    r = d.resolve(
        override=date(2026, 8, 14),
        derived=date(2026, 8, 12),
        reason="Dernière palette embarquée le 14, SOF saisi en retard",
    )
    assert r.value == date(2026, 8, 14)
    assert r.is_override is True
    assert r.reason
    assert r.derived_value == date(2026, 8, 12), "la dérivée doit rester lisible pour comparaison"


def test_a_missing_source_yields_no_value_rather_than_a_wrong_one():
    """🔴 Pas de donnée source ⇒ pas de date. Un connaissement sans opération
    réelle ne doit pas afficher une date inventée."""
    r = d.resolve(override=None, derived=None)
    assert r.value is None
    assert r.source == d.DERIVED


def test_an_override_equal_to_the_derived_value_is_not_a_divergence():
    """Utile pour n'attirer l'œil du contrôleur que sur les vrais écarts."""
    same = date(2026, 8, 12)
    r = d.resolve(override=same, derived=same, reason="Confirmé après vérification du SOF")
    assert r.is_override is True
    assert r.diverges is False


def test_a_divergence_is_flagged():
    r = d.resolve(override=date(2026, 8, 14), derived=date(2026, 8, 12), reason="x" * 20)
    assert r.diverges is True


def test_a_divergence_needs_a_derived_value_to_compare_against():
    """Sans dérivée, on ne peut pas parler d'écart — on ne l'affirme donc pas."""
    r = d.resolve(override=date(2026, 8, 14), derived=None, reason="x" * 20)
    assert r.is_override is True
    assert r.diverges is False


# ───────────────────────── snapshot d'audit ─────────────────────────


def test_the_audit_snapshot_is_serialisable_and_complete():
    r = d.resolve(
        override=date(2026, 8, 14),
        derived=date(2026, 8, 12),
        reason="Dernière palette embarquée le 14",
    )
    snap = d.audit_snapshot(r)
    assert snap == {
        "value": "2026-08-14",
        "source": "override",
        "reason": "Dernière palette embarquée le 14",
        "derived_value": "2026-08-12",
        "diverges": True,
    }
    # Sérialisable tel quel : le snapshot part en JSON dans la piste d'audit.
    import json

    assert json.loads(json.dumps(snap)) == snap


def test_the_snapshot_handles_non_date_values():
    """Le motif sert aussi aux durées de contrat (des entiers), pas qu'aux dates."""
    snap = d.audit_snapshot(d.resolve(override=60, derived=90, reason="Cas particulier PMS"))
    assert snap["value"] == 60 and snap["derived_value"] == 90
