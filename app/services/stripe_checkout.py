"""Stripe Checkout — encaissement CB de la vente à bord.

Réintroduction **ciblée** de Stripe (retiré de la facturation fret en V3.1) :
le commandant génère un **lien de paiement hébergé** (Stripe Checkout Session),
affiché sous forme d'URL + QR code au collaborateur, qui paie sur son propre
appareil. La confirmation arrive par **webhook** (``/webhooks/stripe``).

Secure-by-default : sans ``STRIPE_SECRET_KEY`` la création de session lève
``StripeNotConfigured`` (le routeur renvoie 503). Le SDK synchrone ``stripe``
est appelé dans un executor pour ne pas bloquer la boucle d'événements
(même approche que ``services.email``).

Devises : Stripe attend le montant en **plus petite unité**. Les devises
« zéro-décimale » (ex. VND) ne sont pas multipliées par 100.
"""

from __future__ import annotations

import asyncio
import time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import stripe

from app.config import settings

# Devises Stripe sans sous-unité (le montant est l'entier tel quel).
# Référence Stripe « zero-decimal currencies » (sous-ensemble utile ici).
ZERO_DECIMAL_CURRENCIES: frozenset[str] = frozenset(
    {
        "BIF",
        "CLP",
        "DJF",
        "GNF",
        "JPY",
        "KMF",
        "KRW",
        "MGA",
        "PYG",
        "RWF",
        "UGX",
        "VND",
        "VUV",
        "XAF",
        "XOF",
        "XPF",
    }
)


class StripeNotConfigured(Exception):
    """Clé Stripe absente : voie carte indisponible (503 côté route)."""


class StripeCheckoutError(Exception):
    """Erreur d'appel à l'API Stripe (message affichable)."""


class StripeSessionAlreadyPaid(StripeCheckoutError):
    """La session visée a déjà été payée : elle ne peut plus être fermée.

    Signalé à part car c'est le cas dangereux : on s'apprêtait à annuler la
    vente ou à l'encaisser en espèces alors que le client venait de payer par
    carte. Poursuivre produirait un double encaissement.
    """


# Durée de vie d'un lien de paiement. Stripe la fixe à 24 h par défaut ; à bord
# la vente se règle dans la minute, et un lien qui traîne est une fenêtre de
# double débit (le client peut le rouvrir après un règlement en espèces).
SESSION_TTL_SECONDS = 30 * 60

# Délai réseau borné : le SDK attend ~80 s par défaut et réessaie deux fois. Sur
# liaison satellite, l'affichage d'une vente — qui interroge Stripe — se figeait
# jusqu'à plusieurs minutes. Mieux vaut échouer vite et proposer les espèces.
REQUEST_TIMEOUT_SECONDS = 8

# Version d'API épinglée : sans elle, une montée de version côté Stripe peut
# changer la forme des objets reçus par le webhook sans qu'on l'ait décidé.
API_VERSION = "2024-11-20.acacia"


def _api_kwargs() -> dict[str, Any]:
    """Arguments communs à tous les appels SDK (clé, version, délais)."""
    return {
        "api_key": settings.stripe_secret_key or "",
        "stripe_version": API_VERSION,
        "timeout": REQUEST_TIMEOUT_SECONDS,
        "max_network_retries": 1,
    }


def is_configured() -> bool:
    """Vrai si l'API Stripe est joignable (clé secrète présente).

    Gouverne les opérations sortantes : relire, fermer, réconcilier.
    """
    return settings.stripe_enabled


def card_payments_enabled() -> bool:
    """Vrai si un **nouveau** lien de paiement peut être proposé au client.

    Exige en plus le secret de webhook : sans canal de confirmation, une carte
    débitée ne remonterait jamais dans l'application.
    """
    return settings.stripe_card_payments_enabled


def webhook_configured() -> bool:
    """Vrai si le secret de signature du webhook est configuré."""
    return bool(settings.stripe_webhook_secret)


def amount_to_minor(amount: Decimal, currency: str) -> int:
    """Convertit un ``Decimal`` en plus petite unité Stripe (int)."""
    cur = currency.upper()
    value = Decimal(amount)
    if cur in ZERO_DECIMAL_CURRENCIES:
        return int(value.to_integral_value(rounding=ROUND_HALF_UP))
    return int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _create_session_sync(
    *,
    line_items: list[dict],
    success_url: str,
    cancel_url: str,
    metadata: dict[str, str],
    client_reference_id: str,
    expires_at: int,
) -> Any:
    return stripe.checkout.Session.create(
        mode="payment",
        line_items=line_items,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata,
        client_reference_id=client_reference_id,
        expires_at=expires_at,
        # Le montant fait foi côté serveur ; on interdit tout ajustement client.
        submit_type="pay",
        **_api_kwargs(),
    )


