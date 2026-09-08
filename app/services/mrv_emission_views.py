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

🔴 **Le mouillage est une TROISIÈME assiette, hors périmètre MRV.**

Constat métier du 2026-09-04 : le mouillage n'appartient pas au périmètre MRV.
Sa consommation est donc exclue de l'assiette du trajet par construction, ce
qui est correct au regard du règlement — mais laissait du carburant réellement
brûlé sans émission connue. ``co2_mouillage_t`` comble ce manque **pour
l'analyse interne uniquement**.

Conséquence sur cette couche de restitution, et c'est l'invariant à ne pas
casser : le périmètre MRV (trajet + escale) est le **défaut**, le mouillage est
**opt-in** (``include_anchoring``), et tout total qui l'inclut est étiqueté
hors MRV. Les additionner par défaut gonflerait un chiffre réglementaire.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
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

    # ── Mouillage — 🔴 hors périmètre MRV, jamais dans un total par défaut ──
    @property
    def conso_mouillage_t(self) -> Decimal | None:
        return self.summary.conso_mouillage_t if self.summary else None

    @property
    def co2_mouillage_t(self) -> Decimal | None:
        return self.summary.co2_mouillage_t if self.summary else None

    @property
    def co2eq_mouillage_t(self) -> Decimal | None:
        return self.summary.co2eq_mouillage_t if self.summary else None

    @property
    def co2_with_anchoring_t(self) -> Decimal | None:
        """Trajet + mouillage — **hors périmètre MRV**, à n'afficher qu'en opt-in.

        ``None`` dès qu'un des deux termes manque : un total partiel qui
        passerait pour complet serait pire que pas de total.

        🔴 **Un mouillage à ``None`` n'est pas un mouillage nul.** La première
        version le traitait comme zéro, en affirmant « le navire n'a pas
        mouillé ». C'était faux, et la distinction est nette dans le grand
        livre :

        - source ``events`` → ``conso_mouillage`` est une **somme
          d'intervalles**, donc ``0`` quand le navire n'a pas mouillé ;
        - source ``legacy_noon`` (et archives TOWT) → ``None``, faute de toute
          granularité d'intervalle : le mouillage est **inconnu**.

        Sommer un inconnu comme un zéro produisait donc un total en gras
        présenté comme « trajet + mouillage » alors que la cellule mouillage de
        la même ligne affichait un tiret. Zéro reste additionné — c'est une
        mesure ; ``None`` interdit le total.
        """
        if self.co2_t is None or self.co2_mouillage_t is None:
            return None
        return self.co2_t + self.co2_mouillage_t

    @property
    def has_summary(self) -> bool:
        """Un leg sans résumé n'a pas encore d'événement finalisé exploitable.

        Distinguer « pas encore calculé » de « calculé à zéro » : les deux
        s'affichent différemment, sinon un voyage non déclaré ressemblerait à un
        voyage sans émission.
        """
        return self.summary is not None


async def _rows(
    db: AsyncSession,
    *,
    vessel_id: int | None,
    only_with_escale: bool,
    now: datetime | None = None,
) -> list[LegEmissionRow]:
    stmt = select(Leg)
    if vessel_id is not None:
        stmt = stmt.where(Leg.vessel_id == vessel_id)

    # 🔴 Une restitution ne regarde que le passé — mesuré sur le RÉEL.
    #
    # Sans cette borne, une séquence planifiée à l'avance remplissait les 40
    # places avec des voyages FUTURS : « non calculé » partout, et tous les
    # voyages porteurs de vraies émissions repoussés hors de la page.
    #
    # ⚠️ La borne et le tri se font sur le **départ effectif**
    # (`coalesce(atd, etd)`), jamais sur l'ETD seule : `declare_departure` ne
    # réécrit pas l'ETD, donc un voyage parti en avance (ATD 09-02, ETD 10-01)
    # aurait été exclu des deux écrans jusqu'en octobre malgré des émissions
    # réelles. C'est la convention `planning.effective_etd` du projet — tout
    # calcul « où en est le voyage » lui passe par là.
    effective_etd = func.coalesce(Leg.atd, Leg.etd)
    stmt = stmt.where(effective_etd <= (now or datetime.now(UTC)))

    if only_with_escale:
        # 🔴 Le filtre doit précéder le PLAFOND, pas le suivre.
        #
        # Les legs sont triés par ETD décroissant : une séquence planifiée à
        # l'avance remplit les 40 places avec des voyages FUTURS, sans escale.
        # Filtrer après le plafond rendait alors `/mrv/emissions/port` vide
        # alors que tous les voyages arrivés ont une escale à montrer.
        #
        # Une escale n'existe que si le voyage est arrivé ET que le départ
        # suivant est finalisé (G12) : `conso_escale_t` non nul est donc le
        # critère exact, et il est exprimable en SQL.
        stmt = stmt.join(VoyageEmissionSummary, VoyageEmissionSummary.leg_id == Leg.id).where(
            VoyageEmissionSummary.conso_escale_t.is_not(None)
        )
    stmt = stmt.order_by(effective_etd.desc()).limit(_LIMIT)
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
    return rows


async def voyage_emissions(
    db: AsyncSession, *, vessel_id: int | None = None, now: datetime | None = None
) -> list[LegEmissionRow]:
    """Émissions du trajet (Departure → Arrival) par voyage."""
    return await _rows(db, vessel_id=vessel_id, only_with_escale=False, now=now)


async def port_emissions(
    db: AsyncSession, *, vessel_id: int | None = None, now: datetime | None = None
) -> list[LegEmissionRow]:
    """Séjour au port qui suit l'arrivée de chaque voyage.

    Consommation **et** émission d'escale, toutes deux issues du grand livre
    (``conso_escale_t``, ``co2_escale_t`` — cf. docstring du module). Assiette
    disjointe de celle du trajet, jamais additionnée ici.

    Ne renvoie que les escales réellement closes : ``conso_escale_t`` non nul,
    ce qui suppose le voyage arrivé **et** le départ suivant finalisé (G12).
    """
    return await _rows(db, vessel_id=vessel_id, only_with_escale=True, now=now)


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
