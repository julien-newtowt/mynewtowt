"""Catalogue complet des règles de validation MRV (LOT 8).

Implémente TOUTES les règles restantes du moteur (``@rule``) au-delà des 5
règles structurelles codées au lot 2 (R01/R02/R11/R12/R13, cf.
``validation_engine``) : R03-R10, R14-R26, IR01-IR05. Chaque règle est
documentée par son énoncé Matrice (``Matrice_regles_validation.md``) et ses
amendements (retex MRV 2025-07-08 + Corrective Action Report 2025).

Principes appliqués (Matrice §0 + plan §2.4) :

- **Aucun littéral métier** : chaque seuil est résolu via
  ``validation_engine.get_threshold`` (override par navire, fail-closed). Les
  paramètres manquants au catalogue ont été ajoutés à ``THRESHOLD_SEED``
  (``provisional=True``) — cf. le rapport de lot. Les constantes purement
  *physiques/structurelles* (bornes lat ±90 / lon ±180, LOCODE = 5 caractères,
  24 h/jour) ne sont PAS des seuils métier et restent des constantes documentées.
- **Graduation de sévérité par verdict** : une règle « Bloquant/Warning » de la
  Matrice émet des ``CheckOutcome(severity=...)`` distincts selon la condition
  (hook lot 8 de ``CheckOutcome``/``run_rules``).
- **Robustesse duck-typée** : une règle qui ne trouve pas la donnée qu'elle
  contrôle (attribut absent, pas de contexte leg/prev) **s'abstient** (retourne
  ``[]``) — jamais de faux positif, jamais d'exception. C'est ce qui permet au
  même registre de tourner sur un événement unique (finalisation) comme sur une
  séquence complète (run nocturne) ou des sujets synthétiques (tests).

Réconciliations lot 2 → Matrice (documentées ici et dans ``validation_engine``) :

- **R11** (lot 2) = bornes de plausibilité paramétrées (conso/ROB) ; la Matrice
  visait « ROB annexes urée/eau douce manquants ». Non modelé par ``nav_events``
  → volet N/A ; R11 conservé comme garde générique, ROB principal couvert par
  R06, conso par R08/R15.
- **R13** (lot 2) = chronologie stricte (doublon/antériorité) ; la Matrice visait
  la complétude de champs (Informatif). Le doublon/antériorité est désormais porté
  rigoureusement par **IR01** ; R13 conservé.
- **R25** (lot 7) = Σ compartiments vs total (volet interne). La Matrice décrit
  AUSSI la cohérence entre lectures consécutives → **2ᵉ volet** implémenté
  (``flgo_sync.check_consecutive_consistency``) et appelé par la règle R25.

Déclencheurs (tous passent par ``run_rules`` du moteur) :

- finalisation d'événement (scope ``event``) : déjà branché par ``event_capture`` ;
- validation Master/siège d'un rapport (scope ``report``) : hooks ``mrv_router`` ;
- validation/correction d'un soutage (scope ``bunker``) : hook ``mrv_router`` ;
- import/sync FLGO (scope ``flgo``) : hook ``mrv_router`` ;
- **run nocturne** ``POST /api/mrv/quality-run`` : event + voyage + inter-rapports
  sur les legs actifs (non clôturés) de chaque navire.

Routage des alertes (``route_alerts``, via ``services.notifications``) :
R10 non confirmé → Administrateur ; R24 → Administrateur ; R14 critique →
Manager maritime + Administrateur. Idempotent (dédup 24 h + acquittement).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bunker import BunkerOperation, BunkerTankAllocation
from app.models.env_report import EnvFieldModification, EnvReport
from app.models.flgo import FlgoReading
from app.models.leg import Leg
from app.models.nav_event import EVENT_TYPES
from app.models.port import Port
from app.models.validation import QualityCheckResult, ValidationRule
from app.models.vessel import Vessel
from app.services import referential_env
from app.services.validation_engine import (
    _DATETIME_ATTRS,
    CheckOutcome,
    RuleContext,
    _as_decimal,
    _first,
    _get,
    _norm_dt,
    _present,
    rule,
    run_rules,
)

# ════════════════════════════════════════════════════════════ Constantes physiques
# (NON paramétrables — structure/physique, pas des seuils métier ; documentées.)
_LAT_MIN, _LAT_MAX = Decimal("-90"), Decimal("90")
_LON_MIN, _LON_MAX = Decimal("-180"), Decimal("180")
_LOCODE_LEN = 5
_HOURS_PER_DAY = Decimal("24")
_PORTCALL_TYPES: tuple[str, ...] = ("departure", "arrival")
_ANCHORING_TYPES: tuple[str, ...] = ("anchoring_begin", "anchoring_end")

# Défauts codés (repli ultime si get_threshold renvoie None — ne devrait pas
# arriver, tous ces paramètres sont seedés).
_D = {
    "tolerance_datetime_futur_h": Decimal("24"),
    "delai_confirmation_reset_j": Decimal("3"),
    "seuil_conso_ref_l_j": Decimal("750"),
    "borne_max_rob_t": Decimal("300"),
    "tolerance_distance_manuelle_nm": Decimal("20"),
    "tolerance_distance_haversine_nm": Decimal("20"),
    "tolerance_datetime_escale_h": Decimal("6"),
    "tolerance_duree_rapport_h": Decimal("2"),
    "seuil_rob_ecart_mineur_t": Decimal("0.5"),
    "seuil_rob_ecart_majeur_t": Decimal("2"),
    "seuil_rob_ecart_critique_t": Decimal("5"),
    "duree_escale_alerte_conso_manquante_j": Decimal("2"),
    "conso_estimee_defaut_t_j": Decimal("0.21"),
    "seuil_cargo_mrv_ecart_t": Decimal("5"),
    "tolerance_carbon_noon_conso_t": Decimal("1"),
    "densite_defaut_t_m3": Decimal("0.845"),
    "ir03_min_reports_figes": Decimal("3"),
    "ir03_conso_min_t": Decimal("0.05"),
    "ir05_min_reports_figes": Decimal("3"),
}


async def _thr(ctx: RuleContext, name: str) -> Decimal:
    """Seuil paramétré (snapshotté) ; repli sur le défaut codé si absent."""
    v = await ctx.threshold(name)
    return v if v is not None else _D.get(name, Decimal("0"))


# ════════════════════════════════════════════════════════════ Helpers duck-typés


def _has(subject: Any, name: str) -> bool:
    if isinstance(subject, dict):
        return name in subject
    return hasattr(subject, name)


def _has_any(subject: Any, names: tuple[str, ...]) -> bool:
    return any(_has(subject, n) for n in names)


def _event_type(subject: Any) -> str | None:
    return _get(subject, "event_type")


def _dt(subject: Any) -> datetime | None:
    return _norm_dt(_first(subject, _DATETIME_ATTRS))


def _latlon(subject: Any) -> tuple[Decimal, Decimal] | None:
    lat = _as_decimal(_first(subject, ("lat_decimal", "latitude", "lat")))
    lon = _as_decimal(_first(subject, ("lon_decimal", "longitude", "lon")))
    if lat is None or lon is None:
        return None
    return lat, lon


def _rob(subject: Any) -> Decimal | None:
    """ROB de référence (déclaré) d'un sujet — PortCall (``rob_t``) ou synthèse."""
    return _as_decimal(_first(subject, ("rob_t", "rob_declared_t", "rob")))


def _duration_h(prev: Any, cur: Any) -> Decimal | None:
    a, b = _dt(prev), _dt(cur)
    if a is None or b is None:
        return None
    return Decimal(str((b - a).total_seconds())) / Decimal("3600")


def _distance_nm(prev: Any, cur: Any) -> Decimal | None:
    a, b = _latlon(prev), _latlon(cur)
    if a is None or b is None:
        return None
    from app.services.ports import haversine_nm

    return Decimal(str(haversine_nm(float(a[0]), float(a[1]), float(b[0]), float(b[1]))))


def _engine_fuel_map(subject: Any) -> dict[Any, tuple[Decimal | None, bool, bool, int | None]]:
    """{engine_id: (fuel_l, is_reset, reset_confirmed, reading_id)}.

    Lit ``engine_readings`` (événement réel) ou, à défaut, un scalaire
    ``fuel_counter_l`` + ``is_counter_reset`` (sujet synthétique).
    ``reading_id`` alimente l'action « confirmer le reset » de l'écran
    ``/mrv/qualite`` (R10) — ``None`` pour un sujet synthétique."""
    out: dict[Any, tuple[Decimal | None, bool, bool, int | None]] = {}
    readings = _get(subject, "engine_readings")
    if readings:
        for r in readings:
            out[_get(r, "engine_id")] = (
                _as_decimal(_get(r, "fuel_counter_l")),
                bool(_get(r, "is_counter_reset")),
                _get(r, "reset_confirmed_by") is not None,
                _get(r, "id"),
            )
        return out
    if _has(subject, "fuel_counter_l"):
        out[0] = (
            _as_decimal(_get(subject, "fuel_counter_l")),
            bool(_get(subject, "is_counter_reset")),
            _get(subject, "reset_confirmed_by") is not None
            or bool(_get(subject, "reset_confirmed")),
            None,
        )
    return out