async def create_session(
    sale: Any,
    lines: list[Any],
    *,
    success_url: str,
    cancel_url: str,
    sku_by_product_id: dict[int, str] | None = None,
) -> Any:
    """Crée une Checkout Session pour une vente. Montants recalculés serveur.

    Chaque ligne de vente devient un ``line_item`` à quantité 1 dont le montant
    est le **total de ligne** (évite les quantités fractionnaires non gérées par
    Stripe et garantit que le total Stripe == total serveur).

    ``sku_by_product_id`` (optionnel) : mappe ``product_id`` → référence produit
    (SKU). Quand une ligne y correspond, le SKU préfixe le libellé affiché sur
    la page de paiement Stripe et le reçu — traçabilité de l'article vendu.
    """
    if not card_payments_enabled():
        raise StripeNotConfigured(
            "Voie carte indisponible : STRIPE_SECRET_KEY et STRIPE_WEBHOOK_SECRET "
            "sont tous deux requis (sans webhook, un paiement ne remonterait pas)."
        )
    skus = sku_by_product_id or {}
    currency = sale.currency.lower()
    line_items: list[dict] = []
    for line in lines:
        qty = Decimal(line.qty)
        qty_txt = f"{qty.normalize():f}"
        sku = skus.get(line.product_id) if line.product_id is not None else None
        name = f"[{sku}] {line.label} ×{qty_txt}" if sku else f"{line.label} ×{qty_txt}"
        line_items.append(
            {
                "quantity": 1,
                "price_data": {
                    "currency": currency,
                    "unit_amount": amount_to_minor(Decimal(line.line_total), sale.currency),
                    "product_data": {"name": name},
                },
            }
        )
    if not line_items:
        raise StripeCheckoutError("Vente sans article : rien à encaisser.")
    # ``env`` permet au webhook de rejeter un événement émis par une autre
    # installation partageant le même compte Stripe (staging ↔ production).
    metadata = {
        "sale_id": str(sale.id),
        "reference": sale.reference,
        "env": settings.app_env,
    }
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: _create_session_sync(
                line_items=line_items,
                success_url=success_url,
                cancel_url=cancel_url,
                metadata=metadata,
                client_reference_id=sale.reference,
                expires_at=expires_at,
            ),
        )
    except stripe.StripeError as e:  # type: ignore[attr-defined]
        raise StripeCheckoutError(f"Erreur Stripe : {e}") from e


async def retrieve_session(session_id: str) -> Any:
    """Récupère une Checkout Session (pour réafficher son ``url`` / statut)."""
    if not is_configured():
        raise StripeNotConfigured("Stripe non configuré.")
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: stripe.checkout.Session.retrieve(session_id, **_api_kwargs()),
        )
    except stripe.StripeError as e:  # type: ignore[attr-defined]
        raise StripeCheckoutError(f"Erreur Stripe : {e}") from e


async def expire_session(session_id: str) -> str:
    """Ferme une Checkout Session encore ouverte. Renvoie son statut final.

    Appelée avant d'annuler une vente, de l'encaisser en espèces ou de
    régénérer un lien. Sans cela, la session restait **payable** : le client
    qui avait déjà scanné le QR pouvait régler par carte une vente réglée en
    liquide ou annulée entre-temps — double débit réel, absorbé en silence par
    la garde d'idempotence du règlement, et non remboursable dans l'application.

    On relit la session avant de la fermer, délibérément : c'est ce qui permet
    de détecter le cas dangereux — le client vient de payer pendant que le
    commandant cliquait — et de lever ``StripeSessionAlreadyPaid`` plutôt que
    d'enchaîner sur un second encaissement.

    Renvoie ``"expired"`` si la session a été fermée ici, sinon son statut déjà
    acquis (``"expired"``, ``"complete"``). Lève ``StripeCheckoutError`` si on
    ne peut pas garantir qu'elle est fermée : l'appelant doit alors renoncer,
    jamais poursuivre.
    """
    if not is_configured():
        raise StripeNotConfigured("Stripe non configuré.")
    session = await retrieve_session(session_id)
    if getattr(session, "payment_status", None) in ("paid", "no_payment_required"):
        raise StripeSessionAlreadyPaid(
            "Le client a déjà réglé cette vente par carte : rechargez la vente "
            "avant toute autre action."
        )
    status = getattr(session, "status", None)
    if status != "open":
        # Déjà expirée côté Stripe : l'objectif est atteint.
        return status or "unknown"
    try:
        loop = asyncio.get_running_loop()
        closed = await loop.run_in_executor(
            None,
            lambda: stripe.checkout.Session.expire(session_id, **_api_kwargs()),
        )
    except stripe.StripeError as e:  # type: ignore[attr-defined]
        raise StripeCheckoutError(f"Fermeture du lien de paiement impossible : {e}") from e
    return getattr(closed, "status", "expired") or "expired"


def construct_event(payload: bytes, sig_header: str) -> Any:
    """Vérifie la signature d'un webhook Stripe et renvoie l'event.

    Lève ``StripeNotConfigured`` sans secret, ``StripeCheckoutError`` si la
    signature/le payload est invalide (le routeur répond alors 400).
    """
    if not webhook_configured():
        raise StripeNotConfigured("STRIPE_WEBHOOK_SECRET manquant.")
    try:
        return stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)
    except (ValueError, stripe.SignatureVerificationError) as e:  # type: ignore[attr-defined]
        raise StripeCheckoutError(f"Webhook Stripe invalide : {e}") from e
