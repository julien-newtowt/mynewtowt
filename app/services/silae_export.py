"""Génération du fichier d'export EVP vers Silae (lot L5).

Construit un CSV (séparateur ``;``, BOM UTF-8 pour Excel FR) à partir des
lignes d'éléments variables verrouillées d'une période. Fonction pure et
testable ; l'écriture/persistance du lot est gérée par le routeur.

NB : le format exact des rubriques attendu par Silae reste à confirmer avec
le cabinet de paie (cf. cahier des charges §10.3, question ouverte n°1). Les
colonnes ci-dessous sont génériques et stables ; le mapping ``evp_type`` →
rubrique Silae sera ajouté une fois le format figé.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable

CSV_HEADER: tuple[str, ...] = (
    "matricule",
    "silae_id",
    "nom",
    "prenom",
    "periode",
    "type_evp",
    "libelle",
    "quantite",
    "montant",
    "commentaire",
)


def _fmt(value) -> str:
    if value is None:
        return ""
    return str(value)


def build_evp_csv(rows: Iterable[dict]) -> str:
    """Sérialise des lignes EVP en CSV Silae (séparateur ``;``).

    Chaque ``row`` est un dict aux clés de :data:`CSV_HEADER`. Renvoie une
    chaîne préfixée du BOM UTF-8 (compat Excel FR).
    """
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(CSV_HEADER)
    for row in rows:
        writer.writerow([_fmt(row.get(col)) for col in CSV_HEADER])
    return "﻿" + buf.getvalue()