def _fuel_delta_l(prev: Any, cur: Any) -> tuple[Decimal | None, bool]:
    """(Σ delta litres compté, une régression a-t-elle eu lieu ?) entre prev/cur."""
    pm, cm = _engine_fuel_map(prev), _engine_fuel_map(cur)
    if not cm:
        return None, False
    total = Decimal("0")
    seen = False
    regressed = False
    for eid, (cf, _r, _c, _rid) in cm.items():
        pf = pm.get(eid, (None, False, False, None))[0]
        if pf is None or cf is None:
            continue
        seen = True
        d = cf - pf
        if d < 0:
            regressed = True
        total += d
    return (total if seen else None), regressed


async def _r08_missing_engine_readings(ctx: RuleContext) -> list[CheckOutcome]:
    """G2 — compteurs moteur obligatoires à la finalisation de Departure/
    Arrival/Anchoring/Cut-off (le Noon les demande déjà et reste couvert par
    les volets « conso nulle »/« conso hors seuil » ci-dessous).

    S'abstient (règle duck-typée, cf. principes en tête de fichier) si le
    contexte ne permet pas de trancher : pas de navire connu, ou navire sans
    aucun moteur référencé (rien à contrôler)."""
    if ctx.vessel_id is None:
        return []
    engines = await referential_env.get_vessel_engines(ctx.db, ctx.vessel_id)
    if not engines:
        return []
    if _engine_fuel_map(ctx.subject):
        return []
    return [
        CheckOutcome(
            "fail",
            "R08 — compteurs moteur manquants (aucun relevé saisi à cet événement).",
            {"engines_attendus": len(engines)},
            severity="bloquant",
        )
    ]


def _bunker_t(subject: Any) -> Decimal:
    v = _as_decimal(_first(subject, ("bunkered_t", "bunker_t")))
    return v if v is not None else Decimal("0")


def _ok(msg: str = "Contrôle conforme.") -> list[CheckOutcome]:
    return [CheckOutcome("pass", msg)]


# ════════════════════════════════════════════════════════════ Scope EVENT


@rule("R03")
async def _r03_event_type(ctx: RuleContext) -> list[CheckOutcome]:
    """R03 — Type d'événement présent et reconnu (bloquant sinon). Matrice §1."""
    if not _has(ctx.subject, "event_type"):
        return []  # sujet non événementiel → hors périmètre
    et = _event_type(ctx.subject)
    if not _present(et):
        return [
            CheckOutcome(
                "fail",
                "R03 — type d'événement manquant.",
                {"event_type": None},
                severity="bloquant",
            )
        ]
    if et not in EVENT_TYPES:
        return [
            CheckOutcome(
                "fail",
                f"R03 — type d'événement non reconnu : {et!r}.",
                {"event_type": et, "allowed": list(EVENT_TYPES)},
                severity="bloquant",
            )
        ]
    return _ok("R03 — type d'événement valide.")


@rule("R04")
async def _r04_datetime(ctx: RuleContext) -> list[CheckOutcome]:
    """R04 — Date présente (bloquant) et plausible (pas dans le futur au-delà de
    ``tolerance_datetime_futur_h``, warning). Matrice §1 + amendement lot 8."""
    if not _has_any(ctx.subject, _DATETIME_ATTRS):
        return []
    dt = _dt(ctx.subject)
    if dt is None:
        return [
            CheckOutcome(
                "fail",
                "R04 — date manquante (obligatoire MRV).",
                {"datetime": None},
                severity="bloquant",
            )
        ]
    tol_h = await _thr(ctx, "tolerance_datetime_futur_h")
    now = _norm_dt(ctx.now) or datetime.now(UTC).replace(tzinfo=None)
    if dt > now + timedelta(hours=float(tol_h)):
        return [
            CheckOutcome(
                "fail",
                f"R04 — horodatage dans le futur ({dt.isoformat()} > maintenant + {tol_h} h).",
                {"datetime": dt.isoformat(), "now": now.isoformat()},
                severity="warning",
            )
        ]
    return _ok("R04 — date présente et plausible.")


@rule("R05")
async def _r05_position(ctx: RuleContext) -> list[CheckOutcome]:
    """R05 — Position dans les bornes (bloquant hors plage) ; position manuelle
    ⇒ justification obligatoire (bloquant). Formalise la garde du finalize.
    Matrice §1."""
    if not _has_any(
        ctx.subject, ("lat_decimal", "lon_decimal", "latitude", "longitude", "position_source")
    ):
        return []
    lat = _as_decimal(_first(ctx.subject, ("lat_decimal", "latitude", "lat")))
    lon = _as_decimal(_first(ctx.subject, ("lon_decimal", "longitude", "lon")))
    src = _get(ctx.subject, "position_source")
    outs: list[CheckOutcome] = []
    if lat is not None and not (_LAT_MIN <= lat <= _LAT_MAX):
        outs.append(
            CheckOutcome(
                "fail",
                f"R05 — latitude hors plage : {lat}.",
                {"lat": str(lat)},
                severity="bloquant",
            )
        )
    if lon is not None and not (_LON_MIN <= lon <= _LON_MAX):
        outs.append(
            CheckOutcome(
                "fail",
                f"R05 — longitude hors plage : {lon}.",
                {"lon": str(lon)},
                severity="bloquant",
            )
        )
    if src == "manuel_justifie" and (lat is not None or lon is not None):
        just = _get(ctx.subject, "position_justification")
        if not (just and str(just).strip()):
            outs.append(
                CheckOutcome(
                    "fail",
                    "R05 — position saisie manuellement sans justification.",
                    {"position_source": src},
                    severity="bloquant",
                )
            )
    return outs or _ok("R05 — position valide.")


@rule("R06")
async def _r06_rob(ctx: RuleContext) -> list[CheckOutcome]:
    """R06 — ROB de référence présent sur Departure/Arrival, non négatif, ≤ borne
    plausible ; =0 → warning. Matrice §1.

    Note réconciliation : dans le modèle événementiel, le ROB de référence
    n'est porté QUE par les PortCall (jamais le Noon, R14-v2) → R06 ne
    s'applique qu'à Departure/Arrival ; le « ROB=0 en Noon » de la Matrice est
    N/A (le Noon n'a pas de champ ROB)."""
    if _event_type(ctx.subject) not in _PORTCALL_TYPES:
        return []
    if not _has(ctx.subject, "rob_t"):
        return []
    rob = _as_decimal(_get(ctx.subject, "rob_t"))
    if rob is None:
        return [
            CheckOutcome(
                "fail",
                "R06 — ROB de référence manquant à l'escale.",
                {"rob_t": None},
                severity="bloquant",
            )
        ]
    if rob < 0:
        return [
            CheckOutcome(
                "fail", f"R06 — ROB négatif : {rob} t.", {"rob_t": str(rob)}, severity="bloquant"
            )
        ]
    outs: list[CheckOutcome] = []
    if rob == 0:
        outs.append(
            CheckOutcome(
                "fail", "R06 — ROB déclaré nul à l'escale.", {"rob_t": "0"}, severity="warning"
            )
        )
    borne = await _thr(ctx, "borne_max_rob_t")
    if rob > borne:
        outs.append(
            CheckOutcome(
                "fail",
                f"R06 — ROB {rob} t > borne plausible {borne} t.",
                {"rob_t": str(rob), "borne": str(borne)},
                severity="warning",
            )
        )
    return outs or _ok("R06 — ROB de référence plausible.")


@rule("R07")
async def _r07_ports(ctx: RuleContext) -> list[CheckOutcome]:
    """R07 — LOCODE des ports du voyage présents et conformes (5 caractères).
    Warning. Matrice §1. Évalué une fois par séquence (1er sujet)."""
    if ctx.leg is None or ctx.index != 0:
        return []
    leg = ctx.leg
    bad: list[str] = []
    for pid_attr, label in (("departure_port_id", "départ"), ("arrival_port_id", "arrivée")):
        pid = _get(leg, pid_attr)
        if pid is None:
            bad.append(f"port {label} absent")
            continue
        port = await ctx.db.get(Port, pid)
        locode = (_get(port, "locode") if port is not None else None) or ""
        if len(str(locode).strip()) != _LOCODE_LEN:
            bad.append(f"LOCODE {label} non conforme ({locode!r})")
    if bad:
        return [
            CheckOutcome("fail", "R07 — " + " ; ".join(bad), {"issues": bad}, severity="warning")
        ]
    return _ok("R07 — LOCODE des ports conformes.")


