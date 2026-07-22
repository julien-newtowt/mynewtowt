"""SQLAlchemy ORM models for mynewtowt.

Importing this package registers all models against `Base.metadata`,
which is required for `init_db()` (dev) and Alembic auto-generate.
"""

from app.models.activity_log import ActivityLog
from app.models.analytics_event import AnalyticsEvent
from app.models.anemos_certificate import AnemosCertificate
from app.models.blog_post import BlogPost
from app.models.booking import Booking, BookingItem
from app.models.booking_message import BookingMessage
from app.models.bunker import BunkerOperation, BunkerTankAllocation
from app.models.chat import ChatConversation, ChatMessage
from app.models.claim import (
    Claim,
    ClaimDocument,
    ClaimProvisionHistory,
    ClaimTimelineEntry,
    VesselPosition,
)
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
from app.models.emission_factor import EmissionFactor
from app.models.employee import Employee
from app.models.employment_contract import EmploymentContract
from app.models.env_report import (
    EnvFieldModification,
    EnvReport,
    EnvReportEventLink,
)
from app.models.escale import DockerShift, EscaleOperation
from app.models.feature_flag import FeatureFlag
from app.models.finance import LegFinance, LegKPI, OpexParameter, PortConfig
from app.models.flgo import FlgoReading, FlgoTankCompartmentVolume, FlgoVoyageConsumptionRef
from app.models.hr_absence import HrAbsence
from app.models.hr_review import HrReview
from app.models.insurance import InsuranceContract
from app.models.known_device import KnownDevice
from app.models.leg import Leg
from app.models.leg_attachment import LegAttachment
from app.models.mfa_recovery_code import MfaRecoveryCode
from app.models.mrv import MRVEvent, MRVParameter
from app.models.mrv_dataset import MrvBunkeringEntry, MrvLogAbstractEntry
from app.models.nav_event import (
    AnchoringEvent,
    ArrivalEvent,
    BeginAnchoringEvent,
    DepartureEvent,
    EndAnchoringEvent,
    NavEvent,
    NavEventEngineReading,
    NavEventHoldReading,
    NavEventSailReading,
    NavEventWeatherReading,
    NoonEvent,
    PortCallEvent,
)
from app.models.news_digest import NewsDigest
from app.models.news_item import NewsItem
from app.models.news_source import NewsSource
from app.models.noon_report import (
    NoonReport,
    NoonReportEngine,
    NoonReportHold,
    NoonReportSail,
    NoonReportWeather,
)
from app.models.notification import Notification
from app.models.onboard_cashbox import (
    CashboxClosure,
    CashboxMovement,
    OnboardCashbox,
)
from app.models.onboard_sales import (
    OnboardProduct,
    OnboardSale,
    OnboardSaleLine,
    OnboardStockMovement,
)
from app.models.packing_list import (
    PackingList,
    PackingListAudit,
    PackingListBatch,
    PackingListDocument,
    PortalAccessLog,
    PortalMessage,
)
from app.models.payroll_variable import PayrollVariable
from app.models.payslip import Payslip
from app.models.planning_scenario import PlanningScenario, ScenarioLeg
from app.models.planning_share import PlanningShare
from app.models.port import Port
from app.models.qhse import (
    CorrectiveAction,
    DeficiencyCode,
    QhseReport,
    QhseReportDeficiencyCode,
    RootCauseEvaluation,
)
from app.models.quote import Quote, QuoteLine
from app.models.rate_limit import RateLimitAttempt
from app.models.role_permission import RolePermission
from app.models.schedule_revision import ScheduleRevision
from app.models.silae_export_batch import SilaeExportBatch
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
from app.models.validation import (
    DashboardParameter,
    QualityCheckResult,
    ValidationRule,
    ValidationRuleThreshold,
)
from app.models.vessel import Vessel
from app.models.vessel_env import VesselEngine, VesselHydrostatics, VesselTank
from app.models.voyage_emission_summary import VoyageEmissionSummary
from app.models.voyage_highlight import VoyageHighlight
from app.models.voyage_photo import VoyagePhoto
from app.models.watch_log import OnboardChecklist, VisitorLog, WatchLog
from app.models.weather import VesselWeather

