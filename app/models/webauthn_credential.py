"""WebAuthn credential — passkey ou clé matérielle FIDO2.

Polymorphe (``owner_type`` ∈ {"client", "staff"}, ``owner_id`` pointe
vers ``client_accounts`` ou ``users``). Un même owner peut enregistrer
plusieurs credentials (un téléphone + une YubiKey + un laptop).

Champs critiques :
- ``credential_id`` : identifiant unique du credential (binaire, base64url
  en pratique pour stockage texte). Renvoyé par l'authenticator au moment
  du register, présenté à chaque login.
- ``public_key`` : clé publique COSE-encoded (binaire). Sert à vérifier
  les signatures lors des challenges d'authentification.
- ``sign_count`` : compteur monotone. Tout incrément < dernier connu
  indique un clonage potentiel → on rejette le login.
- ``transports`` : liste CSV des transports supportés
  ("usb,nfc,ble,internal,hybrid") — sert d'indice côté navigator.
- ``aaguid`` : GUID hardware de l'authenticator (optionnel, fingerprint).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WebAuthnCredential(Base):
    __tablename__ = "webauthn_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "client" | "staff"
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # credential_id est stocké en base64url côté DB pour lookups simples
    credential_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Métadonnées
    name: Mapped[str | None] = mapped_column(String(120))  # libellé user, ex. "iPhone Julien"
    transports: Mapped[str | None] = mapped_column(String(80))  # CSV
    aaguid: Mapped[str | None] = mapped_column(String(40))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_webauthn_owner", "owner_type", "owner_id"),
    )
