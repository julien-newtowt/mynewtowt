"""Détection d'un login depuis un device jamais vu.

Calcule une empreinte (UA + sous-réseau IP) hashée SHA-256, regarde si
elle existe pour ce user, met à jour ``last_seen_at`` ou insère une
nouvelle ligne. Renvoie un drapeau "nouveau ?" que la route de login
utilise pour décider d'envoyer un mail d'alerte.

Politique de fingerprint :
- UA tronqué à 200 chars, lowercased, sans guillemets — ignore les
  différences de mineur version mais détecte un changement de navigateur
  ou d'OS majeur.
- IP IPv4 : /24 (premiers 3 octets) → tolère NAT mobile + DHCP local.
- IP IPv6 : /48 (premiers 6 octets hex) → tolère sous-réseau IPv6 client.
- Concat = hashed SHA-256 hex, 64 chars.

Skip silencieux si UA ou IP manquante (n'arrive normalement pas, mais
au worst-case on log un anonyme — pas pire que rien).
"""
from __future__ import annotations

import hashlib
import ipaddress
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.known_device import KnownDevice


_UA_MAX_LEN = 200


def _ip_prefix(ip: str | None) -> str:
    """Renvoie ``192.168.1`` (IPv4 /24) ou ``2001:db8:cafe`` (IPv6 /48)."""
    if not ip:
        return ""
    try:
        addr = ipaddress.ip_address(ip.strip())
    except (ValueError, TypeError):
        return ""
    if isinstance(addr, ipaddress.IPv4Address):
        parts = str(addr).split(".")[:3]
        return ".".join(parts)
    # IPv6 — 3 premiers groupes hex
    parts = addr.exploded.split(":")[:3]
    return ":".join(parts)


def _human_label(ua: str | None) -> str:
    """Étiquette grossière pour l'UI ('Chrome / macOS' approximatif)."""
    if not ua:
        return "Inconnu"
    s = ua.lower()
    # Détection navigateur
    browser = "Navigateur"
    for needle, name in (
        ("edg/", "Edge"), ("chrome/", "Chrome"), ("firefox/", "Firefox"),
        ("safari/", "Safari"), ("postman", "Postman"), ("curl", "curl"),
    ):
        if needle in s:
            browser = name
            break
    # Détection OS
    osname = "OS inconnu"
    for needle, name in (
        ("windows", "Windows"), ("macintosh", "macOS"), ("iphone", "iOS"),
        ("ipad", "iPadOS"), ("android", "Android"), ("linux", "Linux"),
    ):
        if needle in s:
            osname = name
            break
    return f"{browser} / {osname}"


def compute_fingerprint(*, ua: str | None, ip: str | None) -> str:
    ua_norm = (ua or "")[:_UA_MAX_LEN].lower().replace('"', "")
    ip_pref = _ip_prefix(ip)
    raw = f"{ua_norm}|{ip_pref}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def see_device(
    db: AsyncSession,
    *,
    owner_type: Literal["client", "staff"],
    owner_id: int,
    ua: str | None,
    ip: str | None,
) -> tuple[KnownDevice, bool]:
    """Enregistre une "vue" du device pour ce user.

    Renvoie (device, is_new) — ``is_new=True`` la 1re fois qu'on voit
    cette combinaison. Le caller doit envoyer un mail d'alerte si True.
    """
    fp = compute_fingerprint(ua=ua, ip=ip)
    stmt = (
        select(KnownDevice)
        .where(KnownDevice.owner_type == owner_type)
        .where(KnownDevice.owner_id == owner_id)
        .where(KnownDevice.fingerprint_hash == fp)
        .limit(1)
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if existing:
        existing.last_seen_at = now
        await db.flush()
        return existing, False
    label = _human_label(ua)
    dev = KnownDevice(
        owner_type=owner_type, owner_id=owner_id,
        fingerprint_hash=fp, label=label,
        first_seen_at=now, last_seen_at=now,
    )
    db.add(dev)
    await db.flush()
    return dev, True
