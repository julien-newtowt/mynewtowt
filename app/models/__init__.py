"""SQLAlchemy ORM models for mynewtowt.

Importing this package registers all models against `Base.metadata`,
which is required for `init_db()` (dev) and Alembic auto-generate.
"""
from app.models.activity_log import ActivityLog
from app.models.booking import Booking, BookingItem
from app.models.chat import ChatConversation, ChatMessage
from app.models.claim import Claim, ClaimTimelineEntry, VesselPosition
from app.models.client_account import ClientAccount
from app.models.client_invoice import ClientInvoice
from app.models.co2_certificate import CO2Certificate
from app.models.commercial import (
    Client, Order, OrderAssignment, RateGrid, RateGridLine, RateOffer,
)
from app.models.crew import (
    CrewAssignment, CrewCertification, CrewLeave, CrewMember,
)
from app.models.crew_ticket import CrewTicket
from app.models.escale import DockerShift, EscaleOperation
from app.models.feature_flag import FeatureFlag
from app.models.finance import LegFinance, LegKPI, OpexParameter, PortConfig
from app.models.insurance import InsuranceContract
from app.models.leg import Leg
from app.models.mrv import MRVEvent, MRVParameter
from app.models.known_device import KnownDevice
from app.models.mfa_recovery_code import MfaRecoveryCode
from app.models.noon_report import NoonReport
from app.models.notification import Notification
from app.models.onboard_cashbox import (
    CashboxClosure, CashboxMovement, OnboardCashbox,
)
from app.models.packing_list import (
    PackingList, PackingListAudit, PackingListBatch, PackingListDocument,
    PortalAccessLog, PortalMessage,
)
from app.models.planning_share import PlanningShare
from app.models.port import Port
from app.models.rate_limit import RateLimitAttempt
from app.models.sof_event import (
    CargoDocument, EtaShift, OnboardMessage, OnboardMessageMention, SofEvent,
)
from app.models.stowage import StowageItem, StowagePlan
from app.models.ticket import Ticket, TicketComment
from app.models.user import User
from app.models.vessel import Vessel
from app.models.watch_log import OnboardChecklist, VisitorLog, WatchLog
from app.models.webauthn_credential import WebAuthnCredential

__all__ = [
    "ActivityLog",
    "Booking", "BookingItem",
    "CargoDocument",
    "CashboxClosure", "CashboxMovement", "OnboardCashbox",
    "ChatConversation", "ChatMessage",
    "Claim", "ClaimTimelineEntry",
    "Client",
    "ClientAccount", "ClientInvoice",
    "CO2Certificate",
    "CrewAssignment", "CrewCertification", "CrewLeave", "CrewMember", "CrewTicket",
    "DockerShift", "EscaleOperation",
    "EtaShift",
    "FeatureFlag",
    "InsuranceContract",
    "Leg", "LegFinance", "LegKPI",
    "KnownDevice",
    "MfaRecoveryCode",
    "MRVEvent", "MRVParameter",
    "NoonReport",
    "Notification",
    "OnboardChecklist", "OnboardMessage", "OnboardMessageMention",
    "OpexParameter", "PortConfig",
    "Order", "OrderAssignment",
    "PackingList", "PackingListAudit", "PackingListBatch", "PackingListDocument",
    "PlanningShare", "Port",
    "PortalAccessLog", "PortalMessage",
    "RateGrid", "RateGridLine", "RateOffer",
    "RateLimitAttempt",
    "SofEvent",
    "StowageItem", "StowagePlan",
    "Ticket", "TicketComment",
    "User",
    "Vessel", "VesselPosition",
    "VisitorLog", "WatchLog",
    "WebAuthnCredential",
]
