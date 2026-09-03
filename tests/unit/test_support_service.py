"""Support applicatif — logique de service.

Couvre ce que le module ``tickets`` ne couvre PAS : la création, les transitions,
l'assainissement du contexte technique, et le prédicat d'archivage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import support

# ─────────────────────────────── Référence ───────────────────────────────


def test_reference_format() -> None:
    assert support.format_reference(2026, 1) == "SUP-2026-0001"
    assert support.format_reference(2026, 42) == "SUP-2026-0042"
    assert support.format_reference(2027, 1234) == "SUP-2027-1234"


def test_reference_is_not_random() -> None:
    """Deux appels avec les mêmes entrées donnent la même référence.

    Garde-fou contre un retour à un suffixe aléatoire : le module ``tickets``
    tire ``secrets.token_hex(2)``, soit 65 536 valeurs par an sur une colonne
    UNIQUE sans reprise — ~50 % de collision à 300 tickets/an.
    """
    assert support.format_reference(2026, 7) == support.format_reference(2026, 7)


# ─────────────────────────────── Transitions ───────────────────────────────


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("nouveau", "en_cours"),
        ("nouveau", "rejete"),
        ("en_cours", "en_attente_utilisateur"),
        ("en_cours", "resolu"),
        ("en_cours", "rejete"),
        ("en_attente_utilisateur", "en_cours"),
        ("en_attente_utilisateur", "resolu"),
        ("resolu", "clos"),
        ("resolu", "en_cours"),
    ],
)
def test_valid_transitions(current: str, target: str) -> None:
    support.assert_transition(current, target)  # ne lève pas


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("nouveau", "clos"),  # on ne clôt pas sans traiter
        ("nouveau", "resolu"),
        ("clos", "en_cours"),  # terminal
        ("rejete", "en_cours"),  # terminal
        ("resolu", "rejete"),
        ("en_attente_utilisateur", "rejete"),
        ("en_cours", "nouveau"),  # pas de retour arrière
    ],
)
def test_invalid_transitions(current: str, target: str) -> None:
    with pytest.raises(support.InvalidSupportTransition):
        support.assert_transition(current, target)


def test_terminal_states_have_no_exit() -> None:
    for state in support.TERMINAL_STATUSES:
        assert support.allowed_targets(state) == set()


def test_reporter_may_only_reopen_or_answer() -> None:
    """Le demandeur ne dispose QUE de deux transitions (spec §7)."""
    assert support.is_reporter_transition("en_attente_utilisateur", "en_cours")
    assert support.is_reporter_transition("resolu", "en_cours")
    # Tout le reste lui est fermé — c'est le routeur qui l'applique, mais la
    # règle est ici, en un seul endroit.
    assert not support.is_reporter_transition("nouveau", "en_cours")
    assert not support.is_reporter_transition("resolu", "clos")
    assert not support.is_reporter_transition("en_cours", "rejete")


def test_all_statuses_are_reachable_from_the_table() -> None:
    """Aucun état déclaré n'est orphelin (ni source ni cible)."""
    reachable = {"nouveau"}
    for targets in support._TRANSITIONS.values():
        reachable |= targets
    assert reachable == set(support.STATUSES)


# ─────────────────────── Contexte technique : page_url ───────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "//evil.example",  # protocol-relative
        "http://evil.example/x",  # schéma
        "https://evil.example/x",
        "javascript:alert(1)",
        "data:text/html,x",
        "escale",  # pas un chemin absolu
        "",
        None,
        "/ok\nInjected: 1",  # injection d'en-tête / de ligne
        "/ok\rX",
        "/ok\tY",
    ],
)
def test_page_url_rejected(value) -> None:
    """``page_url`` est la SEULE valeur de contexte venant du client.

    Non filtrée, elle devient un vecteur de redirection ouverte.
    """
    assert support.sanitize_page_url(value) is None


@pytest.mark.parametrize(
    "value",
    ["/", "/escale", "/escale?leg_id=3", "/mrv/parametres#seuils", "/rh/moi"],
)
def test_page_url_accepted(value: str) -> None:
    assert support.sanitize_page_url(value) == value


def test_page_url_is_capped() -> None:
    assert len(support.sanitize_page_url("/" + "a" * 900)) == 500


# ───────────────────────────── Archivage ─────────────────────────────


def _fake(status: str, *, closed_days: int | None = None, rejected_days: int | None = None):
    now = datetime.now(UTC)
    return SimpleNamespace(
        status=status,
        closed_at=(now - timedelta(days=closed_days)) if closed_days is not None else None,
        rejected_at=(now - timedelta(days=rejected_days)) if rejected_days is not None else None,
    )


