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

# Libellés lisibles des champs de formulaire repris dans la note Pipedrive.
_DETAIL_LABELS = {
    "pol": "Port de départ (POL)",
    "pod": "Port d'arrivée (POD)",
    "cargo_nature": "Nature de la marchandise",
    "volume_weight": "Volume / poids",
    "desired_dates": "Dates souhaitées",
    "palettes": "Palettes",
    "tonnage_t": "Tonnage (t)",
    "hazardous": "Marchandises dangereuses (IMDG)",
    "quote_reference": "Référence devis",
    "quote_total_eur": "Total devis (EUR)",
}


def _build_note(
    *,
    source: str,
    name: str,
    company: str | None,
    email: str | None,
    phone: str | None,
    message: str | None,
    details: dict[str, str | None] | None,
    leg_code: str | None,
) -> str:
    """Construit le contenu HTML de la note Pipedrive — formulaire complet + leg."""
    rows: list[str] = [
        f"<b>Demande entrante ({source})</b>",
        f"Nom : {name}",
        f"Société : {company or '—'}",
        f"Email : {email or '—'}",
        f"Téléphone : {phone or '—'}",
    ]
    if leg_code:
        rows.append(f"Voyage (leg) : {leg_code}")
    for key, value in (details or {}).items():
        if value in (None, ""):
            continue
        label = _DETAIL_LABELS.get(key, key)
        rows.append(f"{label} : {value}")
    if message:
        rows += ["", "Message :", message]
    return "<br>".join(rows)


async def push_lead(
    db,
    *,
    name: str,
    email: str | None = None,
    company: str | None = None,
    phone: str | None = None,
    message: str | None = None,
    source: str = "contact",
    details: dict[str, str | None] | None = None,
    leg_code: str | None = None,
) -> None:
    """Pousse un lead vers Pipedrive + notifie le commercial. Ne lève jamais.

    ``details`` : champs libres du formulaire (POL/POD, marchandise, volume,
    dates…) repris intégralement dans la note Pipedrive. ``leg_code`` : voyage
    concerné le cas échéant.
    """
    display = company or name

    # a) Pipedrive — find_or_create org → deal dans le pipeline cible
    #    (« Dealsfromweb » par défaut) → note reprenant tout le formulaire.
    if settings.pipedrive_api_token:
        try:
            org = await pipedrive.find_or_create_organization(display)
            org_id = (org or {}).get("id")

            # Pipeline de destination des leads web (résolu par nom).
            pipeline_id = await pipedrive.find_pipeline_id(settings.pipedrive_pipeline_name)
            stage_id = await pipedrive.first_stage_id(pipeline_id) if pipeline_id else None

            deal = await pipedrive.create_deal(
                f"Lead {source} — {display}",
                org_id=org_id,
                value=None,
                pipeline_id=pipeline_id,
                stage_id=stage_id,
            )
            deal_id = (deal or {}).get("id")

            note = _build_note(
                source=source,
                name=name,
                company=company,
                email=email,
                phone=phone,
                message=message,
                details=details,
                leg_code=leg_code,
            )
            await pipedrive.add_note(note, deal_id=deal_id, org_id=org_id)
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
