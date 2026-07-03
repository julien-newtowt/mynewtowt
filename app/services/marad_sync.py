"""Synchronisation Marad → crew (LECTURE SEULE côté Marad).

Lit les données crew de Marad (``GET /api/Crewing``) et les réconcilie dans la
table ``crew_members`` de mynewtowt. **Read-only au sens Marad** : on ne modifie
JAMAIS Marad ; on n'écrit que dans notre propre base.

Principes de l'upsert (cf. docs/integrations/marad-crew-readonly.md) :
- **clé de réconciliation** : ``crew_members.marad_id`` = GUID Marad ;
- **idempotent** : un même GUID met à jour l'enregistrement existant ;
- **additif / non destructeur** : un champ n'est écrasé que si Marad fournit une
  valeur exploitable (jamais de NULL/placeholder qui effacerait une saisie ERP) ;
- **champs ERP préservés** : statut Schengen, visas, livret marin, passeport,
  ``is_active``, ``notes`` ne sont pas gérés par Marad → jamais touchés ici ;
- **champs sensibles ignorés volontairement** : ``bankAccount``, ``idNumber``,
  adresses postales, tailles de vêtements — non importés.

Schéma Marad ``/api/Crewing`` (confirmé) — champs utilisés :
``id`` (GUID), ``firstName``, ``lastName``, ``callName``, ``ranks`` (liste),
``nationality``, ``birthDate`` (ISO datetime), ``email``, ``mobilePhone``,
``phone``.

⚠️ La **casse des clés JSON varie selon le serveur Marad** : les hôtes par
tenant (``external02.marad.ms``…) sérialisent en PascalCase (``ID``,
``FirstName``, ``BirthDate``…) là où l'échantillon éditeur était en camelCase.
Tout accès aux champs passe donc par ``_field()`` (insensible à la casse) —
ne jamais utiliser ``rec.get("...")`` directement sur un record Marad.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crew import CrewMember, MaradCrewSchedule
from app.models.leg import Leg
from app.models.vessel import Vessel
from app.utils import marad

logger = logging.getLogger("marad")


def is_configured() -> bool:
    return marad.enabled()


def _records(payload: Any) -> list[dict]:
    """Normalise une réponse Marad en liste de dicts.

    Tolérant aux wrappers Marad usuels (``data``/``items``/…), aux noms
    spécifiques (``crewMembers``/``crewing``/``schedules``…), et au cas d'un
    dict enveloppe ne contenant qu'une seule liste.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in (
            "data",
            "items",
            "results",
            "value",
            "records",
            "crewmembers",
            "crewing",
            "crew",
            "schedules",
            "crewingschedule",
            "list",
            "rows",
        ):
            v = _field(payload, key)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
        # Enveloppe à une seule liste (clé inconnue) → on la prend.
        list_values = [v for v in payload.values() if isinstance(v, list)]
        if len(list_values) == 1:
            return [r for r in list_values[0] if isinstance(r, dict)]
        # Sinon, si le dict ressemble à un enregistrement (a un id), on le garde.
        if _field(payload, "id") is not None:
            return [payload]
        return []
    return []


def _field(rec: dict, key: str) -> Any:
    """Valeur du champ ``key``, **insensible à la casse** de la clé.

    Le serveur Marad sérialise en camelCase ou PascalCase selon sa version
    (``external02`` renvoie ``ID``/``FirstName``/``StartInfo``…) : on ne
    présume jamais de la casse. Les records sont petits (< 30 clés) — le
    parcours linéaire en cas de non-correspondance exacte est négligeable.
    """
    if key in rec:
        return rec[key]
    kl = key.lower()
    for k, v in rec.items():
        if isinstance(k, str) and k.lower() == kl:
            return v
    return None


def _clean(val: Any) -> str | None:
    """Chaîne nettoyée, ou None si vide / placeholder Swagger ("string")."""
    if not isinstance(val, str):
        return None
    s = val.strip()
    if not s or s.lower() == "string":
        return None
    return s


def _full_name(rec: dict) -> str | None:
    first = _clean(_field(rec, "firstName"))
    last = _clean(_field(rec, "lastName"))
    name = " ".join(p for p in (first, last) if p)
    return name or _clean(_field(rec, "callName"))


