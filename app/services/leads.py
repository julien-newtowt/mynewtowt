"""Relais des leads vers l'équipe commerciale (COM-04).

Chaque demande entrante (formulaire de contact public, autres sources à
venir) est poussée best-effort vers :

a) Pipedrive — organisation + deal (si ``PIPEDRIVE_API_TOKEN`` configuré) ;
b) une notification in-app ciblant le rôle ``commercial`` ;
c) un email texte vers ``settings.commercial_inbox_email`` (si configurée).

**Strictement best-effort** : aucune exception ne remonte à l'appelant.
Un échec CRM/SMTP/notification ne doit jamais faire échouer la demande du
prospect — chaque étape est isolée dans son propre try/except et loguée.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.services import email as email_svc
from app.services import notifications
from app.utils import pipedrive

logger = logging.getLogger("leads")


async def push_lead(
    db,
    *,
    name: str,
    email: str | None = None,
    company: str | None = None,
    phone: str | None = None,
    message: str | None = None,
    source: str = "contact",
) -> None:
    """Pousse un lead vers Pipedrive + notifie le commercial. Ne lève jamais."""
    display = company or name

    # a) Pipedrive — find_or_create org puis deal (no-op sans token).
    if settings.pipedrive_api_token:
        try:
            org = await pipedrive.find_or_create_organization(display)
            org_id = (org or {}).get("id")
            await pipedrive.create_deal(
                f"Lead {source} — {display}",
                org_id=org_id,
                value=None,
            )
        except Exception:
            logger.warning("pipedrive lead push failed for %r", display, exc_info=True)

    # b) Notification in-app vers le rôle commercial (même ciblage par rôle
    #    que notify_eta_shift). Pas encore de page staff dédiée aux leads →
    #    lien vers le module commercial.
    try:
        detail_parts = [name]
        if company:
            detail_parts.append(company)
        if email:
            detail_parts.append(email)
        await notifications.create(
            db,
            type="info",
            title=f"Nouveau lead ({source})",
            detail=" — ".join(detail_parts),
            link="/commercial",
            target_role="commercial",
        )
    except Exception:
        logger.warning("lead notification failed for %r", display, exc_info=True)

    # c) Email texte best-effort vers la boîte commerciale (si configurée).
    if settings.commercial_inbox_email:
        try:
            lines = [
                f"Nouveau lead ({source})",
                "",
                f"Nom      : {name}",
                f"Société  : {company or '—'}",
                f"Email    : {email or '—'}",
                f"Téléphone: {phone or '—'}",
            ]
            if message:
                lines += ["", "Message :", message]
            await email_svc.send_email(
                to=settings.commercial_inbox_email,
                subject=f"Nouveau lead — {name}",
                body_text="\n".join(lines),
                reply_to=email,
            )
        except Exception:
            logger.warning("lead email failed for %r", display, exc_info=True)
