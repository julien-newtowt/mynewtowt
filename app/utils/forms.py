"""Lecture typée des champs de formulaire.

``FormData.get`` renvoie ``UploadFile | str | None`` : un client qui poste un
**fichier** sous le nom attendu d'un champ texte fait donc lever ``.strip()``
sur un ``UploadFile``, et la route répond 500 au lieu de rejeter proprement la
saisie. Le motif ``(form.get("x") or "").strip()`` est répandu dans les routers ;
il porte ce défaut partout où il est employé.

:func:`form_str` reproduit exactement la sémantique de ce motif — valeur absente
**ou vide** ⇒ ``default`` — en traitant en plus le fichier comme une absence de
valeur. C'est le comportement voulu : un fichier n'est pas une réponse à un
champ texte, et le rejeter comme une saisie vide fait remonter l'erreur de
validation métier plutôt qu'une erreur serveur.
"""

from __future__ import annotations

from starlette.datastructures import FormData


def form_str(form: FormData, name: str, default: str = "") -> str:
    """Valeur texte d'un champ de formulaire — jamais un fichier, jamais ``None``."""
    value = form.get(name)
    return value if isinstance(value, str) and value else default