def _first_rank(rec: dict) -> str | None:
    ranks = _field(rec, "ranks")
    if isinstance(ranks, list):
        for r in ranks:
            c = _clean(r)
            if c:
                return c[:60]
    return None


def _nationality(rec: dict) -> str | None:
    """``nationality`` n'est conservé que si c'est un code ISO-2 (colonne CHAR(2))."""
    n = _clean(_field(rec, "nationality"))
    if n and len(n) == 2 and n.isalpha():
        return n.upper()
    return None


def _phone(rec: dict) -> str | None:
    p = _clean(_field(rec, "mobilePhone")) or _clean(_field(rec, "phone"))
    return p[:50] if p else None


def _email(rec: dict) -> str | None:
    e = _clean(_field(rec, "email"))
    return e[:255] if e and "@" in e else None


def _birth_date(rec: dict) -> date | None:
    raw = _clean(_field(rec, "birthDate"))
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _apply(member: CrewMember, rec: dict, *, creating: bool) -> None:
    """Écrit les champs issus de Marad sur ``member`` (additif, non destructeur)."""
    name = _full_name(rec)
    if name:
        member.full_name = name[:200]
    elif creating:
        member.full_name = "(sans nom)"

    rank = _first_rank(rec)
    if rank:
        member.role = rank
    elif creating:
        member.role = "marin"

    nat = _nationality(rec)
    if nat:
        member.nationality = nat

    dob = _birth_date(rec)
    if dob:
        member.date_of_birth = dob

    email = _email(rec)
    if email:
        member.email = email

    phone = _phone(rec)
    if phone:
        member.phone = phone


async def sync_crew(db: AsyncSession) -> dict:
    """Upsert idempotent du crew Marad dans ``crew_members`` (clé ``marad_id``).

    No-op propre si Marad n'est pas configuré. Renvoie un résumé
    ``{configured, fetched, created, updated, skipped, errors, note}``.
    """
    if not marad.enabled():
        return {
            "configured": False,
            "fetched": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "note": "MARAD_API_TOKEN non configuré — intégration inactive.",
        }

    payload = await marad.list_crew()
    records = _records(payload)

    created = updated = skipped = errors = 0
    for rec in records:
        marad_id = _clean(_field(rec, "id"))
        if not marad_id:
            skipped += 1  # enregistrement sans GUID → non réconciliable
            continue
        try:
            member = (
                await db.execute(select(CrewMember).where(CrewMember.marad_id == marad_id))
            ).scalar_one_or_none()
            if member is None:
                member = CrewMember(marad_id=marad_id, full_name="(sans nom)", role="marin")
                _apply(member, rec, creating=True)
                db.add(member)
                created += 1
            else:
                _apply(member, rec, creating=False)
                updated += 1
        except Exception:  # un enregistrement fautif ne stoppe pas le batch
            logger.exception("Marad sync: échec sur l'enregistrement %s", marad_id)
            errors += 1

    await db.flush()  # commit géré par la dependency get_db
    result = {
        "configured": True,
        "fetched": len(records),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "note": "Sync read-only Marad → crew_members (clé marad_id, non destructeur).",
    }
    logger.info("Marad sync: %s", result)
    return result


# ─────────────────── Plannings d'embarquement (CrewingSchedule) ───────────────────
# Schéma /api/CrewingSchedule CONFIRMÉ (échantillon éditeur 2026-06-18) :
#   id (GUID) ; crewMember{ id (GUID), firstName, lastName, … } ;
#   rank ; status ; vessel (NOM) ;
#   startInfo{ dateTime, date, time, remarks, port } ; endInfo{ … }.
# Pas de code « voyage » explicite → le leg est réconcilié par **navire + fenêtre
# de dates** (un « voyage » Marad = un leg). marad_voyage_ref = route POL→POD.

_SCHED_RANK_KEYS = ("rank", "rankName", "position")
_SCHED_STATUS_KEYS = ("status", "state")


