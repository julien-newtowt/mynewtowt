"""SQLAlchemy ORM models for mynewtowt.

Importing this package registers all models against `Base.metadata`,
which is required for `init_db()` (dev) and Alembic auto-generate.
"""
from app.models.activity_log import ActivityLog
from app.models.booking import Booking, BookingItem
from app.models.client_account import ClientAccount
from app.models.client_invoice import ClientInvoice
from app.models.co2_certificate import CO2Certificate
from app.models.feature_flag import FeatureFlag
from app.models.leg import Leg
from app.models.planning_share import PlanningShare
from app.models.port import Port
from app.models.rate_limit import RateLimitAttempt
from app.models.user import User
from app.models.vessel import Vessel

__all__ = [
    "ActivityLog",
    "Booking",
    "BookingItem",
    "ClientAccount",
    "ClientInvoice",
    "CO2Certificate",
    "FeatureFlag",
    "Leg",
    "PlanningShare",
    "Port",
    "RateLimitAttempt",
    "User",
    "Vessel",
]