@rule("R08")
async def _r08_consumption(ctx: RuleContext) -> list[CheckOutcome]:
    """R08 — Consommation : négative → bloquant ; nulle en Noon → warning ;
    hors seuil cible (``seuil_conso_ref_l_j``) → warning ; complétude escale
    (amendement : conso escale absente > seuil jours ⇒ estimation défaut
    ``conso_estimee_defaut_t_j`` = 0,21 t/j TRACÉE) ; compteurs moteur
    manquants à Departure/Arrival/Anchoring/Cut-off → bloquant (G2, cf.
    ``_r08_missing_engine_readings`` — sans ça, l'intervalle produirait une
    conso silencieusement vide, jamais détectée par les volets ci-dessous).
    Matrice §1 + §5."""
    et0 = _event_type(ctx.subject)
    if et0 in _PORTCALL_TYPES + _ANCHORING_TYPES + ("cutoff",):
        missing = await _r08_missing_engine_readings(ctx)
        if missing:
            return missing
    prev = ctx.prev
    if prev is None:
        return []
    delta_l, _regressed = _fuel_delta_l(prev, ctx.subject)
    if delta_l is None:
        return []
    outs: list[CheckOutcome] = []
    if delta_l < 0:
        outs.append(
            CheckOutcome(
                "fail",
                f"R08 — consommation négative ({delta_l} L).",
                {"delta_l": str(delta_l)},
                severity="bloquant",
            )
        )
        return outs
    et = et0
    dur_h = _duration_h(prev, ctx.subject)
    # Complétude conso escale : Arrival → Departure (adjacents dans la séquence).
    if et == "departure" and _event_type(prev) == "arrival" and dur_h is not None and dur_h > 0:
        days = dur_h / _HOURS_PER_DAY
        seuil_j = await _thr(ctx, "duree_escale_alerte_conso_manquante_j")
        if days > seuil_j and delta_l == 0:
            defaut = await _thr(ctx, "conso_estimee_defaut_t_j")
            estimee = defaut * days
            outs.append(
                CheckOutcome(
                    "fail",
                    f"R08 — conso d'escale absente sur {days:.1f} j (> {seuil_j} j) : "
                    f"estimation par défaut {estimee:.3f} t tracée.",
                    {
                        "escale_jours": str(days),
                        "conso_estimee_defaut_t_j": str(defaut),
                        "conso_estimee_t": str(estimee),
                        "traced": True,
                    },
                    severity="warning",
                )
            )
            return outs
    if delta_l == 0 and et == "noon":
        outs.append(
            CheckOutcome(
                "fail",
                "R08 — consommation nulle sur un Noon (en mer).",
                {"delta_l": "0"},
                severity="warning",
            )
        )
    if dur_h is not None and dur_h > 0:
        per_day = delta_l / dur_h * _HOURS_PER_DAY
        seuil = await _thr(ctx, "seuil_conso_ref_l_j")
        if per_day > seuil:
            outs.append(
                CheckOutcome(
                    "fail",
                    f"R08 — consommation {per_day:.0f} L/j > seuil cible {seuil} L/j.",
                    {"conso_l_j": str(per_day), "seuil_l_j": str(seuil)},
                    severity="warning",
                )
            )
    return outs or _ok("R08 — consommation plausible.")


@rule("R09")
async def _r09_distance_datetime(ctx: RuleContext) -> list[CheckOutcome]:
    """R09 — v1 : distance déclarée vs calculée (Thalos), tolérance
    ``tolerance_distance_manuelle_nm`` ; v2 : datetime d'escale vs référence
    SOF/planning (``tolerance_datetime_escale_h``). Warning. Matrice §3 (v1) + §5 (v2).

    Distance déclarée : attribut direct ``distance_nm`` (sujets synthétiques /
    legacy) OU delta du cumul ``distance_from_sosp_nm`` (NoonEvent réel —
    même dérivation que R21 pour la durée)."""
    outs: list[CheckOutcome] = []
    # v1 — distance déclarée vs calculée haversine depuis le point précédent.
    declared = _as_decimal(_get(ctx.subject, "distance_nm"))
    if declared is None and ctx.prev is not None:
        cur_s = _as_decimal(_get(ctx.subject, "distance_from_sosp_nm"))
        prev_s = _as_decimal(_get(ctx.prev, "distance_from_sosp_nm"))
        if cur_s is not None and prev_s is not None:
            declared = cur_s - prev_s
    if declared is not None and ctx.prev is not None:
        calc = _distance_nm(ctx.prev, ctx.subject)
        if calc is not None:
            tol = await _thr(ctx, "tolerance_distance_manuelle_nm")
            if abs(declared - calc) > tol:
                outs.append(
                    CheckOutcome(
                        "fail",
                        f"R09 — distance déclarée {declared} nm vs calculée {calc:.1f} nm (> {tol} nm).",
                        {
                            "declared_nm": str(declared),
                            "calculated_nm": str(calc),
                            "tolerance_nm": str(tol),
                        },
                        severity="warning",
                    )
                )
    # v2 — datetime d'escale vs référence du leg (ATD/ETD, ATA/ETA).
    et = _event_type(ctx.subject)
    if et in _PORTCALL_TYPES and ctx.leg is not None:
        dt = _dt(ctx.subject)
        if et == "departure":
            ref = _norm_dt(_get(ctx.leg, "atd") or _get(ctx.leg, "etd"))
        else:
            ref = _norm_dt(_get(ctx.leg, "ata") or _get(ctx.leg, "eta"))
        if dt is not None and ref is not None:
            tol_h = await _thr(ctx, "tolerance_datetime_escale_h")
            gap_h = abs((dt - ref).total_seconds()) / 3600
            if Decimal(str(gap_h)) > tol_h:
                outs.append(
                    CheckOutcome(
                        "fail",
                        f"R09 — datetime d'escale à {gap_h:.1f} h de la référence AIS/SOF (> {tol_h} h).",
                        {"gap_h": str(gap_h), "tolerance_h": str(tol_h)},
                        severity="warning",
                    )
                )
    return outs or _ok("R09 — cohérence distance/horodatage conforme.")


@rule("R28")
async def _r28_haversine_vs_logged_distance(ctx: RuleContext) -> list[CheckOutcome]:
    """R28 — cohérence distance haversine calculée vs distance loguée par le
    bord (revue technique 09/07, Matrice §8) : le calcul haversine sur les
    positions Event sous-estime systématiquement la distance parcourue dès
    que le navire louvoie/dévie pour raison météo (mode d'exploitation
    normal d'une flotte vélique) — la distance alimentant directement le
    Transport Work (dénominateur EF_MRV), cet écart doit rester **visible**
    même s'il n'est jamais corrigé automatiquement (la distance haversine
    reste la valeur utilisée pour Transport Work/EF_MRV). Warning.

    Distance loguée : delta de ``NoonEvent.distance_from_sosp_nm`` (cumul
    déclaratif par le bord, indépendant du calcul positionnel) entre deux
    Noon consécutifs. Recouvrement connu et assumé avec le repli v1 de R09
    (même dérivation, seuil ``tolerance_distance_manuelle_nm`` différent) —
    à résorber par G16 (recentrage de R09 sur les positions manuelles
    ``Manuel_justifie`` uniquement, Matrice §3)."""
    if _event_type(ctx.subject) != "noon" or ctx.prev is None:
        return _ok("R28 — non applicable (pas un Noon, ou premier relevé de la séquence).")
    cur_s = _as_decimal(_get(ctx.subject, "distance_from_sosp_nm"))
    prev_s = _as_decimal(_get(ctx.prev, "distance_from_sosp_nm"))
    if cur_s is None or prev_s is None:
        return _ok("R28 — distance loguée (SOSP) indisponible.")
    logged = cur_s - prev_s
    calc = _distance_nm(ctx.prev, ctx.subject)
    if calc is None:
        return _ok("R28 — position(s) indisponible(s) pour le calcul haversine.")
    tol = await _thr(ctx, "tolerance_distance_haversine_nm")
    ecart = abs(logged - calc)
    if ecart > tol:
        return [
            CheckOutcome(
                "fail",
                f"R28 — distance haversine {calc:.1f} nm vs loguée (SOSP) {logged} nm "
                f"(écart {ecart:.1f} nm > {tol} nm).",
                {
                    "haversine_nm": str(calc),
                    "logged_nm": str(logged),
                    "ecart_nm": str(ecart),
                    "tolerance_nm": str(tol),
                },
                severity="warning",
            )
        ]
    return _ok("R28 — distance haversine cohérente avec la distance loguée.")


@rule("R10")
async def _r10_counter(ctx: RuleContext) -> list[CheckOutcome]:
    """R10 amendé — monotonie des compteurs moteur. Une régression NON confirmée
    → warning routé Administrateur ; escalade en bloquant si non traitée au-delà
    de ``delai_confirmation_reset_j``. Un reset CONFIRMÉ (``reset_confirmed_by``)
    passe (nouvelle base de référence). Matrice §3."""
    prev = ctx.prev
    if prev is None:
        return []
    pm, cm = _engine_fuel_map(prev), _engine_fuel_map(ctx.subject)
    if not cm:
        return []
    regressed: list[Any] = []
    reading_ids: list[int] = []
    for eid, (cf, _flag, confirmed, rid) in cm.items():
        pf = pm.get(eid, (None, False, False, None))[0]
        if pf is None or cf is None:
            continue
        if cf < pf and not confirmed:
            regressed.append(eid)
            if rid is not None:
                reading_ids.append(rid)
    if not regressed:
        return _ok("R10 — compteurs monotones (ou reset confirmé).")
    # Escalade : le relevé est-il ancien et non traité ?
    delai_j = await _thr(ctx, "delai_confirmation_reset_j")
    cur_dt = _dt(ctx.subject)
    now = _norm_dt(ctx.now) or datetime.now(UTC).replace(tzinfo=None)
    stale = cur_dt is not None and (now - cur_dt) > timedelta(days=float(delai_j))
    severity = "bloquant" if stale else "warning"
    return [
        CheckOutcome(
            "fail",
            f"R10 — régression de compteur non confirmée (moteurs {regressed})"
            + (
                " — escalade bloquante (délai dépassé)."
                if stale
                else " — à confirmer par l'Administrateur."
            ),
            {
                "engines": [str(e) for e in regressed],
                "reading_ids": reading_ids,
                "route_roles": ["administrateur"],
                "escalated": stale,
                "delai_confirmation_reset_j": str(delai_j),
            },
            severity=severity,
        )
    ]


