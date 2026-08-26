"""Durcissement du tunnel public d'estimation (constats E-1 / E-3).

Deux défauts couverts ici :

* **E-3** — les LOCODEs étaient repris bruts du formulaire. Or la résolution de
  grille est *get-or-create* : une paire inconnue créait une route persistante
  dans la grille par défaut (pollution du référentiel tarifaire depuis un
  formulaire public), et une valeur de plus de 5 caractères provoquait une
  erreur de troncature côté Postgres — invisible en test, SQLite n'appliquant
  pas les longueurs de colonne.
* **E-1** — entropie de la référence : 24 bits rendaient les estimations
  énumérables (une référence donne accès aux prix et aux coordonnées du
  demandeur).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.datastructures import FormData

from app.models.commercial import RateGrid, RateGridLine
from app.models.port import Port


class _Req:
    """Requête minimale : formulaire fourni, en-têtes vides, IP fixe."""

    def __init__(self, form: dict[str, str]):
        self._form = FormData(list(form.items()))
        self.headers: dict[str, str] = {}
        self.client = SimpleNamespace(host="203.0.113.7")
        self.state = SimpleNamespace(lang="fr")
        self.query_params: dict[str, str] = {}
        self.url = SimpleNamespace(path="/devis")
        self.cookies: dict[str, str] = {}

    async def form(self):
        return self._form


async def _referentiel(db) -> None:
    db.add_all(
        [
            Port(id=1, locode="FRLEH", name="Le Havre", country="FR"),
            Port(id=2, locode="BRSSZ", name="Santos", country="BR"),
        ]
    )
    await db.flush()


def _payload(**over: str) -> dict[str, str]:
    base = {
        "pol": "FRLEH",
        "pod": "BRSSZ",
        "items-0-format": "EPAL",
        "items-0-count": "20",
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_unknown_locode_is_rejected_and_creates_no_grid_route(db):
    """Une paire de ports inconnue est refusée — et ne matérialise aucune route."""
    from app.routers.devis_router import devis_submit

    await _referentiel(db)

    resp = await devis_submit(_Req(_payload(pol="AAAAA", pod="BBBBB")), db=db, client=None)

    assert resp.status_code == 422
    # Aucun effet de bord sur le référentiel tarifaire.
    assert (await db.execute(RateGrid.__table__.select())).fetchone() is None
    assert (await db.execute(RateGridLine.__table__.select())).fetchone() is None


@pytest.mark.asyncio
async def test_overlong_locode_is_rejected_before_reaching_the_database(db):
    """Une valeur > 5 caractères est refusée (elle tronquerait la colonne)."""
    from app.routers.devis_router import devis_submit

    await _referentiel(db)

    resp = await devis_submit(_Req(_payload(pod="BBBBBBBBBB")), db=db, client=None)

    assert resp.status_code == 422
    assert (await db.execute(RateGridLine.__table__.select())).fetchone() is None


def test_quote_reference_has_enough_entropy():
    """La référence d'estimation ne doit pas être énumérable (E-1)."""
    from app.models.quote import Quote
    from app.services.quoting import generate_quote_reference

    ref = generate_quote_reference()
    suffix = ref.rsplit("-", 1)[1]
    assert len(suffix) >= 12  # ≥ 48 bits
    assert set(suffix) <= set("0123456789ABCDEF")
    # Doit tenir dans la colonne.
    assert len(ref) <= Quote.__table__.c.reference.type.length
    assert generate_quote_reference() != ref
