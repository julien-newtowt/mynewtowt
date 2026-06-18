"""SQLAlchemy ORM models for mynewtowt.

Importing this package registers all models against `Base.metadata`,
which is required for `init_db()` (dev) and Alembic auto-generate.
"""

from app.models.activity_log import ActivityLog
from app.models.anemos_certificate import AnemosCertificate
from app.models.blog_post import BlogPost
from app.models.booking import Booking, BookingItem
from app.models.booking_message import BookingMessage
from app.models.chat import ChatConversation, ChatMessage
from app.models.claim import Claim, ClaimTimelineEntry, VesselPosition
from app.models.client_account import ClientAccount
from app.models.client_invoice import ClientInvoice
from app.models.co2_variable import Co2Variable
from app.models.commercial import (
    Client,
    Order,
    OrderAssignment,
    RateGrid,
    RateGridLine,
    RateGridOption,
    RateOffer,
)
from app.models.contact_request import ContactRequest
from app.models.crew import (
    CrewAssignment,
    CrewCertification,
    CrewLeave,
    CrewMember,
    MaradCrewSchedule,
)
from app.models.crew_ticket import CrewTicket
from app.models.employee import Employee
from app.models.escale import DockerShift, EscaleOperation
from app.models.feature_flag import FeatureFlag
from app.models.finance import LegFinance, LegKPI, OpexParameter, PortConfig
from app.models.insurance import InsuranceContract
from app.models.known_device import KnownDevice
from app.models.leg import Leg
from app.models.mfa_recovery_code import MfaRecoveryCode
from app.models.mrv import MRVEvent, MRVParameter
from app.models.news_item import NewsItem
from app.models.news_source import NewsSource
from app.models.noon_report import (
    NoonReport,
    NoonReportEngine,
    NoonReportSail,
    NoonReportWeather,
)
from app.models.notification import Notification
from app.models.onboard_cashbox import (
    CashboxClosure,
    CashboxMovement,
    OnboardCashbox,
)
from app.models.packing_list import (
    PackingList,
    PackingListAudit,
    PackingListBatch,
    PackingListDocument,
    PortalAccessLog,
    PortalMessage,
)
from app.models.planning_share import PlanningShare
from app.models.port import Port
from app.models.quote import Quote, QuoteLine
from app.models.rate_limit import RateLimitAttempt
from app.models.role_permission import RolePermission
from app.models.sof_event import (
    CargoDocument,
    EtaShift,
    OnboardMessage,
    OnboardMessageMention,
    SofEvent,
)
from app.models.stowage import StowageItem, StowagePlan, StowageZoneSpec
from app.models.ticket import Ticket, TicketComment
from app.models.user import User
from app.models.vessel import Vessel
from app.models.watch_log import OnboardChecklist, VisitorLog, WatchLog
from app.models.weather import VesselWeather

__all__ = [
    "ActivityLog",
    "AnemosCertificate",
    "BlogPost",
    "Booking",
    "BookingItem",
    "BookingMessage",
    "CargoDocument",
    "CashboxClosure",
    "CashboxMovement",
    "ChatConversation",
    "ChatMessage",
    "Claim",
    "ClaimTimelineEntry",
    "Client",
    "ClientAccount",
    "ClientInvoice",
    "Co2Variable",
    "ContactRequest",
    "CrewAssignment",
    "CrewCertification",
    "CrewLeave",
    "CrewMember",
    "CrewTicket",
    "DockerShift",
    "Employee",
    "EscaleOperation",
    "EtaShift",
    "FeatureFlag",
    "InsuranceContract",
    "KnownDevice",
    "Leg",
    "LegFinance",
    "LegKPI",
    "MaradCrewSchedule",
    "MRVEvent",
    "MRVParameter",
    "MfaRecoveryCode",
    "NewsItem",
    "NewsSource",
    "NoonReport",
    "NoonReportEngine",
    "NoonReportSail",
    "NoonReportWeather",
    "Notification",
    "OnboardCashbox",
    "OnboardChecklist",
    "OnboardMessage",
    "OnboardMessageMention",
    "OpexParameter",
    "Order",
    "OrderAssignment",
    "PackingList",
    "PackingListAudit",
    "PackingListBatch",
    "PackingListDocument",
    "PlanningShare",
    "Port",
    "PortConfig",
    "PortalAccessLog",
    "PortalMessage",
    "Quote",
    "QuoteLine",
    "RateGrid",
    "RateGridLine",
    "RateGridOption",
    "RateLimitAttempt",
    "RateOffer",
    "RolePermission",
    "SofEvent",
    "StowageItem",
    "StowagePlan",
    "StowageZoneSpec",
    "Ticket",
    "TicketComment",
    "User",
    "Vessel",
    "VesselPosition",
    "VesselWeather",
    "VisitorLog",
    "WatchLog",
]
