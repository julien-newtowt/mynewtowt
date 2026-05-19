"""Seed a freshly-initialized DB with demo data for local exploration.

Run via:
  docker compose exec app python -m scripts.seed_demo
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.auth import hash_password
from app.config import settings
from app.database import SessionLocal, init_db
from app.models.client_account import ClientAccount
from app.models.feature_flag import FeatureFlag
from app.models.leg import Leg
from app.models.port import Port
from app.models.user import User
from app.models.vessel import Vessel


async def seed() -> None:
    await init_db()
    async with SessionLocal() as db:
        # ----- Admin user -----
        existing_admin = (
            await db.execute(
                select(User).where(User.username == settings.initial_admin_username)
            )
        ).scalar_one_or_none()
        if not existing_admin:
            db.add(
                User(
                    username=settings.initial_admin_username,
                    email=settings.initial_admin_email,
                    full_name="Admin NEWTOWT",
                    hashed_password=hash_password(settings.initial_admin_password),
                    role="administrateur",
                    is_active=True,
                    must_change_password=False,
                )
            )

        # ----- Commercial user (for booking confirmation flow) -----
        existing_com = (
            await db.execute(select(User).where(User.username == "commercial"))
        ).scalar_one_or_none()
        if not existing_com:
            db.add(
                User(
                    username="commercial",
                    email="commercial@newtowt.eu",
                    full_name="Inès Commerciale",
                    hashed_password=hash_password("Demo!Commercial2026"),
                    role="commercial",
                )
            )

        # ----- Vessels -----
        vessels_def = [
            ("1", "Anemos", "9123456", "FR", 850),
            ("2", "Artemis", "9123457", "FR", 850),
            ("3", "Atlantis", "9123458", "FR", 850),
            ("4", "Atlas", "9123459", "FR", 850),
        ]
        for code, name, imo, flag, cap in vessels_def:
            row = (await db.execute(select(Vessel).where(Vessel.code == code))).scalar_one_or_none()
            if not row:
                db.add(
                    Vessel(
                        code=code,
                        name=name,
                        imo_number=imo,
                        flag=flag,
                        capacity_palettes=cap,
                        default_speed_kn=8.0,
                        default_elongation=1.15,
                        opex_daily_sea_eur=4500.0,
                    )
                )

        # ----- Ports -----
        ports_def = [
            ("FRFEC", "Fécamp", "FR", 49.7565, 0.3712),
            ("FRLEH", "Le Havre", "FR", 49.4944, 0.1079),
            ("USNYC", "New York", "US", 40.6759, -74.0173),
            ("USBOS", "Boston", "US", 42.3601, -71.0578),
            ("BRSSO", "São Sebastião", "BR", -23.7610, -45.4090),
            ("PTPDL", "Ponta Delgada", "PT", 37.7411, -25.6717),
        ]
        for locode, name, country, lat, lon in ports_def:
            row = (await db.execute(select(Port).where(Port.locode == locode))).scalar_one_or_none()
            if not row:
                db.add(
                    Port(locode=locode, name=name, country=country, latitude=lat, longitude=lon)
                )

        # ----- Demo client -----
        existing_client = (
            await db.execute(
                select(ClientAccount).where(ClientAccount.email == "demo@example.com")
            )
        ).scalar_one_or_none()
        if not existing_client:
            db.add(
                ClientAccount(
                    email="demo@example.com",
                    hashed_password=hash_password("Demo!Client2026"),
                    company_name="Acme Wines SAS",
                    contact_name="Léa Demo",
                    country="FR",
                    is_verified=True,
                    segment="recurring",
                )
            )

        # ----- Feature flags (off by default; admin enables in UI) -----
        for key, desc in [
            ("kairos_design_system", "Activate Kairos design system globally"),
            ("booking_platform", "Public booking platform"),
            ("chatbot_kairos_ai", "Kairos AI chatbot widget"),
            ("onboard_v3_layout", "Onboard 4 spaces layout"),
            ("escale_import_export_split", "Escale import/export split"),
            ("analytics_v2_dashboards", "Analytics V2 dashboards"),
            ("mfa_required_admin", "Force MFA for admin role"),
        ]:
            row = (
                await db.execute(select(FeatureFlag).where(FeatureFlag.key == key))
            ).scalar_one_or_none()
            if not row:
                db.add(
                    FeatureFlag(
                        key=key,
                        enabled=True if key in {"kairos_design_system", "booking_platform"} else False,
                        rollout_pct=100 if key in {"kairos_design_system", "booking_platform"} else 0,
                        description=desc,
                    )
                )

        await db.commit()

        # ----- Legs (need port + vessel IDs in DB) -----
        v1 = (await db.execute(select(Vessel).where(Vessel.code == "1"))).scalar_one()
        v2 = (await db.execute(select(Vessel).where(Vessel.code == "2"))).scalar_one()
        v3 = (await db.execute(select(Vessel).where(Vessel.code == "3"))).scalar_one()
        v4 = (await db.execute(select(Vessel).where(Vessel.code == "4"))).scalar_one()
        fec = (await db.execute(select(Port).where(Port.locode == "FRFEC"))).scalar_one()
        leh = (await db.execute(select(Port).where(Port.locode == "FRLEH"))).scalar_one()
        nyc = (await db.execute(select(Port).where(Port.locode == "USNYC"))).scalar_one()
        bos = (await db.execute(select(Port).where(Port.locode == "USBOS"))).scalar_one()
        sso = (await db.execute(select(Port).where(Port.locode == "BRSSO"))).scalar_one()
        pdl = (await db.execute(select(Port).where(Port.locode == "PTPDL"))).scalar_one()

        now = datetime.now(timezone.utc)
        leg_defs = [
            ("1AFRUS6", v1, fec, nyc, 14, 8, Decimal("38"), 850),
            ("1BUSFR6", v1, nyc, fec, 32, 8, Decimal("38"), 850),
            ("2AFRBR6", v2, leh, sso, 21, 18, Decimal("42"), 850),
            ("2BBRFR6", v2, sso, leh, 50, 18, Decimal("42"), 850),
            ("3AFRUS6", v3, fec, bos, 28, 9, Decimal("36"), 850),
            ("4APTUS6", v4, pdl, nyc, 7, 6, Decimal("34"), 850),
        ]
        for leg_code, vessel, pol, pod, days_to_etd, transit_days, price, capacity in leg_defs:
            existing_leg = (
                await db.execute(select(Leg).where(Leg.leg_code == leg_code))
            ).scalar_one_or_none()
            if existing_leg:
                continue
            etd = now + timedelta(days=days_to_etd)
            eta = etd + timedelta(days=transit_days)
            db.add(
                Leg(
                    leg_code=leg_code,
                    vessel_id=vessel.id,
                    departure_port_id=pol.id,
                    arrival_port_id=pod.id,
                    etd_ref=etd,
                    eta_ref=eta,
                    etd=etd,
                    eta=eta,
                    status="planned",
                    is_bookable=True,
                    public_capacity_palettes=capacity,
                    public_price_per_palette_eur=price,
                    booking_close_at=etd - timedelta(days=2),
                )
            )

        await db.commit()
        print("Seed completed.")
        print(f"  Admin login: {settings.initial_admin_username} / {settings.initial_admin_password}")
        print("  Commercial login: commercial / Demo!Commercial2026")
        print("  Demo client: demo@example.com / Demo!Client2026")


if __name__ == "__main__":
    asyncio.run(seed())
