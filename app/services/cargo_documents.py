"""ONB-02 — documents cargo guidés (formulaires structurés par type).

Reprise V2 : restaure les **modèles guidés** de documents de bord (NOR,
re-tender, certificat de cales, comptes rendus de réunion, 6 lettres de
protestation, Mate's Receipt) avec leurs champs spécifiques, leurs mentions
légales pré-remplies et le choix du signataire parmi l'équipage embarqué.

Le contenu structuré est sérialisé en JSON dans ``CargoDocument.data_json`` ;
le rendu PDF est piloté par ``doc_rows`` (liste de couples libellé/valeur).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

# ── Mentions légales pré-remplies (international maritime — conservées en EN) ──
LOP_RESERVE = (
    "We hereby reserve all our rights and those of our owners, charterers, and "
    "insurers, and hold you and/or your principals responsible for all consequences, "
    "losses and expenses arising therefrom."
)
MATES_CONDITION = "Received in apparent good order and condition"
NOR_RT_NOTE = "Without prejudice to any previous Notice of Readiness tendered."


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str = "text"  # text | textarea | date | time | crew
    default: str = ""
    rows: int = 3


@dataclass(frozen=True)
class DocType:
    label: str
    fields: list[Field]
    recipient: str | None = None  # champ → party_name (affichage liste)
    legal_note: str = ""


def _f(name: str, label: str, kind: str = "text", *, default: str = "", rows: int = 3) -> Field:
    return Field(name=name, label=label, kind=kind, default=default, rows=rows)


_LOP_FIELDS: list[Field] = [
    _f("to", "Destinataire", default="TO WHOM IT MAY CONCERN"),
    _f("port", "Port"),
    _f("lop_date", "Date", "date"),
    _f("lop_time", "Heure (LT)", "time"),
    _f("subject", "Objet de la protestation", "textarea", rows=3),
    _f("details", "Détails et faits", "textarea", rows=6),
    _f("reserve", "Réserve de droits", "textarea", default=LOP_RESERVE, rows=4),
    _f("master_name", "Commandant (signataire)", "crew"),
    _f("countersigned", "Contresigné par (sans préjudice)"),
]

_LOP_LABELS = {
    "LOP_FP": "Letter of Protest — Free Pratique",
    "LOP_DELAYS": "Letter of Protest — Delays & Restrictions",
    "LOP_DOCUMENT": "Letter of Protest — Documentation",
    "LOP_QTY": "Letter of Protest — Quantity",
    "LOP_DEADFREIGHT": "Letter of Protest — Deadfreight",
    "LOP_OTHER": "Letter of Protest — Other",
}


CARGO_DOC_TYPES: dict[str, DocType] = {
    "NOR": DocType(
        label="Notice of Readiness",
        recipient="to_charterer",
        fields=[
            _f("to_charterer", "Destinataire (affréteur)"),
            _f("port", "Port"),
            _f("notice_date", "Date du notice", "date"),
            _f("notice_time", "Heure (LT)", "time"),
            _f("cargo_desc", "Description de la marchandise", "textarea"),
            _f("position", "Position (rade / à quai / mouillage)"),
            _f("remarks", "Remarques", "textarea"),
            _f("master_name", "Commandant (signataire)", "crew"),
            _f("agent_stamp", "Cachet agent / terminal"),
        ],
    ),
    "NOR_RT": DocType(
        label="Notice of Readiness — Re-Tendered",
        recipient="to_charterer",
        legal_note=NOR_RT_NOTE,
        fields=[
            _f("to_charterer", "Destinataire (affréteur)"),
            _f("port", "Port"),
            _f("reason", "Motif du re-tender", "textarea"),
            _f("notice_date", "Date du notice", "date"),
            _f("notice_time", "Heure (LT)", "time"),
            _f("master_name", "Commandant (signataire)", "crew"),
        ],
    ),
    "HOLDS_CERT": DocType(
        label="Holds Readiness Certificate",
        recipient="to",
        fields=[
            _f("to", "Destinataire"),
            _f("port", "Port"),
            _f("cargo", "Marchandise à charger"),
            _f("inspection_date", "Date d'inspection", "date"),
            _f("holds_list", "Cales inspectées", "textarea"),
            _f("observations", "Résultat / observations", "textarea"),
            _f("officer_name", "Officier (Chief Officer / Commandant)", "crew"),
            _f("surveyor", "Surveyor / cachet terminal"),
        ],
    ),
    "KEY_MEETING": DocType(
        label="Key Transfer Meeting",
        fields=[
            _f("port", "Port"),
            _f("meeting_date", "Date de réunion", "date"),
            _f("attendees", "Participants", "textarea"),
            _f("key_points", "Points clés discutés", "textarea", rows=6),
            _f("actions", "Actions / suivi", "textarea"),
        ],
    ),
    "PRE_MEETING": DocType(
        label="Pre-Loading / Discharging Meeting",
        fields=[
            _f("port", "Port"),
            _f("meeting_date", "Date de réunion", "date"),
            _f("terminal", "Terminal / contact"),
            _f("safety", "Précautions de sécurité", "textarea"),
            _f("plan", "Plan de chargement / déchargement", "textarea", rows=6),
            _f("emergency", "Procédures d'urgence", "textarea"),
        ],
    ),
    "MATES_RECEIPT": DocType(
        label="Mate's Receipt",
        recipient="shipper",
        fields=[
            _f("port_loading", "Port de chargement"),
            _f("receipt_date", "Date", "date"),
            _f("shipper", "Chargeur (shipper)"),
            _f("cargo_desc", "Description de la marchandise", "textarea", rows=4),
            _f("packages", "Nombre de colis"),
            _f("weight", "Poids brut (kg)"),
            _f("condition", "État / remarques", "textarea", default=MATES_CONDITION),
            _f("officer_name", "Officier (Chief Officer)", "crew"),
        ],
    ),
}

# Les 6 lettres de protestation partagent le même schéma.
for _code, _label in _LOP_LABELS.items():
    CARGO_DOC_TYPES[_code] = DocType(label=_label, recipient="to", fields=list(_LOP_FIELDS))


def doc_type_choices() -> list[tuple[str, str]]:
    """(code, libellé) de tous les types guidés, pour le sélecteur."""
    return [(code, dt.label) for code, dt in CARGO_DOC_TYPES.items()]


def field_defaults(kind: str, prefill: dict | None = None) -> dict[str, str]:
    """Valeurs par défaut d'un type : mentions légales + pré-remplissage contextuel."""
    dt = CARGO_DOC_TYPES[kind]
    prefill = prefill or {}
    out: dict[str, str] = {}
    for f in dt.fields:
        if f.default:
            out[f.name] = f.default
        elif f.kind == "date":
            out[f.name] = prefill.get("date_today", date.today().isoformat())
        elif f.name in ("port", "port_loading"):
            out[f.name] = prefill.get("current_port", "")
    return out


def coerce_doc_form(kind: str, form: dict) -> dict[str, str]:
    """Ne retient QUE les champs du schéma du type (anti mass-assignment)."""
    dt = CARGO_DOC_TYPES[kind]
    return {f.name: (form.get(f.name) or "").strip() for f in dt.fields}


def doc_rows(kind: str, data: dict) -> list[tuple[str, str]]:
    """Couples (libellé, valeur) pour le rendu PDF, dans l'ordre du schéma."""
    dt = CARGO_DOC_TYPES.get(kind)
    if dt is None:
        return [(k, str(v)) for k, v in (data or {}).items()]
    return [(f.label, (data or {}).get(f.name, "") or "—") for f in dt.fields]


def parse_data_json(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else {}
    except (ValueError, TypeError):
        return {}


def recipient_of(kind: str, data: dict) -> str | None:
    """Destinataire principal (→ party_name) selon le type."""
    dt = CARGO_DOC_TYPES.get(kind)
    if dt is None or dt.recipient is None:
        return None
    return (data.get(dt.recipient) or "").strip() or None
