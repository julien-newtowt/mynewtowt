"""Helpers timezone & affichage UTC.

Resolve port timezone (IANA), conversion HH:MM entre fuseaux, formatage
offset (+02:00 par ex.). Évite la dépendance à `pytz` en utilisant
`zoneinfo` (stdlib).
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TIMEZONE_CHOICES: tuple[tuple[str, str], ...] = (
    ("UTC",             "UTC"),
    ("Europe/Paris",    "Paris"),
    ("Europe/London",   "Londres"),
    ("Europe/Lisbon",   "Lisbonne"),
    ("America/New_York","New York"),
    ("America/Sao_Paulo","São Paulo"),
    ("America/Recife",  "Recife"),
    ("Asia/Ho_Chi_Minh","Hô Chi Minh-Ville"),
    ("port_local",      "Port local"),
)


def resolve_tz(name: str | None) -> ZoneInfo:
    """Return a ZoneInfo object, falling back to UTC on unknown names."""
    if not name or name == "UTC":
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def utc_offset_label(tz_name: str, at: datetime | None = None) -> str:
    """Return ``+02:00`` style offset for `tz_name` at the given datetime."""
    tz = resolve_tz(tz_name)
    d = (at or datetime.now(timezone.utc)).astimezone(tz)
    off = d.utcoffset() or _zero()
    total = int(off.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


def to_utc(value: datetime, source_tz: str) -> datetime:
    """Interpret a naive datetime as living in `source_tz` and return its UTC equivalent."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=resolve_tz(source_tz))
    return value.astimezone(timezone.utc)


def from_utc(value: datetime, target_tz: str) -> datetime:
    """Convert an aware UTC datetime into `target_tz`."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(resolve_tz(target_tz))


def _zero():
    from datetime import timedelta
    return timedelta(0)