def _pick(rec: dict, keys: tuple[str, ...]) -> str | None:
    """1re valeur exploitable parmi plusieurs clés candidates (placeholders ignorés)."""
    for k in keys:
        c = _clean(_field(rec, k))
        if c:
            return c
    return None


def _subdict(rec: dict, key: str) -> dict:
    """Sous-objet imbriqué (crewMember / startInfo / endInfo) ou {} si absent."""
    v = _field(rec, key)
    return v if isinstance(v, dict) else {}


def _parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    """Normalise en UTC naïf (compat SQLite, qui perd la tzinfo en stockage)."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _resolve_leg(
    vessel_id: int | None,
    start_dt: datetime | None,
    legs_by_vessel: dict[int, list[tuple[int, datetime | None, datetime | None]]],
) -> int | None:
    """Leg dont la fenêtre [début, fin] contient l'embarquement (un voyage = un leg)."""
    if not vessel_id or start_dt is None:
        return None
    s = _to_naive_utc(start_dt)
    for leg_id, lo, hi in legs_by_vessel.get(vessel_id, ()):
        if lo is not None and hi is not None and lo <= s <= hi:
            return leg_id
    return None


async def sync_schedules(db: AsyncSession) -> dict:
    """Upsert idempotent des plannings Marad dans ``marad_crew_schedules``.

    Lecture seule côté Marad. Réconcilie :
    - le marin via ``CrewMember.marad_id`` (objet imbriqué ``crewMember.id``) ;
    - le navire via son **nom** (champ ``vessel``) — repli ``MARAD_VESSEL_MAP`` ;
    - le **leg** via navire + fenêtre de dates (un « voyage » Marad = un leg).
    No-op propre si Marad n'est pas configuré.
    """
    if not marad.enabled():
        return {
            "configured": False,
            "fetched": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "note": "MARAD_API_TOKEN non configuré — intégration inactive.",
        }

    payload = await marad.list_schedules()
    records = _records(payload)

    # Index de réconciliation (une seule requête chacun).
    vmap = marad.vessel_map()  # repli {marad_vessel_id|nom: vessel_id (str)}
    crew_rows = (
        await db.execute(
            select(CrewMember.id, CrewMember.marad_id).where(CrewMember.marad_id.is_not(None))
        )
    ).all()
    crew_by_marad = {marad_id: cid for cid, marad_id in crew_rows}
    # Marad identifie un navire par {number, name} (cf. /api/vessels/getVessels) ;
    # le champ `vessel` du schedule peut porter l'un ou l'autre → on indexe nos
    # navires par nom ET par code (le nom prime en cas de collision).
    vessel_rows = (await db.execute(select(Vessel.id, Vessel.name, Vessel.code))).all()
    vessel_by_key: dict[str, int] = {}
    for vid, name, _code in vessel_rows:
        if name:
            vessel_by_key[name.strip().upper()] = vid
    for vid, _name, code in vessel_rows:
        if code:
            vessel_by_key.setdefault(code.strip().upper(), vid)
    # Legs groupés par navire avec leur fenêtre [atd|etd, ata|eta] (UTC naïf).
    leg_rows = (
        await db.execute(select(Leg.id, Leg.vessel_id, Leg.etd, Leg.eta, Leg.atd, Leg.ata))
    ).all()
    legs_by_vessel: dict[int, list[tuple[int, datetime | None, datetime | None]]] = {}
    for lid, vid, etd, eta, atd, ata in leg_rows:
        legs_by_vessel.setdefault(vid, []).append(
            (lid, _to_naive_utc(atd or etd), _to_naive_utc(ata or eta))
        )

    created = updated = skipped = errors = 0
    for rec in records:
        sched_id = _clean(_field(rec, "id"))
        if not sched_id:
            skipped += 1
            continue
        try:
            crew_guid = _clean(_field(_subdict(rec, "crewMember"), "id"))
            vessel_str = _clean(_field(rec, "vessel"))
            vessel_id = None
            if vessel_str:
                vessel_id = vessel_by_key.get(vessel_str.strip().upper())
                if vessel_id is None and vessel_str in vmap:
                    try:
                        vessel_id = int(vmap[vessel_str])
                    except ValueError:
                        vessel_id = None

            si, ei = _subdict(rec, "startInfo"), _subdict(rec, "endInfo")
            start_dt = _parse_dt(_clean(_field(si, "dateTime")))
            end_dt = _parse_dt(_clean(_field(ei, "dateTime")))
            start_port, end_port = _clean(_field(si, "port")), _clean(_field(ei, "port"))
            voyage_ref = (
                f"{start_port or '?'} → {end_port or '?'}"[:80]
                if (start_port or end_port)
                else None
            )

            row = (
                await db.execute(
                    select(MaradCrewSchedule).where(MaradCrewSchedule.marad_schedule_id == sched_id)
                )
            ).scalar_one_or_none()
            if row is None:
                row = MaradCrewSchedule(marad_schedule_id=sched_id)
                db.add(row)
                created += 1
            else:
                updated += 1
            row.crew_member_id = crew_by_marad.get(crew_guid) if crew_guid else None
            row.marad_crew_id = crew_guid
            row.vessel_id = vessel_id
            row.marad_vessel_name = vessel_str
            row.marad_voyage_ref = voyage_ref
            row.leg_id = _resolve_leg(vessel_id, start_dt, legs_by_vessel)
            rank = _pick(rec, _SCHED_RANK_KEYS)
            row.rank_label = rank[:80] if rank else None
            row.start_date = start_dt.date() if start_dt else None
            row.end_date = end_dt.date() if end_dt else None
            row.status = _pick(rec, _SCHED_STATUS_KEYS)
        except Exception:  # un schedule fautif ne stoppe pas le batch
            logger.exception("Marad schedules: échec sur le schedule %s", sched_id)
            errors += 1

    await db.flush()
    result = {
        "configured": True,
        "fetched": len(records),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "note": "Sync read-only Marad → marad_crew_schedules (voyage↔leg, navire, marin).",
    }
    logger.info("Marad schedules sync: %s", result)
    return result


