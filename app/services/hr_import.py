"""Import de collaborateurs sédentaires par fichier (lot L1).

Parseur CSV pur (sans DB) pour la reprise initiale des effectifs depuis un
export Silae / registre du personnel. Le router appelle ``parse_employees_csv``
puis persiste les lignes valides — voir cahier des charges §8 (reprise par
import fichier + go-live progressif).

Le parseur est volontairement tolérant sur la forme (séparateur ``;`` ou
``,``, en-têtes accentués / casse libre, dates FR ou ISO) et strict sur le
fond (colonnes obligatoires, statut whitelisté, doublons de matricule dans
le fichier signalés).
"""

from __future__ import annotations

import csv
import io
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from app.models.employee import EMPLOYEE_STATUSES

# En-tête normalisé (cf. ``_normalize_header``) → champ du modèle Employee.
COLUMN_ALIASES: dict[str, str] = {
    "matricule": "matricule",
    "first_name": "first_name",
    "prenom": "first_name",
    "last_name": "last_name",
    "nom": "last_name",
    "email_pro": "email_pro",
    "email": "email_pro",
    "phone_pro": "phone_pro",
    "telephone": "phone_pro",
    "tel": "phone_pro",
    "birth_date": "birth_date",
    "date_de_naissance": "birth_date",
    "job_title": "job_title",
    "poste": "job_title",
    "intitule_de_poste": "job_title",
    "department": "department",
    "service": "department",
    "work_location": "work_location",
    "lieu": "work_location",
    "site": "work_location",
    "entry_date": "entry_date",
    "date_entree": "entry_date",
    "date_d_entree": "entry_date",
    "exit_date": "exit_date",
    "date_sortie": "exit_date",
    "status": "status",
    "statut": "status",
    "silae_id": "silae_id",
}

REQUIRED_FIELDS: tuple[str, ...] = ("matricule", "first_name", "last_name")
DATE_FIELDS: tuple[str, ...] = ("birth_date", "entry_date", "exit_date")
DECIMAL_FIELDS: tuple[str, ...] = ()


@dataclass
class RowError:
    line: int
    message: str


@dataclass
class ImportResult:
    rows: list[dict] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _normalize_header(raw: str) -> str:
    """``"Date d'entrée "`` → ``"date_d_entree"`` (sans accent, snake_case)."""
    text = unicodedata.normalize("NFKD", raw or "").encode("ascii", "ignore").decode()
    text = text.strip().lower()
    out = []
    for ch in text:
        out.append(ch if ch.isalnum() else "_")
    # collapse runs of "_" and trim
    normalized = "_".join(filter(None, "".join(out).split("_")))
    return normalized


def _parse_date(value: str) -> date:
    value = value.strip()
    for fmt in ("iso", "fr"):
        try:
            if fmt == "iso":
                return date.fromisoformat(value)
            d, m, y = value.replace(".", "/").replace("-", "/").split("/")
            return date(int(y), int(m), int(d))
        except (ValueError, IndexError):
            continue
    raise ValueError(f"date invalide: {value!r} (attendu AAAA-MM-JJ ou JJ/MM/AAAA)")


def _parse_decimal(value: str) -> Decimal:
    try:
        return Decimal(value.strip().replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"nombre invalide: {value!r}") from exc


def parse_employees_csv(content: str) -> ImportResult:
    """Parse un CSV d'employés en lignes prêtes à insérer.

    Retourne un :class:`ImportResult` (``rows`` valides + ``errors``). Si
    une erreur structurelle (pas d'en-tête, colonne requise absente) est
    détectée, ``rows`` est vide.
    """
    result = ImportResult()
    text = content.lstrip("﻿")  # strip BOM
    if not text.strip():
        result.errors.append(RowError(0, "fichier vide"))
        return result

    # Détection du séparateur sur la première ligne.
    first_line = text.splitlines()[0]
    delimiter = ";" if first_line.count(";") >= first_line.count(",") else ","

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        result.errors.append(RowError(0, "fichier vide"))
        return result

    fields = [COLUMN_ALIASES.get(_normalize_header(h)) for h in header]
    present = {f for f in fields if f}
    missing = [f for f in REQUIRED_FIELDS if f not in present]
    if missing:
        result.errors.append(RowError(1, f"colonnes obligatoires manquantes: {', '.join(missing)}"))
        return result

    seen_matricules: set[str] = set()
    for line_no, raw_row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in raw_row):
            continue  # ligne vide
        row: dict = {}
        row_errors: list[str] = []
        for col_idx, field_name in enumerate(fields):
            if not field_name or col_idx >= len(raw_row):
                continue
            value = raw_row[col_idx].strip()
            if not value:
                continue
            try:
                if field_name in DATE_FIELDS:
                    row[field_name] = _parse_date(value)
                elif field_name in DECIMAL_FIELDS:
                    row[field_name] = _parse_decimal(value)
                elif field_name == "status":
                    if value not in EMPLOYEE_STATUSES:
                        raise ValueError(
                            f"statut invalide: {value!r} "
                            f"(attendu: {', '.join(EMPLOYEE_STATUSES)})"
                        )
                    row[field_name] = value
                else:
                    row[field_name] = value
            except ValueError as exc:
                row_errors.append(str(exc))

        for req in REQUIRED_FIELDS:
            if not row.get(req):
                row_errors.append(f"{req} obligatoire")

        matricule = row.get("matricule")
        if matricule:
            if matricule in seen_matricules:
                row_errors.append(f"matricule en double dans le fichier: {matricule!r}")
            seen_matricules.add(matricule)

        if row_errors:
            result.errors.append(RowError(line_no, " ; ".join(row_errors)))
        else:
            result.rows.append(row)

    return result
