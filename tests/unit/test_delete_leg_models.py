"""Tests pour ``delete_leg`` — inventaire des dépendances d'un leg.

Deux garde-fous :

1. **Cohérence des inventaires** — chaque modèle listé a bien un ``leg_id``,
   et celui des modèles « déliés » est bien nullable. Bug-pattern récurrent :
   copier-coller une classe voisine qui n'a pas le même schéma → AttributeError
   à l'exécution = 500 utilisateur visible.
2. **Exhaustivité (sentinelle)** — toute table qui référence ``legs.id`` doit
   être couverte : soit par un ``ondelete`` en base (CASCADE / SET NULL), soit
   par l'inventaire bloquant, soit par l'inventaire des FK déliées. Une table
   oubliée = ``IntegrityError`` non traduite à la suppression → **500**
   (régression réellement survenue le 2026-09-02 sur ``packing_lists``).
"""

from __future__ import annotations

import pytest

import app.models  # noqa: F401  (enregistre toutes les tables sur Base.metadata)
from app.database import Base
from app.services import planning

_BLOCKING = [m for m, _label in planning._leg_blocking_models()]
_UNLINKED = list(planning._leg_unlinked_models())

# Tables dont la suppression est gérée par un chemin dédié dans ``delete_leg``.
_HANDLED_SPECIALLY = {
    # Rollup dérivé : supprimé explicitement s'il est auto-calculé, bloquant
    # s'il a été saisi à la main (``is_manual``).
    "leg_kpis",
}


@pytest.mark.parametrize("model", _BLOCKING, ids=lambda m: m.__name__)
def test_blocking_models_have_leg_id(model):
    """Chaque modèle scanné par l'inventaire bloquant doit avoir ``leg_id``."""
    assert hasattr(model, "leg_id"), f"{model.__name__} n'a pas leg_id"


@pytest.mark.parametrize("model", _UNLINKED, ids=lambda m: m.__name__)
def test_unlinked_models_have_nullable_leg_id(model):
    """Chaque modèle « délié » doit avoir ``leg_id`` ET il doit être nullable."""
    assert hasattr(model, "leg_id"), f"{model.__name__} n'a pas leg_id"
    col = model.__table__.columns["leg_id"]
    assert col.nullable, (
        f"{model.__name__}.leg_id est NOT NULL — ne peut pas être set NULL ; "
        f"il appartient à l'inventaire bloquant, pas aux FK déliées"
    )


def _leg_foreign_keys() -> list[tuple[str, str, str | None]]:
    """(table, colonne, ondelete) de toutes les FK pointant vers ``legs.id``."""
    found: list[tuple[str, str, str | None]] = []
    for table in Base.metadata.tables.values():
        for col in table.columns:
            for fk in col.foreign_keys:
                if fk.column.table.name == "legs":
                    found.append((table.name, col.name, fk.ondelete))
    return sorted(found)


def test_every_leg_foreign_key_is_covered():
    """Sentinelle : aucune FK vers ``legs.id`` ne doit échapper à l'inventaire.

    Sans ``ondelete`` en base, PostgreSQL refuse la suppression du parent :
    la table DOIT donc être soit bloquante (erreur métier lisible), soit
    déliée avant suppression. Une nouvelle table oubliée ici sort en 500.
    """
    covered = {m.__table__.name for m in _BLOCKING} | {m.__table__.name for m in _UNLINKED}
    covered |= _HANDLED_SPECIALLY
    orphans = [
        f"{table}.{column}"
        for table, column, ondelete in _leg_foreign_keys()
        if (ondelete or "").upper() not in {"CASCADE", "SET NULL"} and table not in covered
    ]
    assert not orphans, (
        "FK vers legs.id non couvertes (ni ondelete en base, ni inventaire "
        "de suppression) — la suppression d'un leg sortira en 500 : " + ", ".join(orphans)
    )


def test_sentinel_actually_scans_something():
    """Garde-fou du garde-fou : si le scan ne trouve rien, il ne prouve rien."""
    fks = _leg_foreign_keys()
    assert len(fks) > 20, f"scan des FK vers legs.id anormalement pauvre : {len(fks)}"
