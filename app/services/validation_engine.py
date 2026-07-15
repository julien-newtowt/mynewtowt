"""Moteur de règles de validation MRV — socle (LOT 2).

Ce module fournit :

1. **Le catalogue seedé** (``RULE_SEED`` = 33 règles, ``THRESHOLD_SEED`` =
   seuils paramétrables, ``DASHBOARD_SEED`` = paramètres dashboard) et une
   fonction de seed idempotente (``seed_reference_data``) utilisée par le
   boot dev (``create_all`` sans migration) et par l'action d'init admin.
2. **La résolution de seuils** (``get_threshold``) : (rule, vessel) →
   (rule, NULL) → défaut codé *fail-closed*, avec cache 60 s et
   ``invalidate_cache()`` — même patron que ``app.permissions``.
3. **Le registre de règles** (``@rule`` / ``RULES``), le contexte
   (``RuleContext``), le résultat (``CheckOutcome``) et l'exécuteur
   (``run_rules``) qui persiste un ``QualityCheckResult`` par *outcome* avec
   le snapshot des seuils consommés.
4. **Les premières règles STRUCTURELLES** (R01, R02, R11, R12, R13) qui
   n'exigent aucune table événementielle (elles opèrent sur des sujets
   duck-typés). Les règles liées aux entités futures (R08-R10, R14-R26,
   IR*) sont *seedées* mais **pas encore codées** — lot 8.

La sévérité appliquée est celle de ``ValidationRule.default_severity``
(l'override fin par seuil viendra au lot 8).
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# ════════════════════════════════════════════════════════════ Seed catalog

# (rule_id, domain, description, default_severity, scope, active)
RULE_SEED: tuple[tuple[str, str, str, str, str, bool], ...] = (
    (
        "R01",
        "Identité",
        "Nom/identité du navire manquant (bloquant) ou navire non reconnu (warning).",
        "bloquant",
        "event",
        True,
    ),
    (
        "R02",
        "Voyage",
        "Aucun voyage rattaché (leg_id) ; contrôle de format du leg_code (7 caractères).",
        "bloquant",
        "event",
        True,
    ),
    ("R03", "Type", "Type d'événement/rapport manquant ou non reconnu.", "bloquant", "event", True),
    ("R04", "Date", "Date manquante — obligatoire MRV.", "bloquant", "event", True),
    (
        "R05",
        "Position",
        "Position GPS manquante/hors plage ou indicateur N/S/E/W absent ; "
        "Thalos indisponible → saisie manuelle justifiée.",
        "bloquant",
        "event",
        True,
    ),
    (
        "R06",
        "ROB",
        "ROB manquant ou négatif (bloquant) ; =0 en Noon ou >300 t (warning).",
        "bloquant",
        "event",
        True,
    ),
    (
        "R07",
        "Ports",
        "UNLOCODE de port absent ou non conforme (5 caractères).",
        "warning",
        "event",
        True,
    ),
    (
        "R08",
        "Consommation",
        "Consommation négative (bloquant), =0 en Noon (warning), hors seuil cible "
        "(seuil_conso_ref_l_j) ; complétude de la conso d'escale.",
        "warning",
        "event",
        True,
    ),
    (
        "R09",
        "Distance/Vitesse",
        "Distance=0 en Noon ou vitesse implicite hors bornes ; cohérence de la position "
        "manuelle et des horodatages d'escale vs AIS/ShoreManager.",
        "warning",
        "event",
        True,
    ),
    (
        "R10",
        "Compteurs moteur",
        "Monotonie des compteurs moteur ; réinitialisation légitime confirmée par "
        "l'Administrateur (sinon escalade bloquante).",
        "warning",
        "event",
        True,
    ),
    (
        "R11",
        "Bornes plausibles",
        "Valeurs numériques (ROB annexes urée/eau douce, mesures) dans des bornes "
        "plausibles paramétrées.",
        "warning",
        "event",
        True,
    ),
    (
        "R12",
        "Météo/relevés",
        "Moins de 3 relevés par jour, ou valeurs identiques sur des relevés consécutifs "
        "(copier-coller).",
        "warning",
        "event",
        True,
    ),
    (
        "R13",
        "Cohérence séquence",
        "Complétude des champs (voilure/températures/tirants) et cohérence chronologique "
        "de la séquence d'événements (datetime strictement croissant).",
        "info",
        "event",
        True,
    ),
    (
        "R14",
        "Cohérence ROB",
        "Continuité du ROB en traversée (R14a) et en escale (R14b) : ROB théorique vs "
        "déclaré, écart mineur/majeur/critique.",
        "bloquant",
        "voyage",
        True,
    ),
    (
        "R15",
        "Écart consommation",
        "Écart entre conso calculée (compteurs), déclarée (delta ROB) et cible "
        "(seuil_conso_ref_l_j).",
        "warning",
        "voyage",
        True,
    ),
    (
        "R16",
        "Densité",
        "Écart de densité volume/masse entre soutage, cuves et compteurs "
        "(densite_defaut_t_m3 ± tolérance).",
        "warning",
        "bunker",
        True,
    ),
    (
        "R17",
        "ROB vs FLGO",
        "Écart entre ROB déclaré MyTOWT et ROB FLGO (Marad) au Departure/Arrival, "
        "jointure par date la plus proche.",
        "warning",
        "voyage",
        True,
    ),
    (
        "R18",
        "Traçabilité",
        "Toute donnée pré-remplie modifiée doit être confirmée et justifiée (pop-up).",
        "bloquant",
        "report",
        True,
    ),
    (
        "R19",
        "Brouillon",
        "Événement non finalisé au-delà de delai_rappel_brouillon_h (rappel Master ; "
        "second seuil → Environmental Manager).",
        "warning",
        "event",
        True,
    ),
    (
        "R20",
        "Cargo",
        "Cargo MRV (DWT carried) ≥ cargaison déclarée (B/L) pour un voyage chargé "
        "(Info tant que D10 non résolu).",
        "info",
        "voyage",
        True,
    ),
    (
        "R21",
        "Durée entre rapports",
        "Durée déclarée depuis le dernier rapport cohérente avec l'écart réel entre "
        "horodatages (tolerance_duree_rapport_h).",
        "warning",
        "event",
        True,
    ),
    (
        "R22",
        "Carbon vs Noon",
        "Consommation totale Carbon (Departure/Arrival) cohérente avec la somme des Noon "
        "(tolerance_carbon_noon_conso_t).",
        "warning",
        "report",
        True,
    ),
    (
        "R23",
        "Soutage",
        "Soutage BDN cohérent avec la variation FLGO (tolerance_bdn_flgo_t) ; volumes "
        "alloués ≤ capacité physique des cuves (bloquant).",
        "warning",
        "bunker",
        True,
    ),
    (
        "R24",
        "Complétude soutage",
        "Chaque soutage BDN a une lecture FLGO 'Received' correspondante sous "
        "delai_flgo_bunkering_j.",
        "warning",
        "bunker",
        True,
    ),
    (
        "R25",
        "Cohérence FLGO",
        "Deux lectures FLGO consécutives cohérentes entre elles (tolerance_flgo_interne_m3) "
        "— signale sans corriger.",
        "warning",
        "flgo",
        True,
    ),
    (
        "R26",
        "Chaînage voyages",
        "Port d'arrivée du voyage N = port de départ du voyage N+1 (sauf repositionnement "
        "codifié).",
        "warning",
        "voyage",
        True,
    ),
    (
        "R27",
        "Cut-off fin d'année",
        "Voyage en cours à la bascule d'année civile (31/12 24:00 UTC) sans événement "
        "Cut-off finalisé — bloque la consolidation MRV au-delà de tolerance_cutoff_h.",
        "warning",
        "voyage",
        True,
    ),
    (
        "R28",
        "Distance haversine vs loguée (SOSP)",
        "Distance haversine calculée entre deux Noon consécutifs vs distance loguée par "
        "le bord (delta distance_from_sosp_nm) — sous-estimation systématique possible "
        "en flotte vélique (louvoiement), dégrade artificiellement l'EF_MRV affiché "
        "(Matrice §8, revue technique 09/07). N'est jamais corrigée automatiquement.",
        "warning",
        "event",
        True,
    ),
    (
        "IR01",
        "Séquence dates",
        "Doublon (même date+type, bloquant), saut >2 jours (warning), date antérieure au "
        "rapport précédent (bloquant).",
        "bloquant",
        "event",
        True,
    ),
    (
        "IR02",
        "Séquence ROB",
        "ROB(J) vs ROB(J-1) − conso ± bunkering : écart >5 t bloquant, >0,5 t warning.",
        "bloquant",
        "event",
        True,
    ),
    ("IR03", "ROB figé", "ROB figé malgré une consommation >0,05 t.", "warning", "event", True),
    (
        "IR04",
        "Compteur carburant",
        "Compteur carburant (L) régressant d'un rapport à l'autre (sauf reset documenté).",
        "bloquant",
        "event",
        True,
    ),
    (
        "IR05",
        "Position figée",
        "Position identique au rapport précédent malgré une distance déclarée >5 nm.",
        "warning",
        "event",
        True,
    ),
)

# (rule_id, parameter_name, value, unit, provisional, note) — vessel_id = NULL.
# 16 paramètres de la Matrice §6 + 2 densité (R16, absorbés de mrv_parameters)
# + 2 bornes de plausibilité R11 (vecteurs du contrôle paramétrable en lot 2).
THRESHOLD_SEED: tuple[tuple[str, str, str, str, bool, str], ...] = (
    (
        "R08",
        "seuil_conso_ref_l_j",
        "750",
        "L/j",
        False,
        "Seuil de consommation de référence (SMS) — déjà en usage.",
    ),
    (
        "R19",
        "delai_rappel_brouillon_h",
        "24",
        "h",
        False,
        "Délai de rappel d'un brouillon non finalisé.",
    ),
    # LOT 4 — second seuil R19 : au-delà, alerte au siège (Environmental
    # Manager) en plus du rappel Master. Provisoire (calibrage voyage pilote).
    (
        "R19",
        "delai_alerte_siege_brouillon_h",
        "48",
        "h",
        True,
        "Délai d'alerte siège d'un brouillon non finalisé (2e seuil R19, proposition).",
    ),
    (
        "R14",
        "seuil_rob_ecart_mineur_t",
        "0.5",
        "t",
        True,
        "Borne d'écart ROB mineur (proposition Q8/D6, à confirmer métier).",
    ),
    (
        "R14",
        "seuil_rob_ecart_majeur_t",
        "2",
        "t",
        True,
        "Borne d'écart ROB majeur (proposition Q8/D6, à confirmer métier).",
    ),
    (
        "R14",
        "seuil_rob_ecart_critique_t",
        "5",
        "t",
        True,
        "Borne d'écart ROB critique — bloquant (proposition Q8/D6, à confirmer métier).",
    ),
    (
        "R09",
        "tolerance_distance_manuelle_nm",
        "20",
        "nm",
        True,
        "Tolérance distance position manuelle vs trajectoire Thalos (proposition).",
    ),
    (
        "R17",
        "tolerance_flgo_ecart_temps_h",
        "120",
        "h",
        True,
        "Écart temporel max avant déclassement du rapprochement FLGO en Info (≈5 j).",
    ),
    (
        "R20",
        "seuil_cargo_mrv_ecart_t",
        "5",
        "t",
        True,
        "Tolérance Cargo MRV vs B/L — bloqué par D10 (proposition).",
    ),
    (
        "R08",
        "duree_escale_alerte_conso_manquante_j",
        "2",
        "j",
        True,
        "Durée d'escale au-delà de laquelle une conso nulle/absente alerte (proposition).",
    ),
    (
        "R08",
        "conso_estimee_defaut_t_j",
        "0.21",
        "t/j",
        True,
        "Conso d'escale estimée par défaut — valeur constatée 2025, à valider.",
    ),
    (
        "R09",
        "tolerance_datetime_escale_h",
        "6",
        "h",
        True,
        "Écart max entre horodatages d'escale déclarés et AIS/ShoreManager (proposition).",
    ),
    (
        "R22",
        "tolerance_carbon_noon_conso_t",
        "1",
        "t",
        True,
        "Écart max conso Carbon vs somme Noon (proposition).",
    ),
    (
        "R23",
        "tolerance_bdn_flgo_t",
        "2",
        "t",
        True,
        "Écart max masse BDN vs variation FLGO (proposition).",
    ),
    (
        "R24",
        "delai_flgo_bunkering_j",
        "5",
        "j",
        True,
        "Fenêtre de recoupement soutage BDN ↔ FLGO 'Received' (défaut 5 j).",
    ),
    # LOT 6 — rattachement automatique du soutage au voyage suivant l'escale
    # de livraison (services.bunkering.resolve_leg_for_bunker). Ajouté au
    # catalogue existant (seed idempotent, cf. seed_reference_data) SANS
    # toucher aux seuils ci-dessus. 25 j = fenêtre observée sur le dataset
    # 2025 (inventaire) ; à confirmer métier (cf. Q8, même statut que les
    # autres seuils provisoires de ce catalogue).
    (
        "R24",
        "fenetre_rattachement_bunker_j",
        "25",
        "j",
        True,
        "Fenêtre de rattachement automatique du soutage au voyage suivant "
        "(au-delà : leg_id NULL, choix manuel possible).",
    ),
    (
        "R25",
        "tolerance_flgo_interne_m3",
        "2",
        "m3",
        True,
        "Écart max entre lectures FLGO consécutives (proposition).",
    ),
    (
        "R21",
        "tolerance_duree_rapport_h",
        "2",
        "h",
        True,
        "Écart max durée déclarée vs écart réel entre rapports (proposition).",
    ),
    # ─── LOT 8 — seuils manquants au catalogue (tous provisoires, Q8) ───
    # Ajoutés par le moteur de règles complet ; à confirmer métier au
    # calibrage (voyage pilote). Consommés EXCLUSIVEMENT via get_threshold.
    (
        "R04",
        "tolerance_datetime_futur_h",
        "24",
        "h",
        True,
        "Tolérance d'un horodatage dans le futur avant alerte de plausibilité (R04).",
    ),
    (
        "R10",
        "delai_confirmation_reset_j",
        "3",
        "j",
        True,
        "Délai au-delà duquel une régression compteur non confirmée passe "
        "de warning (→ admin) à bloquant (escalade R10, Matrice §3).",
    ),
    (
        "IR03",
        "ir03_min_reports_figes",
        "3",
        "reports",
        True,
        "Nombre de relevés consécutifs à ROB strictement figé avant alerte "
        "(IR03 ; cas réel dossier : figé 4 j).",
    ),
    (
        "IR03",
        "ir03_conso_min_t",
        "0.05",
        "t",
        True,
        "Consommation minimale entre relevés au-delà de laquelle un ROB figé "
        "est incohérent (IR03 ; valeur notebook QC).",
    ),
    (
        "IR05",
        "ir05_min_reports_figes",
        "3",
        "reports",
        True,
        "Nombre de relevés consécutifs à position strictement figée en mer " "avant alerte (IR05).",
    ),
    (
        "R16",
        "densite_defaut_t_m3",
        "0.845",
        "t/m3",
        False,
        "Densité MDO par défaut (SMS) — absorbée de mrv_parameters.",
    ),
    (
        "R16",
        "densite_tolerance_t_m3",
        "0.015",
        "t/m3",
        False,
        "Tolérance densité MDO (SMS) — absorbée de mrv_parameters.",
    ),
    (
        "R11",
        "seuil_conso_ref_l_j",
        "750",
        "L/j",
        False,
        "Borne haute de plausibilité de la conso journalière (aligné SMS 750 L/j).",
    ),
    (
        "R11",
        "borne_max_rob_t",
        "300",
        "t",
        False,
        "Borne haute de plausibilité du ROB (aligné R06 >300 t).",
    ),
    (
        "R12",
        "min_releves_meteo_jour",
        "3",
        "relevés",
        True,
        "Nombre minimal de relevés météo horodatés (créneaux 4 h) attendus par "
        "NoonEvent — volet « fréquence » de R12 (Matrice §1), jamais codé "
        "jusqu'ici (G7).",
    ),
    (
        "R27",
        "tolerance_cutoff_h",
        "24",
        "h",
        True,
        "Délai de tolérance après la bascule d'année avant escalade bloquante "
        "de R27 (CDC v0.7 §14.1, proposition).",
    ),
    (
        "R27",
        "rappel_cutoff_avant_j",
        "7",
        "j",
        True,
        "Fenêtre de rappel au Master avant l'approche de la bascule d'année "
        "(CDC v0.7 §9.2 : « rappel système au Master à l'approche de "
        "l'échéance »), proposition.",
    ),
    (
        "R28",
        "tolerance_distance_haversine_nm",
        "20",
        "nm",
        True,
        "Écart acceptable entre distance haversine calculée et distance loguée "
        "(SOSP) — aucune valeur proposée par la Matrice §8 (« à confirmer avec "
        "le métier, nouveau »), alignée sur tolerance_distance_manuelle_nm (R09) "
        "à défaut d'un chiffre métier.",
    ),
)

# (parameter_name, value, unit) — vessel_id = NULL.
DASHBOARD_SEED: tuple[tuple[str, str, str], ...] = (
    ("occupancy_rate_pct", "70", "%"),
    ("vessel_capacity_ref_t", "1100", "t"),
    ("ef_container_ship_gco2_tkm", "16", "gCO2/t.km"),
    ("ef_airfreight_gco2_tkm", "800", "gCO2/t.km"),
)

# Défauts codés *fail-closed* : dernier recours quand la DB n'a aucune ligne
# (ou est en erreur). Dérivé du seed → {parameter_name: (Decimal, unit)}.
CODED_DEFAULTS: dict[str, tuple[Decimal, str | None]] = {
    param: (Decimal(value), unit) for (_rid, param, value, unit, _prov, _note) in THRESHOLD_SEED
}

# ════════════════════════════════════════════════════ Résolution de seuils


@dataclass(frozen=True)
class ThresholdValue:
    """Seuil résolu + sa provenance (pour le snapshot d'audit)."""

    rule_id: str
    parameter_name: str
    vessel_id: int | None
    value: Decimal
    unit: str | None
    source: str  # "vessel" | "global" | "coded_default"
    provisional: bool

    def as_dict(self) -> dict[str, Any]:
        # ``value`` en str : préserve la précision et reste JSON-sérialisable
        # (le type JSON de la colonne ``details`` ne sait pas encoder Decimal).
        return {
            "rule_id": self.rule_id,
            "parameter_name": self.parameter_name,
            "vessel_id": self.vessel_id,
            "value": str(self.value),
            "unit": self.unit,
            "source": self.source,
            "provisional": self.provisional,
        }


_THRESHOLD_TTL_SECONDS = 60.0
# cache : {(rule_id, vessel_id, parameter_name): (Decimal, unit, provisional)}
_threshold_cache: dict[tuple[str, int | None, str], tuple[Decimal, str | None, bool]] | None = None
_threshold_loaded_at: float = 0.0


def invalidate_cache() -> None:
    """Force la relecture DB des seuils au prochain ``get_threshold``."""
    global _threshold_cache, _threshold_loaded_at
    _threshold_cache = None
    _threshold_loaded_at = 0.0


async def _load_thresholds(
    db: AsyncSession,
) -> dict[tuple[str, int | None, str], tuple[Decimal, str | None, bool]]:
    """Charge toutes les lignes ``validation_rule_thresholds`` (cache 60 s).

    FAIL CLOSED : toute erreur DB (table absente, connexion HS…) renvoie
    ``{}`` → la résolution retombe sur ``CODED_DEFAULTS``. Le résultat (même
    vide sur erreur) est mis en cache pour ne pas marteler une DB en échec.
    """
    global _threshold_cache, _threshold_loaded_at
    now = time.monotonic()
    if _threshold_cache is not None and (now - _threshold_loaded_at) < _THRESHOLD_TTL_SECONDS:
        return _threshold_cache

    rows: dict[tuple[str, int | None, str], tuple[Decimal, str | None, bool]] = {}
    try:
        from app.models.validation import ValidationRuleThreshold

        for t in (await db.execute(select(ValidationRuleThreshold))).scalars().all():
            rows[(t.rule_id, t.vessel_id, t.parameter_name)] = (
                Decimal(t.value),
                t.unit,
                bool(t.provisional),
            )
    except Exception:
        rows = {}

    _threshold_cache = rows
    _threshold_loaded_at = now
    return rows


async def get_threshold(
    db: AsyncSession,
    rule_id: str,
    parameter_name: str,
    vessel_id: int | None = None,
) -> ThresholdValue | None:
    """Résout un seuil : (rule, vessel) → (rule, NULL) → défaut codé.

    Renvoie ``None`` si le paramètre est totalement inconnu (ni en base, ni
    dans ``CODED_DEFAULTS``) — la règle appelante décide alors d'ignorer le
    contrôle. *Fail-closed* : sur erreur DB, ``_load_thresholds`` renvoie
    ``{}`` → on retombe sur le défaut codé.
    """
    rows = await _load_thresholds(db)

    if vessel_id is not None:
        hit = rows.get((rule_id, vessel_id, parameter_name))
        if hit is not None:
            value, unit, prov = hit
            return ThresholdValue(rule_id, parameter_name, vessel_id, value, unit, "vessel", prov)

    hit = rows.get((rule_id, None, parameter_name))
    if hit is not None:
        value, unit, prov = hit
        return ThresholdValue(rule_id, parameter_name, None, value, unit, "global", prov)

    coded = CODED_DEFAULTS.get(parameter_name)
    if coded is not None:
        value, unit = coded
        return ThresholdValue(rule_id, parameter_name, None, value, unit, "coded_default", False)

    return None


# ════════════════════════════════════════════════════ Contexte & résultats


def _get(subject: Any, name: str, default: Any = None) -> Any:
    """Accès attribut tolérant objet OU dict."""
    if isinstance(subject, dict):
        return subject.get(name, default)
    return getattr(subject, name, default)


def _get_loaded(subject: Any, name: str) -> Any:
    """Comme ``_get``, mais ne déclenche JAMAIS un lazy-load synchrone d'une
    relation ORM non chargée — incompatible avec une session async (crash
    « greenlet_spawn has not been called »), qui surviendrait si le sujet a
    été construit/flushé sans jamais avoir touché cet attribut (contrairement
    au flux normal onboard_router, cf. ``_sync_event_readings``, qui l'assigne
    toujours avant finalisation). ``None`` si non chargée ou absente."""
    try:
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(subject, raiseerr=False)
    except Exception:
        insp = None
    if insp is not None and name in insp.unloaded:
        return None
    return _get(subject, name)


def _first(subject: Any, names: tuple[str, ...]) -> Any:
    for n in names:
        v = _get(subject, n)
        if v is not None:
            return v
    return None


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _norm_dt(dt: Any) -> datetime | None:
    """Normalise un datetime en naïf UTC pour une comparaison robuste."""
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


@dataclass
class RuleContext:
    """Contexte passé à une règle pour un sujet donné d'une séquence.

    ``subject`` = sujet courant ; ``subjects`` = séquence complète transmise
    à ``run_rules`` (ordonnée par l'appelant) ; ``index`` = position du sujet
    courant. Les règles séquentielles (R12, R13) regardent ``prev``.
    """

    db: AsyncSession
    rule_id: str
    subject: Any
    subjects: list[Any]
    index: int
    now: datetime
    vessel: Any = None
    leg: Any = None
    _consumed: list[ThresholdValue] = field(default_factory=list)

    @property
    def vessel_id(self) -> int | None:
        if self.vessel is None:
            return None
        return _get(self.vessel, "id")

    @property
    def prev(self) -> Any:
        return self.subjects[self.index - 1] if self.index > 0 else None

    async def threshold(self, parameter_name: str, coded_default: Any = None) -> Decimal | None:
        """Résout un seuil et l'enregistre dans le snapshot du run."""
        tv = await get_threshold(self.db, self.rule_id, parameter_name, self.vessel_id)
        if tv is None and coded_default is not None:
            tv = ThresholdValue(
                self.rule_id,
                parameter_name,
                None,
                Decimal(str(coded_default)),
                None,
                "coded_default_inline",
                False,
            )
        if tv is None:
            return None
        self._consumed.append(tv)
        return tv.value


@dataclass
class CheckOutcome:
    """Verdict d'une règle pour un sujet. ``subject`` peut cibler un autre
    sujet que le courant (défaut = sujet courant).

    LOT 8 — ``severity`` permet à une règle d'imposer la sévérité de CE
    verdict (par ex. R06 « ROB manquant » = bloquant mais « ROB=0 » = warning,
    R14 mineur/majeur → warning et critique → bloquant, IR04 régression =
    bloquant). Laisser ``None`` → la sévérité par défaut de la règle
    (``ValidationRule.default_severity``) s'applique, préservant la sémantique
    du lot 2 (une règle = une sévérité). La Matrice grade explicitement la
    sévérité par condition ; ce hook rend cette graduation exprimable sans
    dupliquer les règles."""

    result: str  # "pass" | "fail"
    message: str = ""
    details: dict | None = None
    subject: Any = None
    severity: str | None = None  # override par verdict (cf. docstring)


@dataclass
class RunSummary:
    run_id: str
    total: int
    passed: int
    failed: int
    by_severity: dict[str, int]
    results: list[Any]


# ════════════════════════════════════════════════════════ Registre & règles

RuleFn = Callable[[RuleContext], Awaitable[list[CheckOutcome]]]
RULES: dict[str, RuleFn] = {}


def rule(rule_id: str) -> Callable[[RuleFn], RuleFn]:
    """Décorateur d'enregistrement d'une règle."""

    def deco(fn: RuleFn) -> RuleFn:
        RULES[rule_id] = fn
        return fn

    return deco


# Jeux de candidats (sujets duck-typés — noms possibles selon la source).
_VESSEL_ATTRS = ("vessel_id", "vessel_name", "vessel", "vessel_code")
_DATETIME_ATTRS = (
    "datetime_utc",
    "datetime_local",
    "recorded_at",
    "occurred_at",
    "event_datetime",
    "datetime",
    "date",
)
_MEASUREMENT_ATTRS = (
    "latitude",
    "longitude",
    "lat",
    "lon",
    "lat_decimal",
    "lon_decimal",
    "rob_t",
    "rob_l",
    "conso_l_j",
    "fuel_consumed_24h_l",
    "distance_nm",
    "tws_kn",
    "aws_kn",
    "ship_speed_kn",
    "speed_kn",
)
_CONSO_ATTRS = ("conso_l_j", "fuel_consumed_24h_l", "conso_journaliere_l")

# leg_code : 1 chiffre + 5 lettres + 1 chiffre = 7 caractères.
_LEG_CODE_RE = re.compile(r"^\d[A-Z]{5}\d$")

# R12 : nombre minimal de champs de mesure identiques pour crier « copier-coller ».
_R12_MIN_IDENTICAL_FIELDS = 2


@rule("R01")
async def _r01_required_fields(ctx: RuleContext) -> list[CheckOutcome]:
    """R01 — champs obligatoires présents (identité navire + date).

    Lot 2 : présence de l'identité navire ET d'une date. Les contrôles fins
    R03-R07 (type, format position/ports…) arrivent au lot 8.
    """
    missing: list[str] = []
    if not _present(_first(ctx.subject, _VESSEL_ATTRS)):
        missing.append("navire")
    if not _present(_first(ctx.subject, _DATETIME_ATTRS)):
        missing.append("date")
    if missing:
        return [
            CheckOutcome(
                "fail",
                f"Champs obligatoires manquants : {', '.join(missing)}.",
                {"missing": missing},
            )
        ]
    return [CheckOutcome("pass", "Champs obligatoires présents.")]


@rule("R02")
async def _r02_voyage_binding(ctx: RuleContext) -> list[CheckOutcome]:
    """R02 — rattachement voyage (leg_id) + format leg_code (7 caractères).

    LOT 8 (réconciliation retex — fiche AV-001) : quand le contexte voyage
    est chargé (``ctx.leg``), vérifie AUSSI la cohérence des segments PAYS du
    leg_code (caractères 3-4 = pays de départ, 5-6 = pays d'arrivée) avec les
    ports réels du leg. Le cas réel ``1AFRBZ6`` (« BZ » Belize au lieu de
    « BR » Brésil sur BRSSO/Santos) passe le contrôle de FORMAT (1 chiffre +
    5 lettres + 1 chiffre) mais pas cette cohérence — c'était l'angle mort du
    lot 2. Sévérité **warning** pour ce volet : anomalie de codification du
    voyage (donnée leg), qui ne doit pas bloquer la finalisation d'un
    événement de bord (le format invalide / voyage absent restent bloquants
    via la sévérité par défaut de la règle).
    """
    leg_present = _present(_first(ctx.subject, ("leg_id", "leg")))
    leg_code = _get(ctx.subject, "leg_code")
    if leg_code is None:
        leg_obj = _get(ctx.subject, "leg")
        if leg_obj is not None:
            leg_code = _get(leg_obj, "leg_code")
    if leg_code is None and ctx.leg is not None:
        leg_code = _get(ctx.leg, "leg_code")

    if not leg_present:
        return [CheckOutcome("fail", "Aucun voyage rattaché (leg_id manquant).", {"leg_id": None})]
    code = str(leg_code).strip() if _present(leg_code) else ""
    if code:
        if len(code) != 7 or not _LEG_CODE_RE.match(code):
            return [
                CheckOutcome(
                    "fail",
                    f"Format de leg_code invalide : {code!r} (attendu 7 caractères, "
                    "1 chiffre + 5 lettres + 1 chiffre).",
                    {"leg_code": code, "length": len(code)},
                )
            ]
        # Volet pays (AV-001) — seulement si le voyage réel est en contexte.
        if ctx.leg is not None:
            mismatches: list[str] = []
            try:
                from app.models.port import Port

                for pid_attr, seg, label in (
                    ("departure_port_id", code[2:4], "départ"),
                    ("arrival_port_id", code[4:6], "arrivée"),
                ):
                    pid = _get(ctx.leg, pid_attr)
                    if pid is None:
                        continue
                    port = await ctx.db.get(Port, pid)
                    country = (
                        str(_get(port, "country") or "").strip().upper() if port is not None else ""
                    )
                    if country and seg != country:
                        mismatches.append(f"pays {label} {seg!r} ≠ port réel {country!r}")
            except Exception:
                mismatches = []  # contexte non requêtable → volet non évalué
            if mismatches:
                return [
                    CheckOutcome(
                        "fail",
                        f"leg_code {code!r} incohérent avec les ports du voyage : "
                        + " ; ".join(mismatches)
                        + " (cas type AV-001 « 1AFRBZ6 »).",
                        {"leg_code": code, "mismatches": mismatches},
                        severity="warning",
                    )
                ]
    return [CheckOutcome("pass", "Voyage rattaché, leg_code conforme.")]


@rule("R11")
async def _r11_plausible_bounds(ctx: RuleContext) -> list[CheckOutcome]:
    """R11 — valeurs numériques dans des bornes plausibles paramétrées.

    Lot 2 : borne haute de la conso journalière (``seuil_conso_ref_l_j``,
    défaut 750) et du ROB (``borne_max_rob_t``, défaut 300), plus la borne
    basse 0. Les seuils sont résolus en base (override navire possible) → le
    verdict change sans redéploiement (critère d'acceptation du lot).
    """
    checks: list[dict[str, Any]] = []
    violations: list[str] = []

    # borne conso journalière
    conso = _as_decimal(_first(ctx.subject, _CONSO_ATTRS))
    if conso is not None:
        ceil = await ctx.threshold("seuil_conso_ref_l_j", coded_default=750)
        ok = conso >= 0 and (ceil is None or conso <= ceil)
        checks.append(
            {
                "field": "conso_l_j",
                "value": str(conso),
                "max": (str(ceil) if ceil is not None else None),
                "ok": ok,
            }
        )
        if not ok:
            violations.append(f"conso {conso} hors [0, {ceil}] L/j")

    # borne ROB
    rob = _as_decimal(_first(ctx.subject, ("rob_t", "rob")))
    if rob is not None:
        ceil = await ctx.threshold("borne_max_rob_t", coded_default=300)
        ok = rob >= 0 and (ceil is None or rob <= ceil)
        checks.append(
            {
                "field": "rob_t",
                "value": str(rob),
                "max": (str(ceil) if ceil is not None else None),
                "ok": ok,
            }
        )
        if not ok:
            violations.append(f"ROB {rob} hors [0, {ceil}] t")

    if not checks:
        return [CheckOutcome("pass", "Aucune valeur numérique bornée à contrôler.", {"checks": []})]
    if violations:
        return [
            CheckOutcome(
                "fail",
                "Valeur(s) hors bornes plausibles : " + " ; ".join(violations),
                {"checks": checks},
            )
        ]
    return [
        CheckOutcome("pass", "Valeurs numériques dans les bornes plausibles.", {"checks": checks})
    ]


@rule("R12")
async def _r12_measurement_quality(ctx: RuleContext) -> list[CheckOutcome]:
    """R12 — qualité des relevés météo (Matrice §1, deux volets) :

    - **fréquence** (G7) : un NoonEvent porte moins de ``min_releves_meteo_jour``
      relevés météo horodatés (créneaux 4 h, ``NAV_TIME_SLOTS``, sur les 6
      possibles) — chaque ligne ``weather_readings`` correspond à un créneau
      **effectivement saisi** (aucune ligne n'est créée pour un créneau vide,
      cf. ``onboard_router._sync_event_readings``), donc ``len(...)`` est
      directement le compte de relevés du jour. Duck-typé : s'abstient si le
      sujet ne porte pas cet attribut (seul NoonEvent l'expose) ou si la
      relation n'est pas chargée (``_get_loaded``, jamais de lazy-load
      synchrone) ;
    - **copier-coller** : sujet identique au précédent sur les mesures.
    """
    weather_readings = _get_loaded(ctx.subject, "weather_readings")
    if weather_readings is not None:
        seuil = await ctx.threshold("min_releves_meteo_jour", coded_default=3)
        count = len(weather_readings)
        if seuil is not None and count < seuil:
            return [
                CheckOutcome(
                    "fail",
                    f"R12 — {count} relevé(s) météo (< {seuil} attendus/jour).",
                    {"releves_meteo": count, "seuil": str(seuil)},
                    severity="warning",
                )
            ]
    prev = ctx.prev
    if prev is None:
        return [CheckOutcome("pass", "Premier relevé de la séquence.")]
    identical: list[str] = []
    compared = 0
    for f in _MEASUREMENT_ATTRS:
        cur_v = _get(ctx.subject, f)
        prev_v = _get(prev, f)
        if cur_v is None or prev_v is None:
            continue
        compared += 1
        if str(cur_v) == str(prev_v):
            identical.append(f)
    if compared >= _R12_MIN_IDENTICAL_FIELDS and len(identical) == compared:
        return [
            CheckOutcome(
                "fail",
                f"Valeurs identiques au relevé précédent sur {len(identical)} champ(s) de mesure "
                "(copier-coller ?).",
                {"identical_fields": identical, "compared": compared},
            )
        ]
    return [
        CheckOutcome(
            "pass",
            "Relevé distinct du précédent.",
            {"identical_fields": identical, "compared": compared},
        )
    ]


@rule("R13")
async def _r13_chronology(ctx: RuleContext) -> list[CheckOutcome]:
    """R13 — chronologie : horodatage strictement croissant dans la séquence.

    Lot 2 : contrôle de cohérence chronologique (doublon/antériorité). La
    complétude des champs (sens Matrice) et l'affinage de sévérité arrivent
    au lot 8. Sévérité seedée = info (fidèle à la Matrice).
    """
    prev = ctx.prev
    if prev is None:
        return [CheckOutcome("pass", "Premier relevé de la séquence.")]
    cur_dt = _norm_dt(_first(ctx.subject, _DATETIME_ATTRS))
    prev_dt = _norm_dt(_first(prev, _DATETIME_ATTRS))
    if cur_dt is None or prev_dt is None:
        return [CheckOutcome("pass", "Horodatage indisponible (présence traitée par R01/R04).")]
    if cur_dt <= prev_dt:
        return [
            CheckOutcome(
                "fail",
                f"Horodatage non strictement croissant : {cur_dt.isoformat()} ≤ {prev_dt.isoformat()} "
                "(doublon ou antériorité).",
                {"current": cur_dt.isoformat(), "previous": prev_dt.isoformat()},
            )
        ]
    return [CheckOutcome("pass", "Chronologie croissante.")]


# ════════════════════════════════════════════════════════════ Exécuteur


def _subject_ref(subject: Any, scope: str) -> tuple[str, int | None]:
    """Déduit (subject_type, subject_id) d'un sujet duck-typé."""
    st = _get(subject, "subject_type") or _get(subject, "_subject_type")
    if not st:
        st = _get(subject, "__tablename__")
    if not st:
        cls = type(subject).__name__
        st = scope if cls in ("SimpleNamespace", "dict") else cls
    sid = _get(subject, "id")
    return str(st)[:40], (int(sid) if isinstance(sid, int) else None)


def _resolve_leg_id(leg: Any, subject: Any) -> int | None:
    if leg is not None:
        lid = _get(leg, "id")
        if isinstance(lid, int):
            return lid
    lid = _get(subject, "leg_id")
    return lid if isinstance(lid, int) else None


async def _active_rules_for_scope(db: AsyncSession, scope: str) -> list[tuple[str, str]]:
    """Règles actives du scope → [(rule_id, default_severity)].

    Fail-closed : sur erreur DB, retombe sur le catalogue codé ``RULE_SEED``.
    """
    try:
        from app.models.validation import ValidationRule

        rows = (
            await db.execute(
                select(ValidationRule.rule_id, ValidationRule.default_severity).where(
                    ValidationRule.scope == scope, ValidationRule.active.is_(True)
                )
            )
        ).all()
        if rows:
            return [(r[0], r[1]) for r in rows]
    except Exception:
        pass
    return [(rid, sev) for (rid, _d, _desc, sev, sc, active) in RULE_SEED if sc == scope and active]


async def run_rules(
    db: AsyncSession,
    scope: str,
    subjects: list[Any],
    *,
    vessel: Any = None,
    leg: Any = None,
    run_id: str | None = None,
    persist_passes: bool = True,
) -> RunSummary:
    """Exécute les règles actives du ``scope`` sur ``subjects``.

    Persiste un ``QualityCheckResult`` par *outcome* (avec le snapshot des
    seuils consommés dans ``details``) et renvoie la synthèse. Une règle qui
    lève une exception → un *outcome* fail de sévérité ``info`` (message
    technique) — jamais de crash du run. ``subjects`` doit être ordonné pour
    les règles séquentielles (R12/R13).
    """
    from app.models.validation import QualityCheckResult

    run_id = run_id or uuid.uuid4().hex
    now = datetime.now(UTC)
    subjects = list(subjects)
    active = await _active_rules_for_scope(db, scope)

    results: list[QualityCheckResult] = []
    passed = failed = 0
    by_severity: dict[str, int] = {}

    def _persist(rid, target, result, severity, message, details) -> None:
        nonlocal passed, failed
        st, sid = _subject_ref(target, scope)
        qcr = QualityCheckResult(
            rule_id=rid,
            subject_type=st,
            subject_id=sid,
            leg_id=_resolve_leg_id(leg, target),
            run_id=run_id,
            result=result,
            severity_applied=severity,
            message=(message or None),
            details=(details or None),
            executed_at=now,
        )
        db.add(qcr)
        results.append(qcr)
        if result == "fail":
            failed += 1
            by_severity[severity] = by_severity.get(severity, 0) + 1
        else:
            passed += 1

    for rid, severity in active:
        fn = RULES.get(rid)
        if fn is None:
            # Règle seedée mais pas encore codée (lot 8).
            continue
        for i, subj in enumerate(subjects):
            ctx = RuleContext(
                db=db,
                rule_id=rid,
                subject=subj,
                subjects=subjects,
                index=i,
                now=now,
                vessel=vessel,
                leg=leg,
            )
            try:
                outcomes = await fn(ctx)
            except Exception as exc:  # une règle ne doit jamais casser le run
                snap = [tv.as_dict() for tv in ctx._consumed]
                _persist(
                    rid,
                    subj,
                    "fail",
                    "info",
                    f"Erreur technique dans la règle {rid} : {exc}",
                    {"error": repr(exc), "thresholds_used": snap} if snap else {"error": repr(exc)},
                )
                continue
            snapshot = [tv.as_dict() for tv in ctx._consumed]
            for oc in outcomes:
                if oc.result not in ("pass", "fail"):
                    continue
                if oc.result == "pass" and not persist_passes:
                    passed += 1
                    continue
                target = oc.subject if oc.subject is not None else subj
                details = dict(oc.details or {})
                if snapshot:
                    details.setdefault("thresholds_used", snapshot)
                # LOT 8 — la règle peut imposer la sévérité de ce verdict
                # (graduation Matrice) ; sinon défaut de la règle.
                applied = oc.severity or severity
                _persist(rid, target, oc.result, applied, oc.message, details or None)

    await db.flush()
    return RunSummary(
        run_id=run_id,
        total=passed + failed,
        passed=passed,
        failed=failed,
        by_severity=by_severity,
        results=results,
    )


# ════════════════════════════════════════════════════════════ Seed (dev/admin)


async def seed_reference_data(
    db: AsyncSession, *, updated_by: int | None = None
) -> dict[str, list[str]]:
    """Seed idempotent du référentiel de validation.

    N'insère que les lignes manquantes (rules / thresholds globaux /
    dashboard params). Utilisé au boot dev (``create_all`` sans migration)
    et par l'action d'init admin. Renvoie ce qui a été créé.
    """
    from app.models.validation import (
        DashboardParameter,
        ValidationRule,
        ValidationRuleThreshold,
    )

    created: dict[str, list[str]] = {"rules": [], "thresholds": [], "dashboard": []}

    existing_rules = set((await db.execute(select(ValidationRule.rule_id))).scalars().all())
    for rid, domain, desc, severity, scope, active in RULE_SEED:
        if rid in existing_rules:
            continue
        db.add(
            ValidationRule(
                rule_id=rid,
                domain=domain,
                description=desc,
                default_severity=severity,
                scope=scope,
                active=active,
            )
        )
        created["rules"].append(rid)

    existing_thr = set(
        (
            await db.execute(
                select(
                    ValidationRuleThreshold.rule_id,
                    ValidationRuleThreshold.vessel_id,
                    ValidationRuleThreshold.parameter_name,
                )
            )
        ).all()
    )
    for rid, param, value, unit, provisional, note in THRESHOLD_SEED:
        if (rid, None, param) in existing_thr:
            continue
        db.add(
            ValidationRuleThreshold(
                rule_id=rid,
                vessel_id=None,
                parameter_name=param,
                value=Decimal(value),
                unit=unit,
                provisional=provisional,
                note=note,
                updated_by=updated_by,
            )
        )
        created["thresholds"].append(f"{rid}:{param}={value}")

    existing_dash = set(
        (
            await db.execute(
                select(DashboardParameter.parameter_name, DashboardParameter.vessel_id)
            )
        ).all()
    )
    for param, value, unit in DASHBOARD_SEED:
        if (param, None) in existing_dash:
            continue
        db.add(
            DashboardParameter(
                parameter_name=param,
                vessel_id=None,
                value=Decimal(value),
                unit=unit,
                updated_by=updated_by,
            )
        )
        created["dashboard"].append(f"{param}={value}")

    if created["rules"] or created["thresholds"] or created["dashboard"]:
        await db.flush()
        invalidate_cache()
    return created


# ════════════════════════════════════════════════════════════ LOT 8 — catalogue
#
# Enregistrement des règles complètes (R03-R10, R14-R26, IR01-IR05) : leur
# module s'importe EN FIN de fichier pour peupler ``RULES`` via ``@rule`` sans
# cycle d'import (il ne consomme que des noms déjà définis ci-dessus :
# ``rule``, ``RuleContext``, ``CheckOutcome``, ``get_threshold``, helpers).
#
# RÉCONCILIATIONS de sémantique lot 2 → Matrice (documentées, cf. le catalogue) :
# - **R11** : la Matrice décrit « ROB annexes urée/eau douce manquants
#   (Warning) ». Le lot 2 a recentré R11 sur des *bornes de plausibilité*
#   paramétrées (conso ≤ seuil, ROB ≤ borne) — conservé tel quel (guard générique
#   utile, tests dépendants). Les annexes urée/eau douce ne sont pas portées par
#   le modèle ``nav_events`` (pas de colonne) → volet Matrice N/A sur ce modèle ;
#   le ROB principal est couvert par R06 (lot 8) et la conso par R08/R15.
# - **R13** : la Matrice décrit une *complétude de champs* (voilure/T°/tirants…,
#   Informatif). Le lot 2 a recentré R13 sur la *chronologie stricte* d'une
#   séquence (doublon/antériorité). Conservé ; le volet doublon/antériorité est
#   désormais porté rigoureusement par **IR01** (scope séquence). La complétude
#   reste couverte de fait par les présences R05/R06/R07.
from app.services import validation_rules_catalog as _catalog  # noqa: E402,F401
