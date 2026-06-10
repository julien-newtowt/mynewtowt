"""Email service — stdlib smtplib + asyncio executor (fire-and-forget).

Pas de dépendance aiosmtplib. On utilise ``smtplib`` (sync, stdlib) dans
``asyncio.get_running_loop().run_in_executor(None, ...)`` pour ne pas
bloquer la boucle d'événements.

Si ``settings.smtp_host`` est ``None`` ou vide, ``send_email()`` est
**no-op** (renvoie False sans lever d'exception). Utile en dev local
pour ne pas avoir à configurer un MTA, et en CI/tests.

Templating : ``render_email(template_name, **ctx)`` charge un template
Jinja2 sous ``app/templates/emails/<template_name>``. Les templates
sont structurés en deux blocs Jinja : ``{% block subject %}…{% endblock %}``
et ``{% block body %}…{% endblock %}`` (texte brut). Optionnellement un
bloc ``{% block html %}…{% endblock %}`` pour la version HTML.

Toutes les erreurs SMTP sont loguées et retournent False — pas de retry,
pas de queue persistante (v3.5 = best-effort alertes). Une vraie file
durable arrivera quand on aura un module de notifications email
massives (newsletter, factures…).
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import Any

from jinja2 import TemplateNotFound

from app.config import settings
from app.templating import templates

logger = logging.getLogger("email")


def render_email(template_stem: str, **ctx: Any) -> tuple[str, str, str | None]:
    """Renvoie (subject, body_text, body_html_or_None).

    Convention : 3 fichiers sous ``app/templates/emails/`` :
      - ``<stem>.subject.txt``  (1 ligne, requis)
      - ``<stem>.body.txt``     (texte brut, requis)
      - ``<stem>.body.html``    (HTML, optionnel)
    """
    try:
        subj_tpl = templates.env.get_template(f"emails/{template_stem}.subject.txt")
        body_tpl = templates.env.get_template(f"emails/{template_stem}.body.txt")
    except TemplateNotFound as e:
        raise ValueError(f"Email template manquant: {e}")

    subject = subj_tpl.render(**ctx).strip()
    body = body_tpl.render(**ctx).strip()
    html = None
    try:
        html_tpl = templates.env.get_template(f"emails/{template_stem}.body.html")
        html = html_tpl.render(**ctx).strip()
    except TemplateNotFound:
        pass
    if not subject or not body:
        raise ValueError(f"Email template {template_stem} vide (subject/body)")
    return subject, body, html


def _send_sync(
    *,
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None,
    reply_to: str | None,
) -> bool:
    """Envoie effectif via smtplib (synchrone, à appeler dans un executor)."""
    if not settings.smtp_host:
        logger.info("SMTP not configured, skipping email to %s (%s)", to, subject)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_address}>"
    msg["To"] = to
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    try:
        if settings.smtp_port == 465:
            client = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15)
        else:
            client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
            client.ehlo()
            try:
                client.starttls()
                client.ehlo()
            except smtplib.SMTPException:
                # serveur sans STARTTLS — on continue en clair (cas typique dev)
                pass
        if settings.smtp_user and settings.smtp_password:
            client.login(settings.smtp_user, settings.smtp_password)
        client.send_message(msg)
        client.quit()
        return True
    except (smtplib.SMTPException, OSError) as e:
        logger.warning("SMTP send failed to %s (%s): %s", to, subject, e)
        return False


async def send_email(
    *,
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
    reply_to: str | None = None,
) -> bool:
    """Envoie un email en background — best-effort, logue échecs."""
    if not to:
        return False
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: _send_sync(
            to=to, subject=subject,
            body_text=body_text, body_html=body_html,
            reply_to=reply_to,
        ),
    )


async def send_template(
    template_stem: str, *, to: str, reply_to: str | None = None, **ctx: Any,
) -> bool:
    """Render + send. Renvoie False si SMTP HS ou template introuvable."""
    try:
        subject, body, html = render_email(template_stem, **ctx)
    except ValueError as e:
        logger.warning("Render failed for %s: %s", template_stem, e)
        return False
    return await send_email(
        to=to, subject=subject, body_text=body, body_html=html, reply_to=reply_to,
    )
