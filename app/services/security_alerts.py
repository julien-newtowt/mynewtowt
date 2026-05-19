"""Hooks de notification email sur événements sécurité.

Fire-and-forget : si SMTP non configuré → no-op silencieux.
Tout est encapsulé dans un try/except global — une panne d'email
ne doit JAMAIS bloquer l'action sécurité (désactivation MFA, etc.).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

from app.config import settings
from app.services.email import send_template

logger = logging.getLogger("security_alerts")


async def notify_security_event(
    *,
    to_email: str,
    recipient_name: str,
    event_kind: str,
    event_detail: str | None = None,
) -> None:
    """Envoie le mail générique 'security_event.html'."""
    if not to_email:
        return
    try:
        await send_template(
            "security_event",
            to=to_email,
            recipient_name=recipient_name or to_email,
            event_kind=event_kind,
            event_detail=event_detail or "",
            occurred_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            brand_name=settings.app_name,
        )
    except Exception as e:
        # ne propage JAMAIS — l'action sécu est déjà persistée en DB
        logger.warning("notify_security_event failed for %s: %s", to_email, e)


# ─────────────────────────────────────────────────────────────────────
# Helpers spécifiques (libellés cohérents FR + détail formaté)
# ─────────────────────────────────────────────────────────────────────


def _format_ip_ua(ip: str | None, ua: str | None) -> str:
    parts = []
    if ip:
        parts.append(f"IP {ip}")
    if ua:
        # tronque l'user-agent pour éviter les emails 4k
        parts.append(f"UA {ua[:120]}")
    return " · ".join(parts) if parts else ""


async def notify_mfa_disabled(
    *, to_email: str, recipient_name: str, ip: str | None = None, ua: str | None = None,
) -> None:
    await notify_security_event(
        to_email=to_email, recipient_name=recipient_name,
        event_kind="Double authentification (TOTP) désactivée",
        event_detail=_format_ip_ua(ip, ua),
    )


async def notify_passkey_added(
    *, to_email: str, recipient_name: str, passkey_label: str | None,
    ip: str | None = None, ua: str | None = None,
) -> None:
    detail_parts = []
    if passkey_label:
        detail_parts.append(f"« {passkey_label} »")
    iu = _format_ip_ua(ip, ua)
    if iu:
        detail_parts.append(iu)
    await notify_security_event(
        to_email=to_email, recipient_name=recipient_name,
        event_kind="Nouvelle passkey enregistrée",
        event_detail=" · ".join(detail_parts) if detail_parts else None,
    )


async def notify_passkey_deleted(
    *, to_email: str, recipient_name: str, passkey_label: str | None,
    ip: str | None = None, ua: str | None = None,
) -> None:
    detail_parts = []
    if passkey_label:
        detail_parts.append(f"« {passkey_label} »")
    iu = _format_ip_ua(ip, ua)
    if iu:
        detail_parts.append(iu)
    await notify_security_event(
        to_email=to_email, recipient_name=recipient_name,
        event_kind="Passkey supprimée",
        event_detail=" · ".join(detail_parts) if detail_parts else None,
    )


async def notify_password_changed(
    *, to_email: str, recipient_name: str,
    ip: str | None = None, ua: str | None = None,
) -> None:
    await notify_security_event(
        to_email=to_email, recipient_name=recipient_name,
        event_kind="Mot de passe modifié",
        event_detail=_format_ip_ua(ip, ua),
    )


async def notify_new_device_login(
    *, to_email: str, recipient_name: str, ip: str | None, ua: str | None,
) -> None:
    """Login depuis un device jamais vu — alerte forte."""
    await notify_security_event(
        to_email=to_email, recipient_name=recipient_name,
        event_kind="Connexion depuis un nouvel appareil",
        event_detail=_format_ip_ua(ip, ua),
    )
