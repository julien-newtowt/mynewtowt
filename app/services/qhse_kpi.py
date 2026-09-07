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

**Q2 (répartition opérationnel / audit) délibérément absente, pas oubliée** :
constaté sur le jeu de données réel du 2026-09-04 — ``report_source`` n'est
JAMAIS positionné à ``internal_audit``/``external_audit`` par l'ingestion
(``qhse_ingestion._import_row`` ne pose que ``operational`` ou
``suspected_test``, ce dernier détournant le même champ pour un tout autre
usage — détection de motif de test, RQ02). Afficher ce graphe aujourd'hui
montrerait « 0 % audit » alors que le cahier des charges chiffre ~33 % de
constats d'audit sur l'échantillon analysé (§4.1) — une fausse précision
(§10.1), pas un vrai zéro. Nécessite d'abord une vraie classification à
l'ingestion (regex sur le préfixe ``Subject`` — cahier des charges §3.3 —
ou un champ dédié), pas juste un graphe sur un champ mal alimenté.

Vue flotte par défaut, filtre navire optionnel — l'architecture traite le
nombre de navires comme une variable (§3.0/§11.8), jamais câblée sur les
2 navires actuellement en exploitation.
"""

from __future__ import annotations

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

# ══════════════════════════════════════════════════════════════ Résultats


@dataclass(frozen=True)
class GradeCount:
    grade: str
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


async def list_vessels_with_reports(db: AsyncSession) -> list[Vessel]:
    """Navires ayant au moins un signalement — alimente le sélecteur de
    filtre. N'affiche pas les navires sans donnée (bruit, pas un filtre utile)."""
    stmt = (
        select(Vessel)
        .where(Vessel.id.in_(select(QhseReport.vessel_id).distinct()))
        .order_by(Vessel.name)
    )
    return (await db.execute(stmt)).scalars().all()
