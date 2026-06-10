"""Service de demande de cotation / contact (vitrine publique).

La logique de validation est **pure** (sans I/O) afin d'être couverte par
des tests unitaires sans base de données. ``create_contact_request`` assure
la persistance + le journal d'audit.

Aucun paiement ni transaction : la demande est journalisée puis reprise par
l'équipe commerciale (relais vers la plateforme de réservation de l'extranet).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact_request import ContactRequest

# Validation e-mail volontairement permissive (on ne rejette pas un prospect
# pour une adresse exotique mais valide) tout en filtrant le manifestement faux.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Longueurs alignées sur le modèle (évite une erreur DB sur saisie abusive).
_MAX = {
    "name": 160,
    "company": 200,
    "email": 254,
    "phone": 40,
    "pol": 120,
    "pod": 120,
    "cargo_nature": 200,
    "volume_weight": 120,
    "desired_dates": 120,
    "message": 5000,
}


class ContactValidationError(ValueError):
    """Erreur de validation d'un formulaire de cotation/contact."""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


@dataclass(slots=True)
class ContactPayload:
    """Demande validée et normalisée, prête à être persistée."""

    name: str
    email: str
    company: str | None = None
    phone: str | None = None
    pol: str | None = None
    pod: str | None = None
    cargo_nature: str | None = None
    volume_weight: str | None = None
    desired_dates: str | None = None
    message: str | None = None
    lang: str | None = None


def _clean(value: str | None, field: str) -> str | None:
    """Trim + borne la longueur ; renvoie ``None`` si vide."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    limit = _MAX.get(field)
    if limit and len(value) > limit:
        value = value[:limit]
    return value


def is_spam(honeypot: str | None) -> bool:
    """Anti-spam non bloquant : champ leurre (honeypot) qui doit rester vide.

    Un humain ne voit pas le champ (masqué en CSS) ; un robot le remplit.
    On ne renvoie pas d'erreur visible — l'appelant accuse réception sans
    persister, pour ne pas signaler au robot que la soumission a échoué.
    """
    return bool(honeypot and honeypot.strip())


def validate_contact_payload(
    *,
    name: str | None,
    email: str | None,
    consent: bool,
    company: str | None = None,
    phone: str | None = None,
    pol: str | None = None,
    pod: str | None = None,
    cargo_nature: str | None = None,
    volume_weight: str | None = None,
    desired_dates: str | None = None,
    message: str | None = None,
    lang: str | None = None,
) -> ContactPayload:
    """Valide + normalise une demande. Lève ``ContactValidationError``.

    Champs obligatoires : nom, e-mail (format), consentement RGPD.
    """
    errors: dict[str, str] = {}

    name_c = _clean(name, "name")
    if not name_c:
        errors["name"] = "required"

    email_c = _clean(email, "email")
    if not email_c:
        errors["email"] = "required"
    elif not _EMAIL_RE.match(email_c):
        errors["email"] = "invalid"

    if not consent:
        errors["consent"] = "required"

    if errors:
        raise ContactValidationError(errors)

    return ContactPayload(
        name=name_c,  # type: ignore[arg-type]
        email=email_c,  # type: ignore[arg-type]
        company=_clean(company, "company"),
        phone=_clean(phone, "phone"),
        pol=_clean(pol, "pol"),
        pod=_clean(pod, "pod"),
        cargo_nature=_clean(cargo_nature, "cargo_nature"),
        volume_weight=_clean(volume_weight, "volume_weight"),
        desired_dates=_clean(desired_dates, "desired_dates"),
        message=_clean(message, "message"),
        lang=_clean(lang, "name"),
    )


async def create_contact_request(db: AsyncSession, payload: ContactPayload) -> ContactRequest:
    """Persiste la demande validée. Le journal d'audit est posé par la route."""
    entry = ContactRequest(
        name=payload.name,
        email=payload.email,
        company=payload.company,
        phone=payload.phone,
        pol=payload.pol,
        pod=payload.pod,
        cargo_nature=payload.cargo_nature,
        volume_weight=payload.volume_weight,
        desired_dates=payload.desired_dates,
        message=payload.message,
        lang=payload.lang,
        status="new",
    )
    db.add(entry)
    await db.flush()
    return entry