@rule("R21")
async def _r21_report_duration(ctx: RuleContext) -> list[CheckOutcome]:
    """R21 — Durée déclarée depuis le dernier rapport cohérente avec l'écart réel
    entre horodatages (``tolerance_duree_rapport_h``). Warning. Matrice §5.

    Durée déclarée = ``time_from_last_report_h`` si fourni, sinon dérivée du
    delta de ``time_from_sosp_h`` (cumulé depuis SOSP)."""
    prev = ctx.prev
    if prev is None:
        return []
    declared = _as_decimal(_get(ctx.subject, "time_from_last_report_h"))
    if declared is None:
        cur_s = _as_decimal(_get(ctx.subject, "time_from_sosp_h"))
        prev_s = _as_decimal(_get(prev, "time_from_sosp_h"))
        if cur_s is not None and prev_s is not None:
            declared = cur_s - prev_s
    if declared is None:
        return []
    real = _duration_h(prev, ctx.subject)
    if real is None:
        return []
    tol = await _thr(ctx, "tolerance_duree_rapport_h")
    if abs(declared - real) > tol:
        return [
            CheckOutcome(
                "fail",
                f"R21 — durée déclarée {declared} h vs réelle {real:.1f} h (> {tol} h).",
                {"declared_h": str(declared), "real_h": str(real), "tolerance_h": str(tol)},
                severity="warning",
            )
        ]
    return _ok("R21 — durée entre rapports cohérente.")


# ─────────────────────────────── Inter-rapports (séquences) ───────────────────


@rule("IR01")
async def _ir01_duplicate(ctx: RuleContext) -> list[CheckOutcome]:
    """IR01 — Doublon de date/jour pour un même type d'événement (bloquant).
    Complète la contrainte d'unicité (datetime UTC exact) par la détection d'un
    doublon au niveau du JOUR. Cas réel : deux Noon à la même date."""
    et = _event_type(ctx.subject)
    dt = _dt(ctx.subject)
    if not _present(et) or dt is None:
        return []
    day = dt.date()
    for j in range(ctx.index):
        other = ctx.subjects[j]
        odt = _dt(other)
        if _event_type(other) == et and odt is not None and odt.date() == day:
            return [
                CheckOutcome(
                    "fail",
                    f"IR01 — doublon de date+type ({et} au {day.isoformat()}).",
                    {"event_type": et, "date": day.isoformat()},
                    severity="bloquant",
                )
            ]
    return _ok("IR01 — pas de doublon date+type.")


async def _interval_conso_t(ctx: RuleContext, prev: Any, cur: Any) -> Decimal | None:
    """Consommation (t) entre deux sujets consécutifs — 3 sources, dans l'ordre :

    1. attribut déclaré ``conso_t``/``conso_between_t`` du sujet courant
       (sujets synthétiques, lignes de synthèse) ;
    2. delta des compteurs carburant (litres) × 0,001 × densité paramétrée
       (événements réels porteurs d'``engine_readings`` — formule CFOTE_05) ;
    3. ``None`` = information indisponible (la règle appelante décide :
       IR02 s'abstient — la continuité fine est portée par R14 sur la chaîne
       calculée ; IR03 traite « inconnu » comme suspect)."""
    v = _as_decimal(_first(cur, ("conso_t", "conso_between_t")))
    if v is not None:
        return v
    delta_l, _regressed = _fuel_delta_l(prev, cur)
    if delta_l is None or delta_l < 0:
        return None
    density = await _thr(ctx, "densite_defaut_t_m3")
    return delta_l * Decimal("0.001") * density


@rule("IR02")
async def _ir02_rob_continuity(ctx: RuleContext) -> list[CheckOutcome]:
    """IR02 — ROB(J) ≈ ROB(J-1) − conso ± soutage, tolérance = bornes R14
    (mineur → warning, critique → bloquant). Matrice §4 (§5 IR02 notebook :
    >5 t bloquant / >0,5 t warning ≡ bornes R14 critique/mineur).

    Conso indisponible (``_interval_conso_t`` → None) ⇒ abstention : la
    continuité fine est alors portée par R14 (chaîne ``inter_event_compute``)."""
    prev = ctx.prev
    if prev is None:
        return []
    cur_rob, prev_rob = _rob(ctx.subject), _rob(prev)
    if cur_rob is None or prev_rob is None:
        return []
    conso = await _interval_conso_t(ctx, prev, ctx.subject)
    if conso is None:
        return []
    expected = prev_rob - conso + _bunker_t(ctx.subject)
    ecart = abs(cur_rob - expected)
    mineur = await _thr(ctx, "seuil_rob_ecart_mineur_t")
    critique = await _thr(ctx, "seuil_rob_ecart_critique_t")
    details = {
        "rob_declared_t": str(cur_rob),
        "rob_expected_t": str(expected),
        "ecart_t": str(ecart),
        "seuil_mineur_t": str(mineur),
        "seuil_critique_t": str(critique),
    }
    if ecart > critique:
        return [
            CheckOutcome(
                "fail",
                f"IR02 — écart ROB {ecart} t > critique {critique} t.",
                details,
                severity="bloquant",
            )
        ]
    if ecart > mineur:
        return [
            CheckOutcome(
                "fail",
                f"IR02 — écart ROB {ecart} t > mineur {mineur} t.",
                details,
                severity="warning",
            )
        ]
    return _ok("IR02 — continuité ROB cohérente.")


@rule("IR03")
async def _ir03_rob_frozen(ctx: RuleContext) -> list[CheckOutcome]:
    """IR03 — ROB strictement figé sur ≥ ``ir03_min_reports_figes`` relevés
    consécutifs malgré une consommation (> ``ir03_conso_min_t``). Warning.
    Cas réel du dossier : ROB figé à 72,3 t 4 jours puis saut brutal −7,6 t.

    Conso du palier : connue (attributs ou compteurs) et ≤ seuil ⇒ figé
    LÉGITIME (rien ne se consomme) ; connue et > seuil ⇒ anomalie ; totalement
    inconnue ⇒ anomalie aussi (un ROB strictement figé N relevés sans aucune
    donnée de conso est le symptôme réel 2025 — macro de consolidation figée)."""
    cur_rob = _rob(ctx.subject)
    if cur_rob is None:
        return []
    n_min = int(await _thr(ctx, "ir03_min_reports_figes"))
    conso_min = await _thr(ctx, "ir03_conso_min_t")
    run = 1
    known: list[Decimal] = []
    j = ctx.index - 1
    newer = ctx.subject
    while j >= 0:
        other = ctx.subjects[j]
        orob = _rob(other)
        if orob is None or orob != cur_rob:
            break
        run += 1
        c = await _interval_conso_t(ctx, other, newer)
        if c is not None:
            known.append(c)
        newer = other
        j -= 1
    span_conso = sum(known, Decimal("0")) if known else None
    # Flag une seule fois, au moment où le palier atteint exactement le seuil.
    if run == n_min and (span_conso is None or span_conso > conso_min):
        return [
            CheckOutcome(
                "fail",
                f"IR03 — ROB figé à {cur_rob} t sur {run} relevés consécutifs "
                f"(conso cumulée {span_conso if span_conso is not None else 'inconnue'}).",
                {
                    "rob_t": str(cur_rob),
                    "reports": run,
                    "span_conso_t": (str(span_conso) if span_conso is not None else None),
                    "seuil_reports": n_min,
                    "ir03_conso_min_t": str(conso_min),
                },
                severity="warning",
            )
        ]
    return _ok("IR03 — ROB non figé.")


@rule("IR04")
async def _ir04_counter_regress(ctx: RuleContext) -> list[CheckOutcome]:
    """IR04 — Compteur carburant régressant d'un relevé à l'autre SANS reset
    documenté (``is_counter_reset``) → bloquant. Distinct de R10 : IR04 accepte
    un reset simplement DOCUMENTÉ ; R10 exige la CONFIRMATION Administrateur."""
    prev = ctx.prev
    if prev is None:
        return []
    pm, cm = _engine_fuel_map(prev), _engine_fuel_map(ctx.subject)
    if not cm:
        return []
    for eid, (cf, is_reset, _confirmed, rid) in cm.items():
        pf = pm.get(eid, (None, False, False, None))[0]
        if pf is None or cf is None:
            continue
        if cf < pf and not is_reset:
            return [
                CheckOutcome(
                    "fail",
                    f"IR04 — compteur carburant régressant sans reset documenté "
                    f"(moteur {eid} : {pf} → {cf} L).",
                    {
                        "engine_id": str(eid),
                        "prev_l": str(pf),
                        "cur_l": str(cf),
                        "reading_ids": ([rid] if rid is not None else []),
                    },
                    severity="bloquant",
                )
            ]
    return _ok("IR04 — compteurs non régressants (ou reset documenté).")