async def sync_all(db: AsyncSession) -> dict:
    """Synchronise crew + plannings (CrewingSchedule) en un appel.

    Utilisé par le bouton « Synchroniser Marad » (/crew) et le cron
    ``POST /api/marad/refresh``. Renvoie un résumé à plat pour l'UI + le détail.
    """
    # Découvre d'abord le schéma d'auth via un endpoint à quota large
    # (getVessels, 15 req/min) pour ne pas gâcher le quota de /api/Crewing
    # (1 req/min) en essayant plusieurs schémas dessus.
    await marad.prime_auth()
    crew = await sync_crew(db)
    sched = await sync_schedules(db)

    # Diagnostic : si l'API est configurée mais que RIEN n'a été récupéré,
    # on sonde la connectivité pour expliquer le « rien ne remonte » plutôt
    # que de laisser un silencieux 0/0.
    diagnostic: str | None = None
    if crew["configured"] and (crew.get("fetched", 0) + sched.get("fetched", 0)) == 0:
        try:
            diag = await marad.diagnose()
            base = diag.get("base_url")
            cls = diag.get("classification")
            if cls == "unreachable":
                diagnostic = (
                    f"Hôte Marad injoignable ({base}) — aucune réponse HTTP. "
                    "Causes probables : URL incorrecte, DNS, ou pare-feu sortant "
                    "bloquant l'accès depuis le serveur. Vérifiez MARAD_BASE_URL et "
                    "que le serveur peut joindre ce domaine."
                )
            elif cls == "auth_refused":
                tried = ", ".join(diag.get("tried_strategies") or []) or "—"
                tok = (
                    f"token chargé ({diag.get('token_preview')}, {diag.get('token_len')} car.)"
                    if diag.get("token_set")
                    else "AUCUN token chargé (MARAD_API_TOKEN vide !)"
                )
                server = diag.get("auth_error_body")
                server_part = f" Réponse serveur : « {server} »." if server else ""
                diagnostic = (
                    "Hôte Marad joignable mais authentification refusée (401/403). "
                    f"Schémas testés : {tried}. {tok}.{server_part} "
                    "→ Si « query:apikey » figure ci-dessus, le correctif est déployé "
                    "et le token ne correspond pas à celui de votre intégration "
                    "(vérifiez MARAD_API_TOKEN). Sinon, le déploiement n'est pas à jour."
                )
            elif cls == "wrong_path":
                diagnostic = (
                    f"Hôte joignable mais endpoint introuvable (404) sur {base}. "
                    "Le préfixe de l'API a peut-être changé — vérifiez MARAD_BASE_URL "
                    "(ex. avec/sans suffixe de version)."
                )
            elif cls == "rate_limited":
                retry = diag.get("retry_after")
                body = diag.get("rate_limit_body")
                retry_part = (
                    f" Le serveur demande de réessayer dans {retry} s (Retry-After)."
                    if retry
                    else " Aucun en-tête Retry-After renvoyé."
                )
                # Un 429 SANS Retry-After et avec un corps parlant d'auth trahit
                # souvent un token refusé (429 anti-bruteforce) et non un vrai
                # quota — on le signale explicitement.
                looks_auth = bool(body) and any(
                    kw in body.lower()
                    for kw in ("invalid", "unauthor", "forbidden", "api key", "apikey", "token")
                )
                body_part = f" Réponse serveur : « {body} »." if body else ""
                suspect_part = (
                    " ⚠ Ce 429 ressemble à un refus d'authentification déguisé "
                    "(message d'auth, pas de Retry-After) : vérifiez d'abord "
                    "MARAD_API_TOKEN et le header d'auth AVANT de conclure au quota."
                    if looks_auth and not retry
                    else ""
                )
                diagnostic = (
                    "Marad a renvoyé 429 sur getVessels (endpoint à 15 req/min, "
                    "donc un 429 dès le 1er appel est anormal pour un simple quota)."
                    + retry_part
                    + body_part
                    + suspect_part
                    + " Les endpoints crew (/api/Crewing, /api/CrewingSchedule) "
                    "restent à 1 req/min : espacez les tests d'au moins une minute. "
                    "Si la clé API est partagée avec d'autres consommateurs "
                    "(ex. rapports Power BI), leurs refresh consomment le même "
                    "quota — demandez une clé dédiée à l'éditeur."
                )
            elif cls == "ok":
                # 0 navire alors que l'auth passe = presque toujours un MAUVAIS
                # HÔTE : les tenants Marasoft vivent sur des serveurs numérotés
                # (external.marad.ms, external02.marad.ms, …) et une clé valide
                # peut authentifier sur un serveur où le tenant est vide.
                host_hint = (
                    " ⚠ 0 navire visible : la clé authentifie mais ce serveur ne "
                    "porte pas votre tenant — vérifiez que MARAD_BASE_URL pointe "
                    "sur l'hôte de VOTRE tenant (serveurs numérotés : "
                    "external.marad.ms, external02.marad.ms, …)."
                    if not diag.get("vessels_count")
                    else ""
                )
                diagnostic = (
                    "API Marad joignable et authentifiée "
                    f"(navires visibles : {diag.get('vessels_count')}), mais aucun "
                    "marin/planning retourné : compte/tenant vide, filtre delta, ou "
                    "schéma de réponse inattendu sur /api/Crewing." + host_hint
                )
            else:
                diagnostic = (
                    f"Synchro Marad sans données ({base}, classification : {cls}). "
                    "Vérifiez token, header d'auth et URL ; quota possible "
                    "(GET /api/Crewing = 1 req/min)."
                )
            logger.info("marad diagnose: %s", diag)
        except Exception:  # le diagnostic ne doit jamais faire échouer la synchro
            logger.warning("marad diagnose failed", exc_info=True)

    return {
        "configured": crew["configured"],
        "crew_created": crew.get("created", 0),
        "crew_updated": crew.get("updated", 0),
        "crew_fetched": crew.get("fetched", 0),
        "sched_created": sched.get("created", 0),
        "sched_updated": sched.get("updated", 0),
        "sched_fetched": sched.get("fetched", 0),
        "errors": crew.get("errors", 0) + sched.get("errors", 0),
        "diagnostic": diagnostic,
        "crew": crew,
        "schedules": sched,
    }
