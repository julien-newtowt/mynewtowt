"""Paramètres de requête tolérants au champ vide.

Un `<select>` ou un `<input>` vide **est envoyé** par le navigateur : il part
comme ``leg_id=`` (chaîne vide), et non pas absent de la query-string. C'est le
cas dès qu'un formulaire est soumis en GET, et systématiquement avec HTMX
``hx-include``, qui rassemble les champs désignés quel que soit leur contenu.

Or FastAPI/Pydantic refuse de coercer ``""`` en ``int`` : la route répond **422**
avant même d'être exécutée. Côté client, ``toast.js`` reçoit un corps 422 dont
``detail`` est une **liste** (et non la chaîne d'un ``HTTPException``), ne sait
donc rien en tirer, et affiche son repli générique « Action refusée — rechargez
la page ». L'utilisateur voit un refus d'autorisation là où il n'y a qu'un champ
laissé vide, et le fragment attendu n'arrive jamais.

C'est le défaut constaté le 2026-09-07 sur `/commercial/offers/new` : choisir un
client rafraîchit la liste des grilles via ``hx-include="#client_id,#leg_id"``,
et le voyage n'étant pas encore choisi, ``leg_id=`` faisait échouer chaque
requête. La liste des grilles ne se filtrait donc jamais.

Ces alias disent « vide ⇒ non fourni », ce qui est le sens métier : un champ que
l'opérateur n'a pas rempli n'est pas une valeur invalide. À utiliser pour **tout**
paramètre de requête optionnel non textuel qu'un formulaire ou un ``hx-include``
peut alimenter — la sentinelle
``tests/regression/test_htmx_include_blank_tolerant.py`` le vérifie pour toutes
les routes réellement câblées à un ``hx-include``.

À ne pas confondre avec :

* :func:`app.utils.forms.form_str` — lecture d'un champ de **formulaire** posté,
  qui protège d'un fichier envoyé sous un nom de champ texte ;
* les helpers ``_opt_int`` / ``_opt_date`` des routeurs, qui parsent des valeurs
  de **``Form(...)``** déjà reçues.

Ici on agit **avant** la validation, sur la valeur brute de la query-string.
"""

from __future__ import annotations

from typing import Annotated, TypeVar

from pydantic import BeforeValidator

_T = TypeVar("_T")


def blank_to_none(value: object) -> object:
    """``""`` (ou une chaîne d'espaces) ⇒ ``None`` ; tout le reste inchangé.

    On ne touche qu'à la chaîne vide : une valeur non vide invalide doit
    continuer à lever, sinon une saisie fautive passerait pour une absence de
    saisie — et l'écran afficherait un résultat sans dire qu'il a ignoré ce
    qu'on lui a demandé.
    """
    if isinstance(value, str) and not value.strip():
        return None
    return value


#: ``int`` optionnel tolérant au champ vide (``?leg_id=`` ⇒ ``None``).
OptionalInt = Annotated[int | None, BeforeValidator(blank_to_none)]

#: ``float`` optionnel tolérant au champ vide.
OptionalFloat = Annotated[float | None, BeforeValidator(blank_to_none)]

#: ``bool`` optionnel tolérant au champ vide. Utile pour une case à cocher
#: incluse par ``hx-include`` : décochée, certains navigateurs n'envoient rien,
#: mais un ``<select>`` de type oui/non envoie bien ``""``.
OptionalBool = Annotated[bool | None, BeforeValidator(blank_to_none)]
