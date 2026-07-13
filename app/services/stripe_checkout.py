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


def is_configured() -> bool:
    """Vrai si l'encaissement carte est configuré (clé secrète présente)."""
    return settings.stripe_enabled


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
    api_key: str,
    currency: str,
    line_items: list[dict],
    success_url: str,
    cancel_url: str,
    metadata: dict[str, str],
    client_reference_id: str,
) -> Any:
    return stripe.checkout.Session.create(
        api_key=api_key,
        mode="payment",
        line_items=line_items,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata,
        client_reference_id=client_reference_id,
        # Le montant fait foi côté serveur ; on interdit tout ajustement client.
        submit_type="pay",
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
    if not is_configured():
        raise StripeNotConfigured("Stripe non configuré (STRIPE_SECRET_KEY manquant).")
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
    metadata = {"sale_id": str(sale.id), "reference": sale.reference}
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: _create_session_sync(
                api_key=settings.stripe_secret_key or "",
                currency=currency,
                line_items=line_items,
                success_url=success_url,
                cancel_url=cancel_url,
                metadata=metadata,
                client_reference_id=sale.reference,
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
            lambda: stripe.checkout.Session.retrieve(
                session_id, api_key=settings.stripe_secret_key or ""
            ),
        )
    except stripe.StripeError as e:  # type: ignore[attr-defined]
        raise StripeCheckoutError(f"Erreur Stripe : {e}") from e


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