def test_archived_after_the_delay() -> None:
    assert support.is_archived(_fake("clos", closed_days=91)) is True
    assert support.is_archived(_fake("rejete", rejected_days=91)) is True


def test_not_archived_before_the_delay() -> None:
    assert support.is_archived(_fake("clos", closed_days=89)) is False


def test_age_alone_never_archives() -> None:
    """Le critère est l'état TERMINAL, pas l'âge.

    Une demande ouverte depuis deux ans doit rester dans la vue courante —
    sinon on la perdrait de vue précisément quand elle traîne.
    """
    old_open = SimpleNamespace(status="en_cours", closed_at=None, rejected_at=None)
    assert support.is_archived(old_open) is False
    stale = SimpleNamespace(
        status="en_attente_utilisateur",
        closed_at=datetime.now(UTC) - timedelta(days=900),
        rejected_at=None,
    )
    assert support.is_archived(stale) is False


def test_archive_tolerates_naive_datetimes() -> None:
    """Les colonnes ``DateTime(timezone=True)`` reviennent NAÏVES sous SQLite.

    Comparer naïf à aware lève un ``TypeError`` : c'est le bug rencontré dans
    ``voyage_track.leg_window``. ``ensure_utc`` doit absorber le cas.
    """
    naive = SimpleNamespace(
        status="clos",
        closed_at=(datetime.now(UTC) - timedelta(days=91)).replace(tzinfo=None),
        rejected_at=None,
    )
    assert support.is_archived(naive) is True


def test_read_only_covers_terminal_and_archived() -> None:
    assert support.is_read_only(_fake("clos", closed_days=1)) is True  # terminal récent
    assert support.is_read_only(_fake("clos", closed_days=91)) is True  # archivé
    open_one = SimpleNamespace(status="en_cours", closed_at=None, rejected_at=None)
    assert support.is_read_only(open_one) is False


# ─────────────────────── Cloisonnement (règles pures) ───────────────────────


def test_can_view_is_owner_or_admin() -> None:
    ticket = SimpleNamespace(reporter_id=7)
    assert support.can_view(ticket, user_id=7, is_admin=False) is True
    assert support.can_view(ticket, user_id=9, is_admin=False) is False
    assert support.can_view(ticket, user_id=9, is_admin=True) is True


def test_download_follows_the_same_rule_as_view() -> None:
    """Le droit ``support:C`` ne donne PAS accès aux pièces d'autrui.

    Une capture d'écran peut contenir des données d'un autre module (finance,
    RH) : c'est une exfiltration potentielle.
    """
    ticket = SimpleNamespace(reporter_id=7)
    assert support.can_download_attachment(ticket, user_id=9, is_admin=False) is False
    assert support.can_download_attachment(ticket, user_id=7, is_admin=False) is True
    assert support.can_download_attachment(ticket, user_id=9, is_admin=True) is True


def test_internal_comments_hidden_from_reporter() -> None:
    public = SimpleNamespace(is_internal=False)
    private = SimpleNamespace(is_internal=True)
    ticket = SimpleNamespace(comments=[public, private])
    assert support.visible_comments(ticket, is_admin=True) == [public, private]
    assert support.visible_comments(ticket, is_admin=False) == [public]


# ───────────────────────────── Nomenclature ─────────────────────────────


def test_no_french_label_lives_in_the_service() -> None:
    """Le service n'expose que des CLÉS i18n, jamais un libellé en clair.

    Le module ``tickets`` porte 19 libellés français en dur ; on ne reproduit
    pas ça (des marins vietnamiens signalent aussi des problèmes).
    """
    for mapping in (
        support.KIND_LABEL_KEYS,
        support.SEVERITY_LABEL_KEYS,
        support.STATUS_LABEL_KEYS,
    ):
        for value in mapping.values():
            assert value.startswith("sup_"), value
            assert " " not in value, value


def test_label_keys_exist_in_all_five_catalogs() -> None:
    from app.i18n import en, es, fr, pt_br, vi

    keys = [
        *support.KIND_LABEL_KEYS.values(),
        *support.SEVERITY_LABEL_KEYS.values(),
        *support.STATUS_LABEL_KEYS.values(),
    ]
    for name, catalog in (
        ("fr", fr.CATALOG),
        ("en", en.CATALOG),
        ("es", es.CATALOG),
        ("pt_br", pt_br.CATALOG),
        ("vi", vi.CATALOG),
    ):
        missing = [k for k in keys if k not in catalog]
        assert not missing, f"{name} : clés manquantes {missing}"
