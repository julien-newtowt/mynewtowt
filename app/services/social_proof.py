"""Preuve sociale — compteurs réels, mentions presse, témoignages (opt-in).

Trois natures de preuve, trois régimes :

1. **Compteurs cumulés** : calculés en direct depuis la base (bookings
   embarqués, certificats Anemos, traversées réalisées) — jamais des chiffres
   statiques. Cache module 10 min. La landing ne montre le bandeau que si au
   moins un compteur est non nul (pas de « 0 palettes » en vitrine).

2. **Mentions presse** : liste curatée de couvertures *publiées* (fait public,
   aucun accord requis) — nom du média + lien vers l'article.

3. **Témoignages et logos clients** : listes VIDES par défaut. Doctrine
   (cf. docs/strategy/AUDIT_CLAIMS_ECGT.md §2 et rapport P5) : aucune preuve
   sociale nominative sans contenu fourni ET accord écrit du client
   (``consent_ref`` = référence du mail/contrat d'accord). Les sections de la
   landing ne s'affichent que si du contenu existe — activer = remplir ces
   listes, rien d'autre à câbler.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.anemos_certificate import AnemosCertificate
from app.models.booking import Booking
from app.models.leg import Leg

# Statuts où la marchandise a réellement été embarquée.
_LOADED_STATUSES = ("loaded", "at_sea", "discharged", "delivered")

_CACHE_TTL_SECONDS = 600.0


@dataclass(frozen=True)
class SocialCounters:
    """Compteurs cumulés d'exploitation (source : base opérationnelle)."""

    pallets: int
    co2_avoided_kg: int
    crossings: int

    @property
    def has_content(self) -> bool:
        return self.pallets > 0 or self.co2_avoided_kg > 0 or self.crossings > 0

    @property
    def pallets_str(self) -> str:
        return _thousands(self.pallets)

    @property
    def co2_str(self) -> str:
        """CO₂ évité lisible : tonnes dès 1 000 kg, kilogrammes sinon."""
        if self.co2_avoided_kg >= 1000:
            return f"{_thousands(round(self.co2_avoided_kg / 1000))} t"
        return f"{_thousands(self.co2_avoided_kg)} kg"

    @property
    def crossings_str(self) -> str:
        return _thousands(self.crossings)


def _thousands(value: int) -> str:
    """Groupement des milliers à l'espace fine (lisible dans nos 5 langues)."""
    return f"{value:,}".replace(",", " ")


_counters_cache: SocialCounters | None = None
_counters_loaded_at: float = 0.0


def invalidate_counters_cache() -> None:
    """Force le recalcul au prochain ``counters()`` (tests, admin)."""
    global _counters_cache, _counters_loaded_at
    _counters_cache = None
    _counters_loaded_at = 0.0


async def counters(db: AsyncSession) -> SocialCounters:
    """Compteurs cumulés — cache module 10 min, tolérant aux erreurs DB."""
    global _counters_cache, _counters_loaded_at
    now = time.monotonic()
    if _counters_cache is not None and (now - _counters_loaded_at) < _CACHE_TTL_SECONDS:
        return _counters_cache

    pallets = 0
    co2_kg = 0
    crossings = 0
    try:
        pallets = int(
            (
                await db.execute(
                    select(func.coalesce(func.sum(Booking.total_palettes), 0)).where(
                        Booking.status.in_(_LOADED_STATUSES)
                    )
                )
            ).scalar_one()
        )
        co2_kg = int(
            (
                await db.execute(
                    select(func.coalesce(func.sum(AnemosCertificate.co2_avoided_kg), 0))
                )
            ).scalar_one()
        )
        crossings = int(
            (await db.execute(select(func.count(Leg.id)).where(Leg.ata.is_not(None)))).scalar_one()
        )
    except Exception:  # pragma: no cover — best-effort, la vitrine ne casse pas
        pass

    _counters_cache = SocialCounters(pallets=pallets, co2_avoided_kg=co2_kg, crossings=crossings)
    _counters_loaded_at = now
    return _counters_cache


# ── Mentions presse (couvertures publiées — fait public, liens sortants) ────
PRESS_MENTIONS: tuple[dict, ...] = (
    {
        "outlet": "Le Journal de la Marine Marchande",
        "title": "TOWT échappe à la disparition avec la reprise portée par le Crédit Mutuel",
        "url": "https://www.journalmarinemarchande.fr/shipping/2026/05/towt-echappe-a-la-disparition-avec-la-reprise-portee-par-le-credit-mutuel/",
        "year": 2026,
    },
    {
        "outlet": "France 3 Normandie",
        "title": "L'entreprise TOWT sauvée : qui sont les repreneurs du pionnier français du cargo à voile",
        "url": "https://france3-regions.franceinfo.fr/normandie/seine-maritime/havre/l-entreprise-towt-sauvee-qui-sont-les-repreneurs-du-pionnier-francais-du-cargo-a-voile-3347188.html",
        "year": 2026,
    },
    {
        "outlet": "Supply Chain Magazine",
        "title": "NewTowt reprend la mer, cap sur le Brésil",
        "url": "https://supplychainmagazine.fr/newtowt-reprend-la-mer-cap-sur-le-bresil/",
        "year": 2026,
    },
    {
        "outlet": "Le Figaro Nautisme",
        "title": "Anemos et Artemis : le café le plus décarboné du monde arrive à la voile",
        "url": "https://figaronautisme.meteoconsult.fr/actus-nautisme-flash/2026-01-04/84440-anemos-et-artemis-le-cafe-le-plus-decarbone-du-monde-arrive-a-la-voile",
        "year": 2026,
    },
    {
        "outlet": "Voxlog",
        "title": "Towt maintient le cap et devient Newtowt",
        "url": "https://www.voxlog.fr/actualite/10898/towt-maintient-le-cap-et-devient-newtowt",
        "year": 2026,
    },
    {
        "outlet": "Places du Café",
        "title": "NewTowt, un second souffle pour le transport de café à la voile",
        "url": "https://www.placesducafe.com/professionnel/newtowt-un-second-souffle-pour-le-transport-de-cafe-a-la-voile-258",
        "year": 2026,
    },
)

# ── Témoignages clients — VIDE tant que contenu + accord écrit non fournis ──
# Forme attendue :
# {
#     "quote": "Texte exact validé par le client.",
#     "author": "Prénom Nom",
#     "role": "Directeur général",
#     "company": "Société",
#     "consent_ref": "mail du 2026-07-15 / avenant n°…",  # OBLIGATOIRE
# }
TESTIMONIALS: tuple[dict, ...] = ()

# ── Logos clients — VIDE tant que fichier + accord écrit non fournis ────────
# Déposer le fichier dans app/static/img/clients/ puis référencer ici :
# {"name": "Société", "file": "img/clients/societe.png", "consent_ref": "…"}
CLIENT_LOGOS: tuple[dict, ...] = ()
