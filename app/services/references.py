"""Génération de références métier uniques (devis, réservations…).

Deux défauts corrigés ici (constats sécurité E-1 / M-6) :

* **Entropie.** Des suffixes de 16 à 24 bits rendaient les références publiques
  énumérables (une référence de devis donne accès aux prix et aux coordonnées du
  demandeur) et, par le paradoxe des anniversaires, rendaient une collision
  probable dès quelques centaines de références sur l'année.
* **Collision non gérée.** Aucun appelant ne rattrapait l'``IntegrityError`` de la
  contrainte d'unicité : une collision remontait en HTTP 500 au pire moment du
  tunnel (validation d'une réservation).

``unique_reference`` interroge la base avant d'émettre et retente sur collision.
Ce n'est pas une garantie absolue contre une course entre deux workers — la
contrainte d'unicité en base reste l'autorité — mais elle rend l'échec
suffisamment improbable pour ne pas dégrader le parcours.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_MAX_ATTEMPTS = 8


def random_suffix(n_bytes: int) -> str:
    """Suffixe hexadécimal majuscule de ``n_bytes`` octets (2 caractères par octet)."""
    return secrets.token_hex(n_bytes).upper()


async def unique_reference(
    db: AsyncSession,
    *,
    column,
    factory: Callable[[], str],
    max_attempts: int = _MAX_ATTEMPTS,
) -> str:
    """Renvoie une référence produite par ``factory`` absente de la base.

    ``column`` est la colonne portant la contrainte d'unicité (ex.
    ``Quote.reference``). Après ``max_attempts`` tirages tous déjà pris, on
    renvoie le dernier candidat : la contrainte en base tranchera plutôt que de
    boucler indéfiniment — cas assez improbable pour valoir une erreur visible.
    """
    candidate = factory()
    for _ in range(max_attempts):
        taken = (
            await db.execute(select(column).where(column == candidate).limit(1))
        ).scalar_one_or_none()
        if taken is None:
            return candidate
        candidate = factory()
    return candidate