__all__ = [
    "ActivityLog",
    "AnalyticsEvent",
    "AnchoringEvent",
    "AnemosCertificate",
    "ArrivalEvent",
    "BeginAnchoringEvent",
    "BlogPost",
    "Booking",
    "BookingItem",
    "BookingMessage",
    "BunkerOperation",
    "BunkerTankAllocation",
    "CargoDocument",
    "CashboxClosure",
    "CashboxMovement",
    "ChatConversation",
    "ChatMessage",
    "Claim",
    "ClaimDocument",
    "ClaimProvisionHistory",
    "ClaimTimelineEntry",
    "Client",
    "ClientAccount",
    "ClientInvoice",
    "Co2Variable",
    "ContactRequest",
    "CorrectiveAction",
    "CrewAssignment",
    "CrewCertification",
    "CrewLeave",
    "CrewMember",
    "CrewTicket",
    "DashboardParameter",
    "DeficiencyCode",
    "DepartureEvent",
    "DockerShift",
    "EmissionFactor",
    "Employee",
    "EmploymentContract",
    "EndAnchoringEvent",
    "EnvFieldModification",
    "EnvReport",
    "EnvReportEventLink",
    "EscaleOperation",
    "EtaShift",
    "FeatureFlag",
    "FlgoReading",
    "FlgoTankCompartmentVolume",
    "FlgoVoyageConsumptionRef",
    "HrAbsence",
    "HrReview",
    "InsuranceContract",
    "KnownDevice",
    "Leg",
    "LegAttachment",
    "LegFinance",
    "LegKPI",
    "MRVEvent",
    "MRVParameter",
    "MaradCrewSchedule",
    "MfaRecoveryCode",
    "MrvBunkeringEntry",
    "MrvLogAbstractEntry",
    "NavEvent",
    "NavEventEngineReading",
    "NavEventHoldReading",
    "NavEventSailReading",
    "NavEventWeatherReading",
    "NewsDigest",
    "NewsItem",
    "NewsSource",
    "NoonEvent",
    "NoonReport",
    "NoonReportEngine",
    "NoonReportHold",
    "NoonReportSail",
    "NoonReportWeather",
    "Notification",
    "OnboardCashbox",
    "OnboardChecklist",
    "OnboardMessage",
    "OnboardMessageMention",
    "OnboardProduct",
    "OnboardSale",
    "OnboardSaleLine",
    "OnboardStockMovement",
    "OpexParameter",
    "Order",
    "OrderAssignment",
    "PackingList",
    "PackingListAudit",
    "PackingListBatch",
    "PackingListDocument",
    "PayrollVariable",
    "Payslip",
    "PlanningScenario",
    "PlanningShare",
    "Port",
    "PortCallEvent",
    "PortConfig",
    "PortalAccessLog",
    "PortalMessage",
    "QhseReport",
    "QhseReportDeficiencyCode",
    "QualityCheckResult",
    "Quote",
    "QuoteLine",
    "RateGrid",
    "RateGridLine",
    "RateGridOption",
    "RateLimitAttempt",
    "RateOffer",
    "RolePermission",
    "RootCauseEvaluation",
    "ScenarioLeg",
    "ScheduleRevision",
    "SilaeExportBatch",
    "SofEvent",
    "StowageItem",
    "StowagePlan",
    "StowageZoneSpec",
    "Ticket",
    "TicketComment",
    "User",
    "ValidationRule",
    "ValidationRuleThreshold",
    "Vessel",
    "VesselEngine",
    "VesselHydrostatics",
    "VesselPosition",
    "VesselTank",
    "VesselWeather",
    "VisitorLog",
    "VoyageEmissionSummary",
    "VoyageHighlight",
    "VoyagePhoto",
    "WatchLog",
]
