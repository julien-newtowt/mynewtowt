"""Motif réutilisable : **valeur dérivée → override → justification obligatoire**.

Ce motif est demandé **deux fois** dans le périmètre en cours, et la spec insiste
pour qu'il ne soit construit qu'une seule fois :

- la date *shipped on board* du connaissement
  (`SPEC_WORKFLOW_BILL_OF_LADING.md` §5.0) — dérivée du dernier jour des
  opérations d'escale, corrigeable par les Opérations « sous justification »,
  avec journal « en cas de contrôle » ;
- les durées de contrat d'embarquement
  (`REFERENCE_METIER_RELEVES_EQUIPAGE.md` §3.2) — dérivées du poste et du régime
  d'armement, « paramétrable avec possibilité de modif pour des cas en
  particulier avec mot de justification ».

Précédent proche dans le dépôt : ``validation_engine.get_threshold`` (MRV v2,
« zéro seuil en dur ») — résolution en cascade, *fail-closed*, et snapshot de ce
qui a été consommé pour la reproductibilité d'audit. Même esprit ici.

## Les trois règles

1. **La valeur dérivée est la référence.** Elle se recalcule, donc elle reste juste
   quand la donnée source bouge. On ne la recopie pas dans la colonne d'override :
   une valeur figée sans raison de l'être devient fausse en silence.
2. **Un override est explicite.** Il porte une valeur, un auteur, un horodatage.
   Sa présence est ce qui le distingue — pas un drapeau séparé qui pourrait
   désynchroniser.
3. **Sans justification, pas d'enregistrement.** Le refus est levé ici, avant toute
   écriture. Une justification vide, blanche, ou réduite à « ok » n'est pas une
   justification : elle ne répondrait pas à la question posée en contrôle.

⚠️ Ce module ne connaît **ni** la base **ni** le métier. Il décide, il ne persiste
pas — les appelants écrivent les colonnes et tracent. C'est ce qui le rend
réutilisable par deux lots qui ne partagent aucune table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Generic, TypeVar

T = TypeVar("T")

DERIVED = "derived"
OVERRIDE = "override"

#: Longueur minimale d'une justification, après nettoyage des espaces.
#:
#: Pourquoi un minimum et pas seulement « non vide » : la justification est lue
#: **en contrôle**, des mois plus tard, par quelqu'un qui n'était pas là. « ok »,
#: « erreur » ou « cf. mail » ne répondent à aucune question. Le seuil est
#: paramétrable par appelant : c'est un garde-fou, pas un dogme.
MIN_JUSTIFICATION_LENGTH = 10

#: Justifications explicitement refusées même si elles atteignent la longueur —
#: elles remplissent le champ sans rien expliquer.
_EMPTY_JUSTIFICATIONS = frozenset(
    {
        "ok",
        "okay",
        "rien",
        "aucun",
        "aucune",
        "n/a",
        "na",
        "-",
        "--",
        "...",
        "test",
        "correction",
        "modification",
        "changement",
        "erreur",
        "voir mail",
        "cf mail",
        "cf. mail",
        "voir email",
        "asdf",
    }
)


class JustificationRequired(ValueError):
    """Un override a été demandé sans justification exploitable.

    Volontairement une ``ValueError`` : c'est une donnée d'entrée invalide, que
    l'appelant doit renvoyer à l'utilisateur (400/409), pas une panne.
    """


def clean_justification(
    reason: str | None, *, min_length: int = MIN_JUSTIFICATION_LENGTH, field: str = "la valeur"
) -> str:
    """Valide et normalise une justification. Lève ``JustificationRequired`` sinon.

    Renvoie la justification nettoyée (espaces normalisés) — c'est **elle** qui doit
    être persistée, pas la saisie brute : un motif fait de trente espaces et de
    trois mots se relit mal dans un journal.
    """
    text = " ".join((reason or "").split())
    if not text:
        raise JustificationRequired(
            f"corriger {field} exige une justification — elle sera lue en cas de contrôle."
        )
    if text.casefold().rstrip(".!") in _EMPTY_JUSTIFICATIONS:
        raise JustificationRequired(
            f"« {text} » n'explique rien. Indiquer pourquoi {field} est corrigée."
        )
    if len(text) < min_length:
        raise JustificationRequired(
            f"justification trop courte ({len(text)} caractères, {min_length} attendus) — "
            "elle doit rester compréhensible par un tiers en contrôle."
        )
    return text


@dataclass(frozen=True)
class Resolved(Generic[T]):
    """Ce qu'on affiche, **et d'où ça vient**.

    La provenance n'est pas un détail d'implémentation : une date de mise à bord
    corrigée à la main et une date lue dans la timeline d'escale n'ont pas la même
    valeur probante. L'écran et le PDF doivent pouvoir les distinguer.
    """

    value: T | None
    source: str  # DERIVED | OVERRIDE
    reason: str | None = None  # justification, uniquement si OVERRIDE
    derived_value: T | None = None  # ce que la dérivation aurait donné

    @property
    def is_override(self) -> bool:
        return self.source == OVERRIDE

    @property
    def diverges(self) -> bool:
        """L'override dit-il autre chose que la dérivation ?

        Un override identique à la valeur dérivée n'est pas un écart — utile pour
        n'attirer l'œil du contrôleur que sur les vraies divergences.
        """
        return (
            self.is_override and self.derived_value is not None and self.value != self.derived_value
        )


def resolve(*, override: T | None, derived: T | None, reason: str | None = None) -> Resolved[T]:
    """Résout la valeur effective : l'override s'il existe, sinon la dérivée.

    ``None`` en override signifie « pas d'override » — d'où la règle 1 : on ne
    recopie jamais la dérivée dans la colonne d'override, sinon on ne saurait plus
    distinguer « corrigé volontairement à cette valeur » de « pas corrigé ».

    La valeur peut rester ``None`` : une dérivation sans donnée source ne doit
    **rien inventer**. Pour la date de mise à bord, cela veut dire qu'un
    connaissement dont l'escale n'a encore aucune opération réelle n'affiche pas de
    date — plutôt qu'une date fausse.
    """
    if override is not None:
        return Resolved(value=override, source=OVERRIDE, reason=reason, derived_value=derived)
    return Resolved(value=derived, source=DERIVED, reason=None, derived_value=derived)


def audit_snapshot(resolved: Resolved[Any]) -> dict[str, Any]:
    """Instantané sérialisable de la résolution, pour la piste d'audit.

    Reprend l'idée du snapshot des seuils MRV : ce qui a été **consommé** au moment
    du calcul, afin qu'un contrôle des mois plus tard puisse rejouer le
    raisonnement sans dépendre de l'état courant des données sources.
    """
    return {
        "value": _jsonable(resolved.value),
        "source": resolved.source,
        "reason": resolved.reason,
        "derived_value": _jsonable(resolved.derived_value),
        "diverges": resolved.diverges,
    }


def _jsonable(value: Any) -> Any:
    """Rend une valeur sérialisable en JSON, dates comprises.

    Test de type explicite plutôt qu'un ``hasattr("isoformat")`` : le motif sert
    aussi à des valeurs non temporelles (les durées de contrat sont des entiers),
    et un test par attribut attraperait n'importe quel objet exposant ce nom.
    """
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value