@rule("IR05")
async def _ir05_position_frozen(ctx: RuleContext) -> list[CheckOutcome]:
    """IR05 — Position strictement figée sur ≥ ``ir05_min_reports_figes`` relevés
    consécutifs en mer (Noon). Warning. Cas réel : position figée en mer."""
    et = _event_type(ctx.subject)
    if _present(et) and et != "noon":
        return []  # « en mer » = Noon (les escales/mouillages figent normalement)
    cur = _latlon(ctx.subject)
    if cur is None:
        return []
    n_min = int(await _thr(ctx, "ir05_min_reports_figes"))
    run = 1
    j = ctx.index - 1
    while j >= 0:
        other = ctx.subjects[j]
        oet = _event_type(other)
        if _present(oet) and oet != "noon":
            break
        oll = _latlon(other)
        if oll is None or oll != cur:
            break
        run += 1
        j -= 1
    if run == n_min:
        return [
            CheckOutcome(
                "fail",
                f"IR05 — position figée ({cur[0]}, {cur[1]}) sur {run} relevés consécutifs en mer.",
                {"lat": str(cur[0]), "lon": str(cur[1]), "reports": run, "seuil_reports": n_min},
                severity="warning",
            )
        ]
    return _ok("IR05 — position non figée.")


# ════════════════════════════════════════════════════════════ Scope VOYAGE
#
# Sujet attendu = le ``Leg`` ; ``ctx.leg`` DOIT être renseigné (les triggers le
# passent). Sans ``ctx.leg`` la règle s'abstient → un run "voyage" sur un sujet
# quelconque (ex. test socle) ne produit rien (compat lot 2).


