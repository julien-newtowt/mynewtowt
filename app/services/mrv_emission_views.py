"""MRV — restitution des émissions par voyage et par escale (couche 3).

Deux vues de **lecture seule** sur ``voyage_emission_summaries``, la
matérialisation par leg du grand livre (``services.emission_ledger``) :

- **Voyages** — le trajet déclaré, de l'événement *Departure* à l'événement
  *Arrival* du même leg.
- **Escales** — le séjour au port qui **suit** l'arrivée d'un leg, jusqu'au
  *Departure* suivant du même navire (``emission_ledger._escale_consumption``,
  G12). Ce séjour peut appartenir au leg suivant : la ligne est donc rattachée
  au leg qui **arrive**, et le port concerné est son POD.

🔴 **Deux assiettes disjointes, jamais additionnées en silence.**

``co2_t``/``co2eq_t`` portent le **trajet** (assiette : consommation hors
mouillage). ``co2_escale_t``/``co2eq_escale_t`` portent l'**escale** qui suit
l'arrivée. Elles ne se recouvrent pas, et l'escale d'un voyage peut s'étendre
sur la fenêtre du voyage suivant : tout total « trajet + escale » doit
l'annoncer. Ces vues ne l'additionnent jamais.

Les deux grandeurs sont calculées par ``emission_ledger``, au même facteur et
par la même primitive — la règle d'or veut que l'unique multiplication
consommation × facteur d'émission vive là (sentinelle
``tests/regression/test_factor_whitelist.py``). Ces vues **lisent**, elles ne
calculent rien.

⚠️ **Trou restant, documenté** : la consommation au **mouillage**
(``conso_mouillage_t``) est exclue de l'assiette du trajet et ne reçoit
toujours aucune émission — cas symétrique de celui de l'escale, non tranché.
La vue voyage l'affiche donc en consommation, en le disant.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leg import Leg
from app.models.port import Port
from app.models.vessel import Vessel
from app.models.voyage_emission_summary import VoyageEmissionSummary

#: Plafond de listing, aligné sur ``/mrv/voyages`` (40) — les deux écrans se
#: lisent côte à côte, un plafond différent rendrait la comparaison trompeuse.
_LIMIT = 40


@dataclass(frozen=True)
class LegEmissionRow:
    leg: Leg
    vessel: Vessel | None
    summary: VoyageEmissionSummary | None
    #: Port d'arrivée du leg — c'est là que se tient l'escale qui suit.
    arrival_port: Port | None = None

    # ── Voyage (assiette hors mouillage) ─────────────────────────────────
    @property
    def conso_voyage_t(self) -> Decimal | None:
        return self.summary.conso_hors_mouillage_t if self.summary else None

    @property
    def co2_t(self) -> Decimal | None:
        return self.summary.co2_t if self.summary else None

    @property
    def co2eq_t(self) -> Decimal | None:
        return self.summary.co2eq_t if self.summary else None

    @property
    def distance_nm(self) -> Decimal | None:
        return self.summary.distance_nm if self.summary else None

    # ── Escale (assiette disjointe du trajet — cf. docstring du module) ──
    @property
    def conso_escale_t(self) -> Decimal | None:
        return self.summary.conso_escale_t if self.summary else None

    @property
    def co2_escale_t(self) -> Decimal | None:
        return self.summary.co2_escale_t if self.summary else None

    @property
    def co2eq_escale_t(self) -> Decimal | None:
        return self.summary.co2eq_escale_t if self.summary else None

    @property
    def conso_mouillage_t(self) -> Decimal | None:
        return self.summary.conso_mouillage_t if self.summary else None

    @property
    def has_summary(self) -> bool:
        """Un leg sans résumé n'a pas encore d'événement finalisé exploitable.

        Distinguer « pas encore calculé » de « calculé à zéro » : les deux
        s'affichent différemment, sinon un voyage non déclaré ressemblerait à un
        voyage sans émission.
        """
        return self.summary is not None


async def _rows(
    db: AsyncSession, *, vessel_id: int | None, only_with_escale: bool
) -> list[LegEmissionRow]:
    stmt = select(Leg).order_by(Leg.etd.desc()).limit(_LIMIT)
    if vessel_id is not None:
        stmt = stmt.where(Leg.vessel_id == vessel_id)
    legs = list((await db.execute(stmt)).scalars().all())
    if not legs:
        return []

    summaries = {
        s.leg_id: s
        for s in (
            await db.execute(
                select(VoyageEmissionSummary).where(
                    VoyageEmissionSummary.leg_id.in_([leg.id for leg in legs])
                )
            )
        )
        .scalars()
        .all()
    }
    vessel_ids = {leg.vessel_id for leg in legs if leg.vessel_id is not None}
    vessels = {
        v.id: v
        for v in (await db.execute(select(Vessel).where(Vessel.id.in_(vessel_ids)))).scalars().all()
    }

    port_ids = {leg.arrival_port_id for leg in legs if leg.arrival_port_id is not None}
    ports = {
        p.id: p
        for p in (await db.execute(select(Port).where(Port.id.in_(port_ids)))).scalars().all()
    }

    rows = [
        LegEmissionRow(
            leg=leg,
            vessel=vessels.get(leg.vessel_id),
            summary=summaries.get(leg.id),
            arrival_port=ports.get(leg.arrival_port_id),
        )
        for leg in legs
    ]
    if only_with_escale:
        # Une escale n'existe que si le voyage est arrivé : sans conso d'escale,
        # la ligne n'aurait rien à dire (cf. G12 — `None` tant que non arrivé).
        rows = [r for r in rows if r.conso_escale_t is not None]
    return rows


async def voyage_emissions(
    db: AsyncSession, *, vessel_id: int | None = None
) -> list[LegEmissionRow]:
    """Émissions du trajet (Departure → Arrival) par voyage."""
    return await _rows(db, vessel_id=vessel_id, only_with_escale=False)


async def port_emissions(db: AsyncSession, *, vessel_id: int | None = None) -> list[LegEmissionRow]:
    """Séjour au port qui suit l'arrivée de chaque voyage.

    Consommation seule — l'émission d'escale n'est pas calculée par le grand
    livre (cf. docstring du module). Ne renvoie que les voyages effectivement
    arrivés.
    """
    return await _rows(db, vessel_id=vessel_id, only_with_escale=True)


async def vessels_with_summaries(db: AsyncSession) -> list[Vessel]:
    """Navires ayant au moins un résumé — alimente le filtre."""
    stmt = (
        select(Vessel)
        .where(
            Vessel.id.in_(
                select(Leg.vessel_id).join(
                    VoyageEmissionSummary, Leg.id == VoyageEmissionSummary.leg_id
                )
            )
        )
        .order_by(Vessel.code)
    )
    return list((await db.execute(stmt)).scalars().all())
