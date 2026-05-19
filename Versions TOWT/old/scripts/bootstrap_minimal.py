"""Minimal bootstrap after a database reset.

Seeds:
- Admin user with a freshly generated random password (printed once on stdout).
- The 4 fleet vessels declared in app.config.Settings.FLEET.
- Reference ports FRFEC (Fécamp) + BRSSO (Santos, Brésil).
- Default OpexParameter (daily_rate = 11600 EUR/day).
- PortConfig rows for each reference port.
- Default emission parameters (CO2 factors).

Idempotent: skips inserts when rows already exist (matched by natural keys).
Run via:

    docker exec -it towt-app-v2 python3 scripts/bootstrap_minimal.py
"""
from __future__ import annotations

import asyncio
import secrets
import string
import sys
from pathlib import Path
from typing import Optional

# Make `app` importable when the script is invoked directly (python3 scripts/bootstrap_minimal.py)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import select

from app.auth import hash_password
from app.config import get_settings
from app.database import async_session
from app.models.emission_parameter import EmissionParameter
from app.models.finance import OpexParameter, PortConfig
from app.models.port import Port
from app.models.user import User
from app.models.vessel import Vessel


REFERENCE_PORTS = [
    {
        "locode": "FRFEC",
        "name": "Fécamp",
        "country_code": "FR",
        "latitude": 49.7589,
        "longitude": 0.3742,
        "is_shortcut": True,
    },
    {
        "locode": "BRSSO",
        "name": "Santos",
        "country_code": "BR",
        "latitude": -23.9608,
        "longitude": -46.3331,
        "is_shortcut": True,
    },
]

DEFAULT_PORT_CONFIG = {
    "accessible": True,
    "port_cost_total": 0.0,
    "cost_per_palette": 0.0,
    "daily_quay_cost": 0.0,
}

DEFAULT_OPEX_PARAMS = [
    {
        "parameter_name": "opex_daily_rate",
        "parameter_value": 11600.0,
        "unit": "EUR/day",
        "category": "vessel",
        "description": "OPEX journalier moyen flotte (référence)",
    },
]

DEFAULT_EMISSION_PARAMS = [
    {
        "parameter_name": "co2_factor_diesel",
        "parameter_value": 3.206,
        "unit": "kgCO2/kg",
        "description": "Facteur émission CO2 pour le diesel marin (MGO)",
    },
    {
        "parameter_name": "co2_factor_hfo",
        "parameter_value": 3.114,
        "unit": "kgCO2/kg",
        "description": "Facteur émission CO2 pour le fioul lourd (HFO)",
    },
]


def _generate_password(length: int = 24) -> str:
    """Generate a strong random password using URL-safe printable characters."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def _seed_admin(session) -> Optional[str]:
    """Create the admin user if missing. Returns the temporary password (or None if existed)."""
    existing = await session.execute(select(User).where(User.username == "admin"))
    if existing.scalar_one_or_none():
        print("[bootstrap] admin user already exists — skipping")
        return None

    temp_password = _generate_password()
    user = User(
        username="admin",
        email="admin@local",
        hashed_password=hash_password(temp_password),
        full_name="Administrateur",
        role="administrateur",
        language="fr",
        is_active=True,
        must_change_password=True,
    )
    session.add(user)
    await session.flush()
    return temp_password


async def _seed_vessels(session) -> int:
    """Seed the 4 fleet vessels from settings. Returns the number created."""
    fleet = get_settings().FLEET
    created = 0
    for code, info in fleet.items():
        existing = await session.execute(select(Vessel).where(Vessel.code == code))
        if existing.scalar_one_or_none():
            continue
        vessel = Vessel(
            code=code,
            name=info["name"],
            default_speed=8.0,
            default_elongation=1.25,
            max_palettes=850,
            is_active=True,
        )
        session.add(vessel)
        created += 1
    await session.flush()
    return created


async def _seed_ports(session) -> int:
    """Seed reference ports (FRFEC, BRSSO). Returns the number created."""
    created = 0
    for port_def in REFERENCE_PORTS:
        existing = await session.execute(
            select(Port).where(Port.locode == port_def["locode"])
        )
        if existing.scalar_one_or_none():
            continue
        session.add(Port(**port_def))
        created += 1
    await session.flush()
    return created


async def _seed_port_configs(session) -> int:
    """Seed default PortConfig rows for each reference port."""
    created = 0
    for port_def in REFERENCE_PORTS:
        existing = await session.execute(
            select(PortConfig).where(PortConfig.port_locode == port_def["locode"])
        )
        if existing.scalar_one_or_none():
            continue
        session.add(PortConfig(port_locode=port_def["locode"], **DEFAULT_PORT_CONFIG))
        created += 1
    await session.flush()
    return created


async def _seed_opex_params(session) -> int:
    created = 0
    for param in DEFAULT_OPEX_PARAMS:
        existing = await session.execute(
            select(OpexParameter).where(OpexParameter.parameter_name == param["parameter_name"])
        )
        if existing.scalar_one_or_none():
            continue
        session.add(OpexParameter(**param))
        created += 1
    await session.flush()
    return created


async def _seed_emission_params(session) -> int:
    created = 0
    for param in DEFAULT_EMISSION_PARAMS:
        existing = await session.execute(
            select(EmissionParameter).where(EmissionParameter.parameter_name == param["parameter_name"])
        )
        if existing.scalar_one_or_none():
            continue
        session.add(EmissionParameter(**param))
        created += 1
    await session.flush()
    return created


async def bootstrap_minimal() -> dict:
    """Run the full minimal bootstrap. Returns a summary dict."""
    summary = {
        "admin_password": None,
        "vessels_created": 0,
        "ports_created": 0,
        "port_configs_created": 0,
        "opex_params_created": 0,
        "emission_params_created": 0,
    }
    async with async_session() as session:
        try:
            summary["admin_password"] = await _seed_admin(session)
            summary["vessels_created"] = await _seed_vessels(session)
            summary["ports_created"] = await _seed_ports(session)
            summary["port_configs_created"] = await _seed_port_configs(session)
            summary["opex_params_created"] = await _seed_opex_params(session)
            summary["emission_params_created"] = await _seed_emission_params(session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    return summary


def main() -> int:
    summary = asyncio.run(bootstrap_minimal())

    print()
    print("=" * 60)
    print("Minimal bootstrap completed")
    print("=" * 60)
    print(f"  Vessels seeded         : {summary['vessels_created']}")
    print(f"  Ports seeded           : {summary['ports_created']}")
    print(f"  Port configs seeded    : {summary['port_configs_created']}")
    print(f"  OPEX parameters seeded : {summary['opex_params_created']}")
    print(f"  Emission params seeded : {summary['emission_params_created']}")

    if summary["admin_password"]:
        print()
        print("┌" + "─" * 58 + "┐")
        print("│  ADMIN TEMPORARY PASSWORD (shown ONCE — store it now)    │")
        print("│" + " " * 58 + "│")
        print(f"│  username : admin{' ' * 40}│")
        print(f"│  password : {summary['admin_password']:<46}│")
        print("│" + " " * 58 + "│")
        print("│  must_change_password = True (forced rotation on login)  │")
        print("└" + "─" * 58 + "┘")
    else:
        print()
        print("  Admin user already existed — no new password generated.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