async def _leg_bunker_lookup(db: AsyncSession, leg_id: int):
    """Ferme ``(from, to] → tonnes soutées`` sur les soutages validés Master."""
    rows = (
        await db.execute(
            select(BunkerOperation.delivery_datetime_utc, BunkerOperation.mass_t)
            .where(BunkerOperation.leg_id == leg_id)
            .where(BunkerOperation.status == "valide_master")
        )
    ).all()

    def _naive(dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo is not None else dt

    ops = [(_naive(dt), m) for dt, m in rows if dt is not None and m is not None]

    def lookup(frm: datetime, to: datetime) -> Decimal:
        f, t = _naive(frm), _naive(to)
        return sum((m for (dt, m) in ops if dt is not None and f < dt <= t), Decimal("0"))

    return lookup


async def _compute_leg(ctx: RuleContext):
    """Calcule (et met en cache sur le leg) la chaîne dérivée du voyage."""
    from app.services import inter_event_compute as iec

    leg = ctx.leg
    cached = getattr(leg, "_lot8_computation", None)
    if cached is not None:
        return cached
    lookup = await _leg_bunker_lookup(ctx.db, leg.id)
    comp = await iec.compute_leg(ctx.db, leg, bunkered_t_lookup=lookup)
    with contextlib.suppress(Exception):
        # best-effort cache (instance ORM) — évite 5× compute par leg/run
        leg._lot8_computation = comp
    return comp


def _classify_rob(
    ecart: Decimal, mineur: Decimal, majeur: Decimal, critique: Decimal
) -> tuple[str, str]:
    """(classe, sévérité) d'un écart ROB selon les 3 bornes R14."""
    if ecart <= mineur:
        return "conforme", "info"
    if ecart <= majeur:
        return "mineur", "warning"
    if ecart <= critique:
        return "majeur", "warning"
    return "critique", "bloquant"


@rule("R14")
async def _r14_rob_continuity(ctx: RuleContext) -> list[CheckOutcome]:
    """R14a/b — Continuité du ROB : cross-check ``rob_declared_t`` vs
    ``rob_calculated_t`` (chaîne ancrée sur Departure/Arrival UNIQUEMENT, jamais
    Noon — hiérarchie v2). Écart classé mineur/majeur/critique ; bloquant si
    critique. Matrice §3 (R14a/b + hiérarchie v2)."""
    if ctx.leg is None:
        return []
    comp = await _compute_leg(ctx)
    mineur = await _thr(ctx, "seuil_rob_ecart_mineur_t")
    majeur = await _thr(ctx, "seuil_rob_ecart_majeur_t")
    critique = await _thr(ctx, "seuil_rob_ecart_critique_t")
    outs: list[CheckOutcome] = []
    anchored = False
    for point in comp.rob_chain:
        if point.rob_declared_t is None:
            continue
        if not anchored:
            anchored = True  # 1er PortCall = ancrage (calculé == déclaré)
            continue
        if point.rob_calculated_t is None:
            continue
        ecart = abs(Decimal(point.rob_declared_t) - Decimal(point.rob_calculated_t))
        klass, sev = _classify_rob(ecart, mineur, majeur, critique)
        if klass == "conforme":
            continue
        outs.append(
            CheckOutcome(
                "fail",
                f"R14 — écart ROB {klass} ({ecart} t) à l'événement {point.event_type} "
                f"#{point.event_id} : déclaré {point.rob_declared_t} vs calculé {point.rob_calculated_t}.",
                {
                    "event_id": point.event_id,
                    "classification": klass,
                    "ecart_t": str(ecart),
                    "rob_declared_t": str(point.rob_declared_t),
                    "rob_calculated_t": str(point.rob_calculated_t),
                    "seuils": {
                        "mineur": str(mineur),
                        "majeur": str(majeur),
                        "critique": str(critique),
                    },
                },
                subject=ctx.leg,
                severity=sev,
            )
        )
    return outs or _ok("R14 — continuité ROB conforme.")


@rule("R15")
async def _r15_consumption_reference(ctx: RuleContext) -> list[CheckOutcome]:
    """R15 — Écart conso voyage vs référence : conso/jour vs cible
    ``seuil_conso_ref_l_j`` et, si présente, vs ``FlgoVoyageConsumptionRef``
    (Marad). Warning. Matrice §2."""
    if ctx.leg is None:
        return []
    comp = await _compute_leg(ctx)
    outs: list[CheckOutcome] = []
    totals = comp.totals
    if (
        totals is not None
        and totals.conso_total_t is not None
        and totals.duration_h
        and totals.duration_h > 0
    ):
        density = await _thr(ctx, "densite_defaut_t_m3") or Decimal("0.845")
        total_l = totals.conso_total_t * Decimal("1000") / density
        per_day_l = total_l / (totals.duration_h / _HOURS_PER_DAY)
        seuil = await _thr(ctx, "seuil_conso_ref_l_j")
        if per_day_l > seuil:
            outs.append(
                CheckOutcome(
                    "fail",
                    f"R15 — conso voyage {per_day_l:.0f} L/j > cible {seuil} L/j.",
                    {"conso_l_j": str(per_day_l), "seuil_l_j": str(seuil)},
                    subject=ctx.leg,
                    severity="warning",
                )
            )
    # Contrôle croisé référence FLGO (CheckConsumption).
    from app.models.flgo import FlgoVoyageConsumptionRef

    ref = (
        (
            await ctx.db.execute(
                select(FlgoVoyageConsumptionRef).where(
                    FlgoVoyageConsumptionRef.leg_id == ctx.leg.id
                )
            )
        )
        .scalars()
        .first()
    )
    if ref is not None and totals is not None:
        me = totals.conso_me_t or Decimal("0")
        ae = totals.conso_ae_t or Decimal("0")
        ref_total = (ref.me_consumption_t or Decimal("0")) + (ref.ae_consumption_t or Decimal("0"))
        ecart = abs((me + ae) - ref_total)
        seuil = await _thr(ctx, "seuil_rob_ecart_majeur_t")
        if ecart > seuil:
            outs.append(
                CheckOutcome(
                    "fail",
                    f"R15 — conso calculée {me + ae} t vs référence FLGO {ref_total} t (écart {ecart} t).",
                    {
                        "conso_calc_t": str(me + ae),
                        "conso_flgo_t": str(ref_total),
                        "ecart_t": str(ecart),
                    },
                    subject=ctx.leg,
                    severity="warning",
                )
            )
    return outs or _ok("R15 — conso voyage cohérente avec la référence.")


@rule("R17")
async def _r17_rob_vs_flgo(ctx: RuleContext) -> list[CheckOutcome]:
    """R17 — Rapprochement ROB MyTOWT vs FLGO (Marad) au Departure/Arrival,
    jointure par date la plus proche ; déclassé Info si l'écart temporel dépasse
    ``tolerance_flgo_ecart_temps_h``. Matrice §3."""
    if ctx.leg is None or ctx.leg.vessel_id is None:
        return []
    from app.services import flgo_sync

    comp = await _compute_leg(ctx)
    vessel = await ctx.db.get(Vessel, ctx.leg.vessel_id)
    if vessel is None:
        return []
    density = await _thr(ctx, "densite_defaut_t_m3") or Decimal("0.845")
    mineur = await _thr(ctx, "seuil_rob_ecart_mineur_t")
    outs: list[CheckOutcome] = []
    for point in comp.rob_chain:
        if point.rob_declared_t is None or point.datetime_utc is None:
            continue
        match = await flgo_sync.flgo_nearest_reading(ctx.db, vessel, point.datetime_utc)
        if match.reading is None or match.reading.total_rob_m3 is None:
            continue
        flgo_rob_t = Decimal(match.reading.total_rob_m3) * density
        ecart = abs(Decimal(point.rob_declared_t) - flgo_rob_t)
        if ecart <= mineur:
            continue
        # Au-delà de la tolérance temporelle → rapprochement peu significatif → Info.
        sev = "warning" if match.within_tolerance else "info"
        outs.append(
            CheckOutcome(
                "fail",
                f"R17 — ROB déclaré {point.rob_declared_t} t vs FLGO {flgo_rob_t:.2f} t "
                f"(écart {ecart:.2f} t, Δt {match.delta_hours} h)"
                + (
                    ""
                    if match.within_tolerance
                    else " — déclassé Info (lecture FLGO trop éloignée)."
                ),
                {
                    "event_id": point.event_id,
                    "rob_declared_t": str(point.rob_declared_t),
                    "rob_flgo_t": str(flgo_rob_t),
                    "ecart_t": str(ecart),
                    "delta_h": str(match.delta_hours),
                    "within_tolerance": match.within_tolerance,
                },
                subject=ctx.leg,
                severity=sev,
            )
        )
    return outs or _ok("R17 — ROB cohérent avec FLGO.")


@rule("R20")
async def _r20_cargo_mrv(ctx: RuleContext) -> list[CheckOutcome]:
    """R20 — Cohérence Cargo MRV (DWT carried) ≥ cargaison déclarée (B/L) pour un
    voyage chargé. **Sévérité Info** tant que D10 (rattachement commercial) n'est
    pas câblé au certificat (lot 9) — arbitrage acté. Matrice §3."""
    if ctx.leg is None:
        return []
    from app.models.nav_event import DepartureEvent

    comp = await _compute_leg(ctx)
    dep = next((e for e in comp.events if isinstance(e, DepartureEvent)), None)
    if dep is None:
        return []
    cargo_bl = _as_decimal(_get(dep, "cargo_bl_t"))
    if cargo_bl is None or _get(dep, "vessel_condition") != "laden":
        return []
    cargo = comp.cargo_mrv.get(dep.id)
    cargo_mrv = cargo.cargo_mrv_t if cargo is not None else None
    if cargo_mrv is None:
        return []
    seuil = await _thr(ctx, "seuil_cargo_mrv_ecart_t")
    if Decimal(cargo_mrv) + seuil < cargo_bl:
        return [
            CheckOutcome(
                "fail",
                f"R20 — Cargo MRV {cargo_mrv} t < cargaison B/L {cargo_bl} t (Info, D10 non résolu).",
                {
                    "cargo_mrv_t": str(cargo_mrv),
                    "cargo_bl_t": str(cargo_bl),
                    "seuil_t": str(seuil),
                    "d10_pending": True,
                },
                subject=ctx.leg,
                severity="info",
            )
        ]
    return _ok("R20 — Cargo MRV ≥ B/L (Info).")


@rule("R26")
async def _r26_voyage_chaining(ctx: RuleContext) -> list[CheckOutcome]:
    """R26 — Chaînage : port d'arrivée du voyage N = port de départ du voyage N+1
    du même navire (sauf repositionnement codifié). Warning. Matrice §5."""
    if ctx.leg is None or ctx.leg.vessel_id is None:
        return []
    leg = ctx.leg
    nxt = (
        (
            await ctx.db.execute(
                select(Leg)
                .where(Leg.vessel_id == leg.vessel_id, Leg.etd > leg.etd, Leg.status != "cancelled")
                .order_by(Leg.etd.asc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if nxt is None:
        return _ok("R26 — pas de voyage suivant (chaînage non applicable).")
    arr = await ctx.db.get(Port, leg.arrival_port_id)
    dep = await ctx.db.get(Port, nxt.departure_port_id)
    arr_code = (_get(arr, "locode") if arr else None) or ""
    dep_code = (_get(dep, "locode") if dep else None) or ""
    if arr_code and dep_code and arr_code != dep_code:
        return [
            CheckOutcome(
                "fail",
                f"R26 — rupture de chaînage : arrivée {arr_code} ({leg.leg_code}) ≠ "
                f"départ {dep_code} ({nxt.leg_code}).",
                {
                    "arr_locode": arr_code,
                    "dep_locode": dep_code,
                    "leg": leg.leg_code,
                    "next_leg": nxt.leg_code,
                },
                subject=ctx.leg,
                severity="warning",
            )
        ]
    return _ok("R26 — chaînage des voyages conforme.")


@rule("R27")
async def _r27_year_end_cutoff(ctx: RuleContext) -> list[CheckOutcome]:
    """R27 — Coupure d'exercice MRV (Year-End Cut-off, G1/CDC v0.7 §9.2/§10.1/
    §14.1) : un voyage en cours à la bascule d'année civile (31/12 24:00 UTC
    ⇔ 01/01 00:00 UTC) doit porter un événement ``cutoff`` finalisé
    exactement à cet instant. Warning avant ``tolerance_cutoff_h``, bloquant
    au-delà — bloque la consolidation MRV (génération des Carbon Reports
    scindés, commit 5/6) tant que non résolu.

    Hypothèse simplificatrice actée avec le porteur du projet : au plus une
    bascule d'année par voyage (rotations de la flotte 3-5 semaines, jamais
    deux 31/12 sur le même voyage en pratique)."""
    if ctx.leg is None:
        return []
    leg = ctx.leg
    start = _norm_dt(_get(leg, "atd"))
    if start is None:
        return []  # voyage pas encore parti — rien à borner
    now = _norm_dt(ctx.now) or datetime.now(UTC).replace(tzinfo=None)
    end = _norm_dt(_get(leg, "ata")) or now
    boundary = datetime(start.year + 1, 1, 1)
    if not (start <= boundary <= end):
        return _ok("R27 — voyage sans bascule d'année civile.")
    if boundary > now:
        return _ok("R27 — bascule d'année à venir, hors fenêtre de contrôle.")

    from app.services import inter_event_compute as iec

    events = await iec.finalized_events_for_leg(ctx.db, leg.id)
    has_cutoff = any(_event_type(e) == "cutoff" and _dt(e) == boundary for e in events)
    if has_cutoff:
        return _ok("R27 — événement Cut-off finalisé à la bascule d'année.")

    tol_h = await _thr(ctx, "tolerance_cutoff_h")
    late_h = Decimal(str((now - boundary).total_seconds() / 3600))
    stale = late_h > tol_h
    return [
        CheckOutcome(
            "fail",
            f"R27 — voyage {leg.leg_code} en cours à la bascule d'année civile "
            f"({boundary.date()}) sans événement Cut-off finalisé"
            + (
                " — bloque la consolidation MRV (délai de tolérance dépassé)."
                if stale
                else f" — tolérance {tol_h} h avant escalade."
            ),
            {
                "boundary": boundary.isoformat(),
                "late_h": str(late_h),
                "tolerance_h": str(tol_h),
            },
            subject=leg,
            severity="bloquant" if stale else "warning",
        )
    ]


# ════════════════════════════════════════════════════════════ Scope BUNKER


@rule("R16")
async def _r16_density(ctx: RuleContext) -> list[CheckOutcome]:
    """R16 — Densité BDN dans [défaut − tol, défaut + tol]. La logique de service
    (``bunkering.check_density``) RESTE ; la règle l'appelle et persiste. Warning.
    Matrice §2."""
    if not _has(ctx.subject, "bdn_number"):
        return []
    from app.services import bunkering

    check = await bunkering.check_density(ctx.db, ctx.subject)
    if check.flagged:
        return [
            CheckOutcome(
                "fail",
                f"R16 — densité BDN {check.density_t_m3} hors plage [{check.low}, {check.high}] t/m³.",
                {
                    "density_t_m3": (
                        str(check.density_t_m3) if check.density_t_m3 is not None else None
                    ),
                    "low": str(check.low),
                    "high": str(check.high),
                    "defaut": str(check.default_t_m3),
                    "tolerance": str(check.tolerance_t_m3),
                },
                severity="warning",
            )
        ]
    return _ok("R16 — densité BDN dans la plage.")


@rule("R23")
async def _r23_bunker_consistency(ctx: RuleContext) -> list[CheckOutcome]:
    """R23 — Σ(volume × densité) des cuves vs masse déclarée (tolérance
    ``tolerance_bdn_flgo_t``, warning) ; volet capacités physiques en **Info**
    (Q11 : capacités officielles indisponibles). La logique de service
    (``bunkering.check_mass_consistency`` / ``check_capacity``) RESTE. Matrice §5.

    Écart vs Matrice : le volet capacités est spécifié Bloquant dans la Matrice
    ; dégradé en Info tant que ``vessel_tanks.capacity_m3`` n'est pas officiel
    (décision Q11 — bascule Bloquant dès réception des plans de capacité)."""
    if not _has(ctx.subject, "bdn_number"):
        return []
    from app.services import bunkering

    bunker = ctx.subject
    allocations = list(
        (
            await ctx.db.execute(
                select(BunkerTankAllocation).where(BunkerTankAllocation.bunker_id == bunker.id)
            )
        )
        .scalars()
        .all()
    )
    outs: list[CheckOutcome] = []
    mass = await bunkering.check_mass_consistency(ctx.db, bunker, allocations)
    if mass.status != "ok":
        outs.append(
            CheckOutcome(
                "fail",
                f"R23 — masse déclarée {mass.declared_mass_t} t vs Σ(vol×dens) "
                f"{mass.allocated_mass_t} t (écart {mass.delta_t} t, {mass.status}).",
                {
                    "declared_mass_t": str(mass.declared_mass_t),
                    "allocated_mass_t": str(mass.allocated_mass_t),
                    "delta_t": str(mass.delta_t),
                    "tolerance_t": str(mass.tolerance_t),
                    "status": mass.status,
                },
                severity="warning",
            )
        )
    tanks_by_id = await bunkering.vessel_tanks_by_id(ctx.db, bunker.vessel_id)
    cap = bunkering.check_capacity(allocations, tanks_by_id)
    if cap.exceeds:
        outs.append(
            CheckOutcome(
                "fail",
                f"R23 — Σ volumes {cap.total_volume_m3} m³ > Σ capacités {cap.total_capacity_m3} m³ "
                "(Info tant que capacités non officielles, Q11).",
                {
                    "total_volume_m3": str(cap.total_volume_m3),
                    "total_capacity_m3": (
                        str(cap.total_capacity_m3) if cap.total_capacity_m3 is not None else None
                    ),
                    "q11_pending": True,
                },
                severity="info",
            )
        )
    return outs or _ok("R23 — soutage cohérent (masse/volumes).")


@rule("R24")
async def _r24_bunker_flgo(ctx: RuleContext) -> list[CheckOutcome]:
    """R24 — Chaque soutage BDN doit avoir une lecture FLGO « Received »
    correspondante sous ``delai_flgo_bunkering_j``. Sinon warning routé
    Administrateur. Matrice §5 (cas réel : BDN 36039 Artemis non recoupé)."""
    if not _has(ctx.subject, "bdn_number"):
        return []
    from app.services import flgo_sync

    match = await flgo_sync.flgo_matches_for_bunker(ctx.db, ctx.subject)
    if not match.matched:
        return [
            CheckOutcome(
                "fail",
                f"R24 — soutage {ctx.subject.bdn_number} sans lecture FLGO 'Received' "
                f"sous {match.window_days} j — complétude Marad à vérifier.",
                {
                    "bdn_number": ctx.subject.bdn_number,
                    "window_days": str(match.window_days),
                    "route_roles": ["administrateur"],
                },
                severity="warning",
            )
        ]
    return _ok("R24 — soutage recoupé dans FLGO.")


# ════════════════════════════════════════════════════════════ Scope FLGO


@rule("R25")
async def _r25_flgo_consistency(ctx: RuleContext) -> list[CheckOutcome]:
    """R25 — Cohérence FLGO (2 volets, signale JAMAIS ne corrige) :
    (1) Σ compartiments vs volume total (``check_internal_consistency``) —
    évalué UNIQUEMENT si le relevé porte un détail par compartiment (un relevé
    sans détail n'a rien à réconcilier : Σ=0 vs total serait un faux positif) ;
    (2) progression cohérente entre lectures consécutives du même navire
    (``check_consecutive_consistency``). Matrice §5 (réconciliation lot 7)."""
    if not _has(ctx.subject, "total_volume_m3"):
        return []
    from app.models.flgo import FlgoTankCompartmentVolume
    from app.services import flgo_sync

    outs: list[CheckOutcome] = []
    compartments = list(
        (
            await ctx.db.execute(
                select(FlgoTankCompartmentVolume).where(
                    FlgoTankCompartmentVolume.flgo_reading_id == ctx.subject.id
                )
            )
        )
        .scalars()
        .all()
    )
    internal = await flgo_sync.check_internal_consistency(
        ctx.db, ctx.subject, compartments=compartments
    )
    if compartments and internal.flagged:
        outs.append(
            CheckOutcome(
                "fail",
                f"R25 — Σ compartiments {internal.total_compartments_m3} m³ ≠ total déclaré "
                f"{internal.total_declared_m3} m³ (écart {internal.delta_m3} m³).",
                {
                    "volet": "interne",
                    "total_declared_m3": str(internal.total_declared_m3),
                    "total_compartments_m3": str(internal.total_compartments_m3),
                    "delta_m3": str(internal.delta_m3),
                    "tolerance_m3": str(internal.tolerance_m3),
                },
                severity="warning",
            )
        )
    prev = ctx.prev
    if prev is not None and _get(prev, "vessel_id") == _get(ctx.subject, "vessel_id"):
        seq = await flgo_sync.check_consecutive_consistency(ctx.db, prev, ctx.subject)
        if seq.flagged:
            outs.append(
                CheckOutcome(
                    "fail",
                    f"R25 — progression ROB incohérente entre lectures consécutives "
                    f"({seq.reason} : {seq.prev_rob_m3} → {seq.cur_rob_m3} m³).",
                    {
                        "volet": "consecutif",
                        "reason": seq.reason,
                        "prev_rob_m3": (
                            str(seq.prev_rob_m3) if seq.prev_rob_m3 is not None else None
                        ),
                        "cur_rob_m3": (str(seq.cur_rob_m3) if seq.cur_rob_m3 is not None else None),
                        "delta_rob_m3": (
                            str(seq.delta_rob_m3) if seq.delta_rob_m3 is not None else None
                        ),
                        "tolerance_m3": str(seq.tolerance_m3),
                    },
                    severity="warning",
                )
            )
    return outs or _ok("R25 — lecture FLGO cohérente (interne + progression).")


# ════════════════════════════════════════════════════════════ Scope REPORT


@rule("R18")
async def _r18_modification_justified(ctx: RuleContext) -> list[CheckOutcome]:
    """R18 — Toute modification post-finalisation doit être justifiée. Formalise
    en règle persistée la garde du service (``apply_field_modification``).
    Bloquant si une modification est sans justification. Matrice §2."""
    if not _has(ctx.subject, "report_type"):
        return []
    mods = list(
        (
            await ctx.db.execute(
                select(EnvFieldModification).where(EnvFieldModification.report_id == ctx.subject.id)
            )
        )
        .scalars()
        .all()
    )
    unjustified = [m for m in mods if not (m.justification_text and m.justification_text.strip())]
    if unjustified:
        return [
            CheckOutcome(
                "fail",
                f"R18 — {len(unjustified)} modification(s) sans justification.",
                {"count": len(unjustified), "fields": [m.field_name for m in unjustified]},
                severity="bloquant",
            )
        ]
    return _ok("R18 — modifications justifiées.")


@rule("R22")
async def _r22_carbon_vs_noon(ctx: RuleContext) -> list[CheckOutcome]:
    """R22 — Cohérence Carbon vs Noon : total conso du Carbon vs Σ des conso des
    Noon générés du voyage (tolérance ``tolerance_carbon_noon_conso_t``).
    **Le Carbon n'est JAMAIS correcteur** : on SIGNALE, on ne modifie pas
    (arbitrage acté). Warning. Matrice §5."""
    report = ctx.subject
    if not _has(report, "report_type") or _get(report, "report_type") != "carbon":
        return []
    payload = _get(report, "payload") or {}
    carbon_total = _as_decimal((payload.get("totals") or {}).get("conso_total_t"))
    if carbon_total is None:
        return []
    noon_reports = list(
        (
            await ctx.db.execute(
                select(EnvReport).where(
                    EnvReport.leg_id == report.leg_id, EnvReport.report_type == "noon"
                )
            )
        )
        .scalars()
        .all()
    )
    noon_conso: list[Decimal] = []
    for nr in noon_reports:
        iv = (nr.payload or {}).get("interval") or {}
        v = _as_decimal(iv.get("conso_total_t"))
        if v is not None:
            noon_conso.append(v)
    if not noon_conso:
        return []  # rien à recouper
    noon_sum = sum(noon_conso, Decimal("0"))
    tol = await _thr(ctx, "tolerance_carbon_noon_conso_t")
    ecart = abs(carbon_total - noon_sum)
    if ecart > tol:
        return [
            CheckOutcome(
                "fail",
                f"R22 — conso Carbon {carbon_total} t vs Σ Noon {noon_sum} t (écart {ecart} t > {tol} t) "
                "— signalé (le Carbon n'est jamais correcteur).",
                {
                    "carbon_conso_t": str(carbon_total),
                    "noon_sum_t": str(noon_sum),
                    "ecart_t": str(ecart),
                    "tolerance_t": str(tol),
                    "carbon_corrector": False,
                },
                severity="warning",
            )
        ]
    return _ok("R22 — Carbon cohérent avec les Noon.")


# ════════════════════════════════════════════════════════════ Déclencheurs & routage


@dataclass
class QualityRunResult:
    """Synthèse d'un déclencheur (pour logs/cron)."""

    checks: int = 0
    fails: int = 0
    fail_results: list[QualityCheckResult] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.fail_results is None:
            self.fail_results = []


async def _catalog_seeded(db: AsyncSession) -> bool:
    """Le catalogue de règles est-il présent en base ? Fail-safe : sans catalogue,
    les déclencheurs s'abstiennent (jamais de QCR orphelin, FK ``rule_id``)."""
    try:
        row = await db.execute(select(ValidationRule.rule_id).limit(1))
        return row.first() is not None
    except Exception:
        return False


# ─────────────────────────────── Routage des alertes ───────────────────────────

# Rôles alertés par règle (patron des helpers ``services.notifications``).
_ADMIN = ("administrateur",)
_R14_CRITIQUE = ("manager_maritime", "administrateur")


def _alert_roles(r: QualityCheckResult) -> tuple[str, ...]:
    """Rôles à alerter pour un ``fail`` — sinon rien (journal seul)."""
    if r.rule_id == "R10":
        return _ADMIN
    if r.rule_id == "R24":
        return _ADMIN
    if r.rule_id == "R14" and r.severity_applied == "bloquant":
        return _R14_CRITIQUE
    if r.rule_id == "R27":
        # Notifie l'Environmental Manager dès le warning (pas seulement à
        # l'escalade bloquante) — CDC v0.7 §14.1 : « rappel système au Master
        # à l'approche de l'échéance » complété ici par une alerte siège dès
        # que la bascule est franchie sans Cut-off. Pas de rôle « Environmental
        # Manager » dédié dans app/permissions.py — mappé sur manager_maritime,
        # déjà utilisé pour R14 critique/R19 2ᵉ palier (même patron).
        return _R14_CRITIQUE
    return ()


async def _already_alerted(db: AsyncSession, r: QualityCheckResult) -> bool:
    """Idempotence : un ``fail`` identique (même règle + sujet) déjà présent —
    acquitté OU dans les 24 h — bloque une nouvelle notification. L'acquittement
    stoppe donc la re-notification ; sinon, une seule alerte par 24 h."""
    horizon = (r.executed_at or datetime.now(UTC)) - timedelta(hours=24)
    stmt = select(QualityCheckResult.id).where(
        QualityCheckResult.rule_id == r.rule_id,
        QualityCheckResult.subject_type == r.subject_type,
        QualityCheckResult.result == "fail",
        QualityCheckResult.id != r.id,
    )
    if r.subject_id is None:
        stmt = stmt.where(QualityCheckResult.subject_id.is_(None))
    else:
        stmt = stmt.where(QualityCheckResult.subject_id == r.subject_id)
    from sqlalchemy import or_

    stmt = stmt.where(
        or_(
            QualityCheckResult.acknowledged_at.isnot(None),
            QualityCheckResult.executed_at >= horizon,
        )
    ).limit(1)
    return (await db.execute(stmt)).first() is not None


async def route_alerts(db: AsyncSession, fail_results: list[QualityCheckResult]) -> int:
    """Route les alertes des ``fail`` selon leur règle (R10/R24 → Administrateur ;
    R14 critique → Manager maritime + Administrateur). Idempotent (dédup 24 h +
    acquittement). Renvoie le nombre de notifications créées. Best-effort."""
    from app.models.notification import Notification
    from app.services import notifications

    created = 0
    for r in fail_results:
        if r.result != "fail":
            continue
        roles = _alert_roles(r)
        if not roles:
            continue
        if await _already_alerted(db, r):
            continue
        link = f"/mrv/qualite?rule={r.rule_id}"
        if r.leg_id is not None:
            link += f"&leg_id={r.leg_id}"
        for role in roles:
            # Dédup notification (lien + rôle) — même garde que draft_reminders.
            exists = (
                await db.execute(
                    select(Notification.id)
                    .where(
                        Notification.link == link,
                        Notification.target_role == role,
                        Notification.is_archived.is_(False),
                    )
                    .limit(1)
                )
            ).first()
            if exists is not None:
                continue
            await notifications.create(
                db,
                type="info",
                title=f"Anomalie qualité MRV — {r.rule_id} ({r.severity_applied})",
                detail=(r.message or "")[:480],
                link=link,
                target_role=role,
            )
            created += 1
    return created


# ─────────────────────────────── Triggers par scope ────────────────────────────


async def run_report_rules_and_route(db: AsyncSession, report: EnvReport) -> QualityRunResult:
    """Scope ``report`` (R18/R22) — déclenché à la validation Master/siège."""
    if not await _catalog_seeded(db):
        return QualityRunResult()
    summary = await run_rules(
        db, "report", [report], leg=await _leg_of(db, report.leg_id), persist_passes=False
    )
    fails = [r for r in summary.results if r.result == "fail"]
    await route_alerts(db, fails)
    return QualityRunResult(checks=summary.total, fails=summary.failed, fail_results=fails)


async def run_bunker_rules_and_route(db: AsyncSession, bunker: BunkerOperation) -> QualityRunResult:
    """Scope ``bunker`` (R16/R23/R24) — déclenché à la validation/correction d'un soutage."""
    if not await _catalog_seeded(db):
        return QualityRunResult()
    summary = await run_rules(
        db, "bunker", [bunker], leg=await _leg_of(db, bunker.leg_id), persist_passes=False
    )
    fails = [r for r in summary.results if r.result == "fail"]
    await route_alerts(db, fails)
    return QualityRunResult(checks=summary.total, fails=summary.failed, fail_results=fails)


async def run_flgo_rules_and_route(db: AsyncSession, vessel_id: int) -> QualityRunResult:
    """Scope ``flgo`` (R25) — déclenché à l'import/sync FLGO d'un navire.

    Séquence = lectures FLGO du navire ordonnées par date (ctx.prev = lecture
    précédente pour le volet 2 « progression »)."""
    if not await _catalog_seeded(db):
        return QualityRunResult()
    readings = list(
        (
            await db.execute(
                select(FlgoReading)
                .where(FlgoReading.vessel_id == vessel_id)
                .order_by(FlgoReading.reading_datetime.asc(), FlgoReading.id.asc())
            )
        )
        .scalars()
        .all()
    )
    if not readings:
        return QualityRunResult()
    summary = await run_rules(db, "flgo", readings, persist_passes=False)
    fails = [r for r in summary.results if r.result == "fail"]
    await route_alerts(db, fails)
    return QualityRunResult(checks=summary.total, fails=summary.failed, fail_results=fails)


async def _leg_of(db: AsyncSession, leg_id: int | None) -> Leg | None:
    return await db.get(Leg, leg_id) if leg_id is not None else None


# ─────────────────────────────── Run nocturne ─────────────────────────────────


def _leg_is_active(leg: Leg) -> bool:
    """Voyage « actif » = non clôturé (pas d'approbation de clôture) et non annulé."""
    return _get(leg, "closure_approved_at") is None and _get(leg, "status") != "cancelled"


async def run_nightly_quality(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """Run nocturne : event + voyage + inter-rapports sur les legs ACTIFS (non
    clôturés) de chaque navire. Renvoie ``{legs_scanned, checks, fails}``.

    Les inter-rapports (IR01-IR05) sont des règles de scope ``event`` : elles
    s'exécutent sur la séquence ordonnée des événements finalisés du leg."""
    now = now or datetime.now(UTC)
    if not await _catalog_seeded(db):
        return {"legs_scanned": 0, "checks": 0, "fails": 0}

    from app.services import inter_event_compute as iec

    legs = list(
        (await db.execute(select(Leg).order_by(Leg.vessel_id.asc(), Leg.etd.asc()))).scalars().all()
    )
    legs_scanned = checks = fails = 0
    all_fails: list[QualityCheckResult] = []
    vessels: dict[int, Vessel] = {}

    for leg in legs:
        if not _leg_is_active(leg):
            continue
        legs_scanned += 1
        if leg.vessel_id is not None and leg.vessel_id not in vessels:
            vessels[leg.vessel_id] = await db.get(Vessel, leg.vessel_id)
        vessel = vessels.get(leg.vessel_id)
        # invalide le cache de calcul du leg pour ce run
        if hasattr(leg, "_lot8_computation"):
            with contextlib.suppress(Exception):
                del leg._lot8_computation
        events = await iec.finalized_events_for_leg(db, leg.id)
        if events:
            s = await run_rules(db, "event", events, vessel=vessel, leg=leg, persist_passes=False)
            checks += s.total
            fails += s.failed
            all_fails += [r for r in s.results if r.result == "fail"]
        sv = await run_rules(db, "voyage", [leg], vessel=vessel, leg=leg, persist_passes=False)
        checks += sv.total
        fails += sv.failed
        all_fails += [r for r in sv.results if r.result == "fail"]

    await route_alerts(db, all_fails)
    return {"legs_scanned": legs_scanned, "checks": checks, "fails": fails}
