"""Un fichier posté dans un champ texte ne doit pas faire tomber la route.

``FormData.get`` renvoie ``UploadFile | str | None``. Le motif répandu
``(form.get("x") or "").strip()`` appelle donc ``.strip()`` sur un ``UploadFile``
dès qu'un client poste un fichier sous le nom d'un champ texte : ``AttributeError``,
et la route répond **500** au lieu de rejeter la saisie.

``form_str`` traite le fichier comme une valeur absente — l'erreur remonte alors
par la validation métier, pas par une erreur serveur — tout en reproduisant à
l'identique la sémantique du motif d'origine (absent **ou vide** ⇒ défaut).
"""

from __future__ import annotations

import io

from starlette.datastructures import FormData, UploadFile

from app.utils.forms import form_str


def test_a_posted_file_is_treated_as_an_empty_field():
    """Le cas qui levait : un fichier là où la route attend du texte."""
    upload = UploadFile(filename="charge.xlsx", file=io.BytesIO(b"PK\x03\x04"))
    form = FormData([("pol_locode", upload)])

    assert form_str(form, "pol_locode") == ""
    # Et la suite du traitement reste possible — c'était tout l'enjeu.
    assert form_str(form, "pol_locode").strip().upper() == ""


def test_the_default_applies_to_a_file_too():
    upload = UploadFile(filename="x.bin", file=io.BytesIO(b""))
    form = FormData([("palette_format", upload)])

    assert form_str(form, "palette_format", "EPAL") == "EPAL"


def test_semantics_match_the_pattern_it_replaces():
    """Absent ou vide ⇒ défaut ; sinon la valeur — comme ``(form.get(x) or d)``."""
    form = FormData([("present", "FRLEH"), ("vide", ""), ("espaces", "  FRLEH  ")])

    assert form_str(form, "present") == "FRLEH"
    assert form_str(form, "absent") == ""
    assert form_str(form, "vide") == ""
    assert form_str(form, "absent", "EPAL") == "EPAL"
    assert form_str(form, "vide", "EPAL") == "EPAL"
    # La fonction ne rogne pas d'elle-même : les appelants gardent leur `.strip()`.
    assert form_str(form, "espaces") == "  FRLEH  "


def test_repeated_field_behaves_exactly_like_form_get():
    """Champ répété : ``FormData.get`` retient la **dernière** valeur, pas la première.

    Contre-intuitif, et c'est justement pourquoi le test l'épingle : ``form_str``
    délègue à ``form.get`` et ne doit surtout pas introduire une règle à lui, sinon
    les routes converties changeraient de comportement sans que personne le voie.
    """
    form = FormData([("pol_locode", "FRLEH"), ("pol_locode", "BRRIO")])

    assert form_str(form, "pol_locode") == form.get("pol_locode") == "BRRIO"
