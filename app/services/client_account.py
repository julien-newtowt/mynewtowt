"""Service de gestion des comptes clients (création / lookup).

Centralise la création d'un :class:`ClientAccount` pour qu'elle soit partagée
entre l'inscription explicite (`/me/register`) et l'**autocréation** depuis le
wizard de réservation (compte créé à la validation, sans page d'inscription
intercalée — cf. fiche /devis + wizard §2).

Politique mot de passe : ≥ 12 caractères (alignée sur l'existant).
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.models.client_account import ClientAccount

logger = logging.getLogger("client_account")

MIN_PASSWORD_LENGTH = 12


class AccountError(Exception):
    """Erreur métier de création de compte (validation)."""


class EmailAlreadyExists(AccountError):
    """Un compte existe déjà avec cet email."""


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


async def find_by_email(db: AsyncSession, email: str) -> ClientAccount | None:
    """Recherche un compte par email (insensible à la casse)."""
    clean = normalize_email(email)
    if not clean:
        return None
    return (
        await db.execute(select(ClientAccount).where(func.lower(ClientAccount.email) == clean))
    ).scalar_one_or_none()


async def create_account(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    company_name: str,
    contact_name: str | None = None,
    country: str | None = None,
    language: str = "fr",
) -> ClientAccount:
    """Crée un compte client vérifié et le relie au client commercial (best-effort).

    Lève :class:`EmailAlreadyExists` si l'email est déjà pris, ou
    :class:`AccountError` si le mot de passe est trop court / champs manquants.
    """
    clean_email = normalize_email(email)
    if not clean_email:
        raise AccountError("Email requis.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AccountError(
            f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères."
        )
    if not (company_name or "").strip():
        raise AccountError("Société requise.")

    existing = await find_by_email(db, clean_email)
    if existing is not None:
        raise EmailAlreadyExists(clean_email)

    account = ClientAccount(
        email=clean_email,
        hashed_password=hash_password(password),
        company_name=company_name.strip(),
        contact_name=(contact_name or "").strip() or None,
        country=((country or "").strip().upper() or None),
        language=language or "fr",
        # V3.0 : vérification instantanée (la vérification par email-lien est un
        # durcissement optionnel — cf. fiche §8 point 3).
        is_verified=True,
        segment="occasional",
    )
    db.add(account)
    await db.flush()

    # Rattachement au client commercial par email (best-effort, ne lève pas).
    try:
        from app.services.client_linking import auto_link_account

        await auto_link_account(db, account)
    except Exception:  # pragma: no cover - best-effort
        logger.warning("auto_link_account a échoué pour %s", clean_email, exc_info=True)

    return account
