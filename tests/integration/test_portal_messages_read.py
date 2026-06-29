"""CARGO-14 — messagerie portail : marquage lu + compteurs non-lus.

`PortalMessage.is_read` n'était pas exploité. On vérifie que la consultation
marque lus les messages de l'autre partie et que les compteurs non-lus
(badge staff) sont corrects et orientés par lecteur.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_portal_unread_counts_and_mark_read(db):
    from app.models.packing_list import PackingList, PortalMessage
    from app.services import messaging

    db.add(PackingList(id=1))
    await db.flush()
    db.add(PortalMessage(packing_list_id=1, sender="client", body="bonjour de l'expéditeur"))
    db.add(PortalMessage(packing_list_id=1, sender="staff", body="réponse de l'armateur"))
    await db.flush()

    # Le staff a 1 message client non lu ; l'expéditeur a 1 message staff non lu.
    assert await messaging.portal_unread_counts(db, [1], reader="staff") == {1: 1}
    assert await messaging.portal_unread_counts(db, [1], reader="client") == {1: 1}

    # Le staff consulte → les messages du client passent en lu…
    await messaging.mark_portal_read(db, 1, reader="staff")
    assert await messaging.portal_unread_counts(db, [1], reader="staff") == {}
    # …mais le message du staff reste non lu côté expéditeur.
    assert await messaging.portal_unread_counts(db, [1], reader="client") == {1: 1}


@pytest.mark.asyncio
async def test_portal_unread_counts_empty_input(db):
    from app.services import messaging

    assert await messaging.portal_unread_counts(db, [], reader="staff") == {}
