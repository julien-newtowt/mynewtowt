"""Le notify party se saisit depuis le portail expéditeur.

Décision de Yasmin (2026-08-03) : « Notify party & consignee à saisir depuis le
portail expéditeur de MyTOWT. »

Constat avant correctif : les cinq colonnes `notify_*` existaient déjà sur
`PackingListBatch` **et** figuraient dans `AUDITABLE_FIELDS` — elles étaient donc
auditées dès qu'elles étaient remplies. Mais elles n'étaient exposées **que dans
le formulaire staff** : le portail affichait `shipper` et `consignee`, jamais le
notify party. Un BL généré depuis une packing list remplie par l'expéditeur
sortait donc systématiquement **sans notify party**, sans que rien ne le signale.
"""

from __future__ import annotations

import pytest


def test_notify_fields_are_exposed_in_the_portal_form():
    """Les 4 champs notify doivent être saisissables depuis `/p/{token}/packing`."""
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "portal/packing.html")[0]
    for field in ("notify_name", "notify_address", "notify_city", "notify_country"):
        assert f'name="{field}"' in src, f"{field} absent du formulaire portail"


def test_notify_name_is_also_on_the_creation_form():
    """Le formulaire de création (et pas seulement d'édition) porte le notify."""
    from app.templating import templates

    src = templates.env.loader.get_source(templates.env, "portal/packing.html")[0]
    # Deux occurrences attendues : une à l'édition d'un batch existant, une à la
    # création. Sans la seconde, l'expéditeur devrait créer puis rouvrir le lot.
    assert src.count('name="notify_name"') >= 2


def test_notify_fields_are_audited():
    """Ils doivent rester dans la piste d'audit champ-par-champ."""
    from app.services.packing_list import AUDITABLE_FIELDS

    for field in (
        "notify_name",
        "notify_address",
        "notify_postal",
        "notify_city",
        "notify_country",
    ):
        assert field in AUDITABLE_FIELDS


@pytest.mark.parametrize("lang", ["fr", "en", "es", "pt_br", "vi"])
def test_notify_labels_exist_in_all_five_languages(lang):
    """Parité i18n — le projet impose les 5 catalogues, pas seulement le français."""
    import importlib

    cat = importlib.import_module(f"app.i18n.{lang}").CATALOG
    for key in (
        "pt_notify",
        "pt_packing_notify_address",
        "pt_packing_notify_city",
        "pt_packing_notify_country",
    ):
        assert key in cat, f"{key} manquant dans {lang}"
        assert cat[key].strip(), f"{key} vide dans {lang}"


@pytest.mark.asyncio
async def test_shipper_can_fill_notify_party_through_the_portal(db):
    """Bout en bout : la saisie portail persiste bien le notify party."""
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from sqlalchemy import select

    from app.models.commercial import Client, Order
    from app.models.packing_list import PackingList, PackingListBatch
    from app.routers.cargo_portal_router import portal_packing_add

    class _Req:
        headers: dict[str, str] = {}
        client = SimpleNamespace(host="203.0.113.9")
        url = SimpleNamespace(path="/p/tok/packing")

        def __init__(self, form):
            self._form = form

        async def form(self):
            return self._form

    token = "b" * 24
    db.add(Client(id=1, name="ACME", client_type="shipper"))
    await db.flush()
    db.add(Order(id=1, reference="CMD-1", client_id=1))
    await db.flush()
    db.add(
        PackingList(
            id=1,
            order_id=1,
            token=token,
            token_expires_at=datetime.now(UTC) + timedelta(days=30),
        )
    )
    await db.flush()

    await portal_packing_add(
        token,
        _Req(
            {
                "pallet_count": "2",
                "consignee_name": "Belco France",
                "notify_name": "Transitaire Le Havre",
                "notify_city": "Le Havre",
                "notify_country": "FR",
            }
        ),
        db=db,
    )

    batch = (await db.execute(select(PackingListBatch))).scalars().one()
    assert batch.notify_name == "Transitaire Le Havre"
    assert batch.notify_city == "Le Havre"
    assert batch.notify_country == "FR"
    # Le consignee reste une donnée distincte du notify party (§5.3 de la spec).
    assert batch.consignee_name == "Belco France"
