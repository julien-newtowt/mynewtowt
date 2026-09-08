"""QHSE — indicateurs du tableau de bord (Phase 1, premier lot visuel).

Reprend le sous-ensemble d'indicateurs du cahier des charges (§5) directement
calculables sur les champs Phase 0, sans attendre les compléments de collecte
listés en §3.6 :

- **O1** — volume de signalements, tendance 12 mois.
- **C1/C2** — écart de clôture entre l'action corrective (containment) et
  l'évaluation de cause racine (prévention). C'est **le constat le plus
  actionnable du jeu de données analysé** (cahier des charges §4.3) : la
  prévention se referme nettement moins souvent que le correctif immédiat.
  Affichés côte à côte, jamais un seul agrégé — c'est l'écart lui-même qui
  est le signal.
- **R1** — complétude des champs clés (cause racine, description
  corrective, responsable identifié) : une donnée manquante n'est pas
  invisible, elle est comptée.

Volontairement **différés** (pas dans ce premier tableau de bord) : S1/S3
(nécessitent une donnée d'exposition — jours de mer, heures embarquées —
non disponible, §3.6) ; S2, plus pertinent une fois plusieurs navires
réellement comparables ; Q3 (référentiel de codes de déficience non encore
construit) ; les espaces de travail par rôle (§6.2-§6.5) — un tableau de
bord unique pour l'instant, pas de RBAC différencié par persona.

**Q2 — origine de l'émetteur** (remplace la « répartition opérationnel /
audit » du cahier des charges §4.1, et explique pourquoi elle avait été
différée).

``report_source`` ne peut pas porter cet indicateur : l'ingestion n'y écrit
que ``operational`` ou ``suspected_test``, jamais ``internal_audit`` /
``external_audit`` — et ``suspected_test`` n'appartient même pas à
l'énumération déclarée ``QHSE_REPORT_SOURCES``, le champ étant détourné pour
un tout autre usage (détection de motif de test, RQ02). Un graphe posé
dessus afficherait « 0 % audit », une fausse précision (§10.1).

La donnée réelle porte en revanche l'**émetteur**, et lui distingue nettement
trois origines (relevé sur les 188 lignes de la flotte, 2026-09-04) :
autorités externes (Centre de Sécurité des Navires, Transport Canada, USCG —
24 lignes), siège (``TOWT COMPANY`` — 46), bord (commandant, chef mécanicien,
second — 118).

On encode donc **le fait — bord / siège / autorité externe — et non
l'interprétation**. Classer le siège en « audit interne » serait une lecture
que la donnée ne porte pas : un signalement émis à terre n'est pas
nécessairement un audit. Regrouper les origines autrement reste un choix
d'affichage, sans reprise de données.

Trois conséquences de conception, toutes délibérées :

- **Dérivation à la lecture, pas à l'ingestion.** Aucune colonne, aucune
  migration, aucun ré-import quand l'heuristique évolue — et cela respecte
  D10 (le FMS reste l'unique outil de saisie, MyTOWT analyse). C'est aussi
  pourquoi ``QhseReport.reporter_organization_type``, colonne prévue pour
  cela mais jamais alimentée, reste inutilisée : elle figerait à l'import une
  classification qui doit pouvoir être révisée.
- **``indetermine`` est une valeur de premier rang**, jamais un repli
  silencieux vers « bord ». Un émetteur inconnu du référentiel de motifs doit
  se voir, sinon le graphe paraît exhaustif alors qu'il ne l'est pas.
- Les motifs sont **codés et lisibles** ci-dessous plutôt que devinés. S'ils
  se multiplient (nouvelles autorités, nouveaux pavillons), les basculer en
  table paramétrable est l'étape naturelle — pas avant.

Vue flotte par défaut, filtre navire optionnel — l'architecture traite le
nombre de navires comme une variable (§3.0/§11.8), jamais câblée sur les
2 navires actuellement en exploitation.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.qhse import (
    QHSE_GRADES,
    CorrectiveAction,
    QhseReport,
    RootCauseEvaluation,
)
from app.models.vessel import Vessel
from app.services.planning import ensure_utc

# ═════════════════════════════════════════ Origine de l'émetteur (Q2)

#: Origines possibles. ``indetermine`` est une valeur de premier rang : un
#: émetteur non reconnu se voit, il ne se range pas par défaut dans « bord ».
ISSUER_ORIGIN_ONBOARD = "onboard"
ISSUER_ORIGIN_SHORE = "shore"
ISSUER_ORIGIN_EXTERNAL = "external"
ISSUER_ORIGIN_UNKNOWN = "indetermine"

ISSUER_ORIGINS: tuple[str, ...] = (
    ISSUER_ORIGIN_ONBOARD,
    ISSUER_ORIGIN_SHORE,
    ISSUER_ORIGIN_EXTERNAL,
    ISSUER_ORIGIN_UNKNOWN,
)

#: Autorités externes — États du pavillon, contrôles par l'État du port,
#: sociétés de classification. Motifs volontairement larges (familles) plutôt
#: qu'une liste de noms propres : « Transport Canada » et
#: « TRANSPORT CANADA [Sync] » doivent tomber au même endroit.
_EXTERNAL_PATTERNS: tuple[str, ...] = (
    "centre de securite des navires",
    "transport canada",
    "uscg",
    "coast guard",
    "garde cotiere",
    "port state",
    "flag state",
    "maritime authority",
    "bureau veritas",
    "lloyd",
    "class nk",
    "classnk",
    "dnv",
    "rina",
)

#: Fonctions de bord. ``c/o``/``c/e`` sont les abréviations réellement
#: présentes dans l'export du FMS.
_ONBOARD_PATTERNS: tuple[str, ...] = (
    "master",
    "commandant",
    "c/o",
    "c/e",
    "chief officer",
    "chief engineer",
    "second capitaine",
    "bosun",
    "matelot",
)

#: Siège. Le libellé réel est « TOWT COMPANY » ; « armement »/« siege »
#: couvrent les variantes francophones plausibles.
_SHORE_PATTERNS: tuple[str, ...] = ("company", "siege", "armement", "hseq", "qhse manager")


def _fold(text: str) -> str:
    """Minuscules sans accents — comparaison robuste aux variantes de saisie."""
    stripped = unicodedata.normalize("NFKD", text)
    return "".join(c for c in stripped if not unicodedata.combining(c)).lower()


def classify_issuer_origin(
    *,
    issued_by_raw: str | None,
    has_crew_link: bool = False,
    has_user_link: bool = False,
    vessel_names: frozenset[str] = frozenset(),
) -> str:
    """Origine de l'émetteur d'un signalement — bord / siège / autorité externe.

    Heuristique **explicite et révisable**, appliquée dans cet ordre :

    1. un émetteur identifié comme membre d'équipage est du bord, et un
       émetteur identifié comme utilisateur MyTOWT est à terre. Ces deux liens
       sont posés par l'ingestion sur un nom réellement rapproché du
       référentiel — c'est plus sûr que n'importe quel motif textuel.
       ⚠️ Limite connue : un marin qui possède aussi un compte utilisateur est
       rapproché de l'utilisateur en premier (``qhse_ingestion`` ne consulte
       l'équipage que si aucun utilisateur ne correspond) et sera donc classé
       « siège ». Le cas ne se présente pas sur les données actuelles.
    2. autorité externe avant tout motif interne : un contrôle par l'État du
       port cite parfois un navire, et ce n'est pas le navire qui signale.
    3. fonction de bord, ou mention d'un nom de navire de la flotte.
    4. siège.
    5. sinon ``indetermine`` — y compris pour un ``TOWT`` seul, trop ambigu
       pour être attribué (5 lignes réelles dans ce cas).
    """
    if has_crew_link:
        return ISSUER_ORIGIN_ONBOARD
    if has_user_link:
        return ISSUER_ORIGIN_SHORE

    if not issued_by_raw or not issued_by_raw.strip():
        return ISSUER_ORIGIN_UNKNOWN

    folded = _fold(issued_by_raw)

    if any(p in folded for p in _EXTERNAL_PATTERNS):
        return ISSUER_ORIGIN_EXTERNAL
    if any(p in folded for p in _ONBOARD_PATTERNS):
        return ISSUER_ORIGIN_ONBOARD
    if any(name and name in folded for name in vessel_names):
        return ISSUER_ORIGIN_ONBOARD
    if any(p in folded for p in _SHORE_PATTERNS):
        return ISSUER_ORIGIN_SHORE
    return ISSUER_ORIGIN_UNKNOWN


# ══════════════════════════════════════════════════════════════ Résultats


@dataclass(frozen=True)
class GradeCount:
    grade: str
    label_key: str
    count: int


@dataclass(frozen=True)
class OriginCount:
    origin: str
    label_key: str
    count: int


@dataclass(frozen=True)
class TrendPoint:
    label: str
    count: int


@dataclass(frozen=True)
class OpenItem:
    id: int
    subject: str
    grade: str
    issued_date: datetime
    days_open: int


@dataclass(frozen=True)
class QhseDashboard:
    vessel_id: int | None
    total_reports: int
    open_count: int

    grade_counts: list[GradeCount] = field(default_factory=list)
    # Q2 — toujours les 4 origines, `indetermine` comprise même à zéro : sa
    # part est l'indicateur de fiabilité du graphe lui-même.
    origin_counts: list[OriginCount] = field(default_factory=list)
    trend: list[TrendPoint] = field(default_factory=list)
    open_items: list[OpenItem] = field(default_factory=list)

    # R1 — Decimal|None : None si aucun rapport (pas un 0% qui mentirait).
    field_completeness_pct: Decimal | None = None

    # C1 — dénominateur explicite : le taux ne porte que sur les actions
    # DÉJÀ finalisées, jamais sur le total (cahier des charges §5.7 C1 : les
    # lignes sans date de finalisation doivent être « inconnues », pas
    # silencieusement exclues du calcul en paraissant 100 % à l'heure).
    corrective_on_time_pct: Decimal | None = None
    corrective_finished_count: int = 0
    corrective_total_count: int = 0

    # C2 — dénominateur = total des rapports (cahier des charges §5.7 C2),
    # pas seulement les évaluations créées.
    root_cause_completion_pct: Decimal | None = None
    root_cause_finished_count: int = 0


# ══════════════════════════════════════════════════════════════ Calcul


async def build_dashboard(
    db: AsyncSession,
    *,
    vessel_id: int | None = None,
    months: int = 12,
    now: datetime | None = None,
) -> QhseDashboard:
    now = now or datetime.now(UTC)

    stmt = select(QhseReport)
    if vessel_id is not None:
        stmt = stmt.where(QhseReport.vessel_id == vessel_id)
    reports = (await db.execute(stmt)).scalars().all()
    total = len(reports)

    if total == 0:
        return QhseDashboard(
            vessel_id=vessel_id,
            total_reports=0,
            open_count=0,
            grade_counts=[GradeCount(g, f"qhse_grade_{g}", 0) for g in QHSE_GRADES],
            origin_counts=[OriginCount(o, f"qhse_origin_{o}", 0) for o in ISSUER_ORIGINS],
            trend=_trend_points([], months, now),
        )

    report_ids = [r.id for r in reports]
    actions = {
        a.report_id: a
        for a in (
            await db.execute(
                select(CorrectiveAction).where(CorrectiveAction.report_id.in_(report_ids))
            )
        )
        .scalars()
        .all()
    }
    evaluations = {
        e.report_id: e
        for e in (
            await db.execute(
                select(RootCauseEvaluation).where(RootCauseEvaluation.report_id.in_(report_ids))
            )
        )
        .scalars()
        .all()
    }

    grade_tally = dict.fromkeys(QHSE_GRADES, 0)
    for r in reports:
        if r.grade in grade_tally:
            grade_tally[r.grade] += 1

    # ── Q2 — origine de l'émetteur, dérivée à la lecture (cf. docstring du
    # module). Les noms de navires servent de motif « bord » : l'export cite
    # parfois le seul nom du navire comme émetteur (« TOWT ARTEMIS »).
    vessel_names = frozenset(
        _fold(v.name) for v in (await db.execute(select(Vessel))).scalars().all() if v.name
    )
    origin_tally = dict.fromkeys(ISSUER_ORIGINS, 0)
    for r in reports:
        origin_tally[
            classify_issuer_origin(
                issued_by_raw=r.issued_by_raw,
                has_crew_link=r.reporter_crew_member_id is not None,
                has_user_link=r.reporter_user_id is not None,
                vessel_names=vessel_names,
            )
        ] += 1

    open_reports = [r for r in reports if r.closed_date is None]
    open_sorted = sorted(open_reports, key=lambda r: ensure_utc(r.issued_date))
    open_items = [
        OpenItem(
            id=r.id,
            subject=r.subject,
            grade=r.grade,
            issued_date=ensure_utc(r.issued_date),
            days_open=(now - ensure_utc(r.issued_date)).days,
        )
        for r in open_sorted[:10]
    ]

    # ── R1 — complétude pondérée sur 3 champs clés (cahier des charges §5.6) :
    # cause racine, description corrective, au moins un responsable identifié
    # (l'un ou l'autre workflow — l'accountability existe si un nom existe
    # quelque part sur le rapport, pas nécessairement sur les deux).
    completeness_sum = 0.0
    for r in reports:
        action = actions.get(r.id)
        evaluation = evaluations.get(r.id)
        has_root_cause = bool(evaluation and evaluation.root_cause_text)
        has_corrective_desc = bool(action and action.description)
        has_responsible = bool(
            (action and action.responsible_user_id)
            or (evaluation and evaluation.responsible_user_id)
        )
        completeness_sum += (
            int(has_root_cause) + int(has_corrective_desc) + int(has_responsible)
        ) / 3
    field_completeness_pct = Decimal(completeness_sum * 100 / total).quantize(Decimal("0.1"))

    # ── C1 — sur les actions FINALISÉES uniquement, dénominateur explicite.
    finished_actions = [a for a in actions.values() if a.finished_date is not None]
    on_time = sum(
        1 for a in finished_actions if a.limit_date is not None and a.finished_date <= a.limit_date
    )
    corrective_on_time_pct = (
        Decimal(on_time * 100 / len(finished_actions)).quantize(Decimal("0.1"))
        if finished_actions
        else None
    )

    # ── C2 — dénominateur = total des rapports, pas des évaluations créées.
    evaluations_finished = sum(1 for e in evaluations.values() if e.finished_date is not None)
    root_cause_completion_pct = Decimal(evaluations_finished * 100 / total).quantize(Decimal("0.1"))

    return QhseDashboard(
        vessel_id=vessel_id,
        total_reports=total,
        open_count=len(open_reports),
        grade_counts=[GradeCount(g, f"qhse_grade_{g}", grade_tally[g]) for g in QHSE_GRADES],
        origin_counts=[OriginCount(o, f"qhse_origin_{o}", origin_tally[o]) for o in ISSUER_ORIGINS],
        trend=_trend_points(reports, months, now),
        open_items=open_items,
        field_completeness_pct=field_completeness_pct,
        corrective_on_time_pct=corrective_on_time_pct,
        corrective_finished_count=len(finished_actions),
        corrective_total_count=len(actions),
        root_cause_completion_pct=root_cause_completion_pct,
        root_cause_finished_count=evaluations_finished,
    )


def _trend_points(reports: list[QhseReport], months: int, now: datetime) -> list[TrendPoint]:
    """``months`` derniers mois calendaires jusqu'à ``now`` inclus, zéro
    explicite pour les mois sans signalement — jamais un mois manquant."""
    cursor = date(now.year, now.month, 1)
    keys: list[tuple[int, int]] = []
    for _ in range(months):
        keys.append((cursor.year, cursor.month))
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    keys.reverse()

    tally = dict.fromkeys(keys, 0)
    for r in reports:
        issued = ensure_utc(r.issued_date)
        key = (issued.year, issued.month)
        if key in tally:
            tally[key] += 1

    return [TrendPoint(label=f"{m:02d}/{str(y)[2:]}", count=tally[(y, m)]) for (y, m) in keys]


# ══════════════════════════════════════════════════════════════ Géométrie SVG

# Même convention que ``dashboard_perf_router._trend_bars`` (server-rendered,
# pas de lib CDN) — dupliquée volontairement plutôt que factorisée : c'est
# la troisième occurrence de ce patron dans le dépôt (les deux premières
# vivent déjà côte à côte dans ``dashboard_perf_router.py`` sans être
# factorisées non plus), assez petit pour rester local à chaque module.
_CHART_WIDTH = 700
_CHART_HEIGHT = 190
_CHART_TOP = 12
_CHART_BOTTOM = 26
_CHART_SIDE = 8
_CHART_GAP = 8


def trend_bars(trend: list[TrendPoint]) -> tuple[list[dict], dict]:
    """Coordonnées des barres du graphe tendance — SVG server-rendered."""
    n = len(trend)
    plot_h = _CHART_HEIGHT - _CHART_TOP - _CHART_BOTTOM
    plot_w = _CHART_WIDTH - 2 * _CHART_SIDE
    bar_w = ((plot_w - _CHART_GAP * (n - 1)) / n) if n else 0.0
    max_count = max((p.count for p in trend), default=0) or 1

    bars: list[dict] = []
    for i, point in enumerate(trend):
        bar_h = (point.count / max_count) * plot_h
        x = _CHART_SIDE + i * (bar_w + _CHART_GAP)
        y = _CHART_TOP + (plot_h - bar_h)
        bars.append(
            {
                "x": round(x, 1),
                "y": round(y, 1),
                "width": round(max(bar_w, 0.0), 1),
                "height": round(max(bar_h, 0.0), 1),
                "label": point.label,
                "value": point.count,
                "label_x": round(x + bar_w / 2, 1),
            }
        )
    meta = {
        "width": _CHART_WIDTH,
        "height": _CHART_HEIGHT,
        "baseline_y": _CHART_TOP + plot_h,
        "label_y": _CHART_HEIGHT - 8,
    }
    return bars, meta


# ═══════════════════════════════════════ Qualité — quoi corriger, et où

#: Plafond de la liste qualité. Une liste coupée en silence ferait croire à un
#: inventaire complet : la troncature est **annoncée** (``truncated``).
_QUALITY_LIMIT = 200

#: Anomalies qui se corrigent **signalement par signalement**, dans l'ordre
#: d'affichage. ``suspected_test`` d'abord : c'est le seul qui demande une
#: **décision** (garder ou écarter), les autres demandent une saisie manquante.
QUALITY_ISSUES: tuple[str, ...] = (
    "suspected_test",
    "closed_before_issued",
    "missing_root_cause",
    "missing_corrective_description",
)

# 🔴 « Responsable non identifié » n'est PAS une anomalie par ligne.
#
# Deux raisons, l'une de format et l'autre de modèle :
#
# 1. L'export « historique par navire » (16 colonnes) ne porte **aucune**
#    colonne de responsable — les alias ``CorrectiveActionResponsiblePerson``
#    / ``EvaluationResponsiblePerson`` n'existent que dans l'export brut
#    41 colonnes. Sur un fichier de ce format, le champ manque sur 100 % des
#    lignes : ce n'est pas 90 oublis de saisie, c'est une limite du fichier.
# 2. Même dans l'export complet, ``responsible_user_id`` n'est posé que si le
#    nom se rapproche d'un compte MyTOWT existant. Un responsable réel mais
#    sans compte laisse le champ vide.
#
# Le mélanger aux anomalies par ligne rendait la liste inutilisable : les 90
# signalements y figuraient, dont 53 sans rien d'autre à corriger. Il est donc
# compté à part, avec sa part, et jamais un motif de présence dans la liste.
QUALITY_STRUCTURAL_ISSUE = "missing_responsible"


@dataclass(frozen=True)
class QualityItem:
    id: int
    subject: str
    vessel_code: str
    issued_date: datetime
    issues: list[str]


@dataclass(frozen=True)
class QualityReport:
    items: list[QualityItem] = field(default_factory=list)
    #: Nombre de signalements porteurs d'au moins une anomalie **par ligne** —
    #: avant plafond d'affichage.
    total_flagged: int = 0
    total_reports: int = 0
    truncated: bool = False
    #: Compte par motif, sur la totalité (jamais sur la page affichée).
    issue_counts: dict[str, int] = field(default_factory=dict)
    #: Signalements sans responsable identifié — constat structurel, compté à
    #: part (cf. ``QUALITY_STRUCTURAL_ISSUE``).
    responsible_missing_count: int = 0


async def build_quality_report(db: AsyncSession, *, vessel_id: int | None = None) -> QualityReport:
    """Signalements à corriger **dans le FMS**, avec le motif nommé.

    Raison d'être : le tableau de bord dit *combien* de champs manquent (R1),
    jamais *lesquels* — donc rien n'était actionnable. Et l'ingestion promet
    qu'« un humain tranche » les lignes marquées « test présumé » sans qu'aucun
    écran ne le permette.

    Aucune écriture, par construction (D10) : la correction se fait dans le
    FMS, et le ré-import met la ligne à jour au lieu de la dupliquer — ce qui
    n'était possible que depuis la réconciliation ``source_code``. Cet écran
    est la liste de courses, pas un formulaire.
    """
    stmt = select(QhseReport)
    if vessel_id is not None:
        stmt = stmt.where(QhseReport.vessel_id == vessel_id)
    reports = (await db.execute(stmt)).scalars().all()
    if not reports:
        return QualityReport(issue_counts=dict.fromkeys(QUALITY_ISSUES, 0))

    report_ids = [r.id for r in reports]
    actions = {
        a.report_id: a
        for a in (
            await db.execute(
                select(CorrectiveAction).where(CorrectiveAction.report_id.in_(report_ids))
            )
        )
        .scalars()
        .all()
    }
    evaluations = {
        e.report_id: e
        for e in (
            await db.execute(
                select(RootCauseEvaluation).where(RootCauseEvaluation.report_id.in_(report_ids))
            )
        )
        .scalars()
        .all()
    }
    vessel_by_id = {
        v.id: v
        for v in (
            await db.execute(select(Vessel).where(Vessel.id.in_({r.vessel_id for r in reports})))
        )
        .scalars()
        .all()
    }

    issue_counts = dict.fromkeys(QUALITY_ISSUES, 0)
    responsible_missing = 0
    items: list[QualityItem] = []
    for r in reports:
        action = actions.get(r.id)
        evaluation = evaluations.get(r.id)
        issues: list[str] = []

        if r.report_source == "suspected_test":
            issues.append("suspected_test")
        if r.closed_date is not None and ensure_utc(r.closed_date) < ensure_utc(r.issued_date):
            issues.append("closed_before_issued")
        if not (evaluation and evaluation.root_cause_text):
            issues.append("missing_root_cause")
        if not (action and action.description):
            issues.append("missing_corrective_description")

        # Compté, jamais listé (cf. ``QUALITY_STRUCTURAL_ISSUE``).
        if not (
            (action and action.responsible_user_id)
            or (evaluation and evaluation.responsible_user_id)
        ):
            responsible_missing += 1

        if not issues:
            continue
        for issue in issues:
            issue_counts[issue] += 1
        vessel = vessel_by_id.get(r.vessel_id)
        items.append(
            QualityItem(
                id=r.id,
                subject=r.subject,
                vessel_code=vessel.code if vessel else "—",
                issued_date=ensure_utc(r.issued_date),
                issues=issues,
            )
        )

    # Tri : ce qui demande une décision d'abord, puis le plus ancien — même
    # convention que la liste des signalements ouverts.
    items.sort(
        key=lambda it: (
            "suspected_test" not in it.issues,
            it.issued_date,
        )
    )
    return QualityReport(
        items=items[:_QUALITY_LIMIT],
        total_flagged=len(items),
        total_reports=len(reports),
        truncated=len(items) > _QUALITY_LIMIT,
        issue_counts=issue_counts,
        responsible_missing_count=responsible_missing,
    )


async def list_vessels_with_reports(db: AsyncSession) -> list[Vessel]:
    """Navires ayant au moins un signalement — alimente le sélecteur de
    filtre. N'affiche pas les navires sans donnée (bruit, pas un filtre utile)."""
    stmt = (
        select(Vessel)
        .where(Vessel.id.in_(select(QhseReport.vessel_id).distinct()))
        .order_by(Vessel.name)
    )
    return (await db.execute(stmt)).scalars().all()
