"""ADM-04 — exports CSV (global ZIP + sélectif) et purges DB ciblées.

Sécurité :
- **Whitelist stricte** des tables exportables/purgeables (jamais de nom de
  table dynamique non validé — anti-injection d'identifiant).
- Accès à la table via ``Base.metadata.tables[name]`` (objet SQLAlchemy),
  jamais par f-string SQL. SELECT/DELETE via l'API d'expression (paramétrée).
- Les cellules CSV passent par ``csv_safe.sanitize_row`` (anti-formule).
- Les tables sensibles (identifiants/MFA/paie) sont **exclues** de l'export ;
  la purge est restreinte aux journaux et positions (hygiène de campagne).
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base
from app.utils.csv_safe import sanitize_row

# Tables exportables (données opérationnelles + métier). On EXCLUT volontairement
# les secrets : users, role_permissions, mfa_recovery_codes, known_devices,
# client_accounts (mots de passe), payslips/payroll/silae (paie sensible).
ALLOWED_EXPORT_TABLES: tuple[str, ...] = (
    "vessels",
    "ports",
    "legs",
    "leg_finances",
    "leg_kpis",
    "vessel_positions",
    "vessel_weather",
    "sof_events",
    "cargo_documents",
    "leg_attachments",
    "eta_shifts",
    "escale_operations",
    "docker_shifts",
    "mrv_events",
    "stowage_plans",
    "stowage_items",
    "commercial_clients",
    "commercial_orders",
    "order_assignments",
    "rate_grids",
    "rate_grid_lines",
    "bookings",
    "packing_lists",
    "packing_list_batches",
    "crew_members",
    "crew_assignments",
    "activity_logs",
)

# Purge restreinte aux journaux/positions (append-only, sûrs à nettoyer).
ALLOWED_PURGE_TABLES: tuple[str, ...] = (
    "activity_logs",
    "vessel_positions",
    "vessel_weather",
    "portal_access_logs",
    "rate_limit_attempts",
)

# Colonne d'horodatage de chaque table purgeable, pour la purge **ciblée par
# rétention** (supprimer les lignes plus anciennes qu'une date). Le nom de
# colonne vient TOUJOURS de cette whitelist (jamais d'une entrée libre) ; la
# date est un paramètre lié (expression SQLAlchemy paramétrée, pas de f-string).
PURGE_DATE_COLUMNS: dict[str, str] = {
    "activity_logs": "created_at",
    "vessel_positions": "recorded_at",
    "vessel_weather": "recorded_at",
    "portal_access_logs": "accessed_at",
    "rate_limit_attempts": "attempted_at",
}
# Garde de complétude : toute table purgeable doit déclarer sa colonne de date.
assert set(ALLOWED_PURGE_TABLES) == set(
    PURGE_DATE_COLUMNS
), "chaque table purgeable doit déclarer sa colonne d'horodatage (PURGE_DATE_COLUMNS)"


def _table(name: str):
    """Table SQLAlchemy d'un nom whitelisté, sinon ``ValueError``."""
    if name not in Base.metadata.tables:
        raise ValueError(f"table inconnue : {name}")
    return Base.metadata.tables[name]


async def export_table_csv(db: AsyncSession, table_name: str) -> str:
    """CSV (str) d'une table exportable. ``ValueError`` si non whitelistée."""
    if table_name not in ALLOWED_EXPORT_TABLES:
        raise ValueError(f"table non exportable : {table_name}")
    table = _table(table_name)
    cols = [c.name for c in table.columns]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(cols)
    for row in (await db.execute(select(table))).all():
        writer.writerow(sanitize_row([getattr(row, c, None) for c in cols]))
    return buf.getvalue()


async def export_global_zip(db: AsyncSession) -> bytes:
    """ZIP (bytes) d'un CSV par table exportable + un manifeste."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = [f"NEWTOWT export — {datetime.now(UTC).isoformat()}", ""]
        for name in ALLOWED_EXPORT_TABLES:
            csv_text = await export_table_csv(db, name)
            zf.writestr(f"{name}.csv", csv_text)
            manifest.append(f"{name}.csv ({csv_text.count(chr(10))} lignes)")
        zf.writestr("MANIFEST.txt", "\n".join(manifest))
    return buf.getvalue()


async def purge_table(db: AsyncSession, table_name: str) -> int:
    """Vide une table purgeable (DELETE paramétré). Retourne le nb de lignes.

    ``ValueError`` si la table n'est pas dans la whitelist de purge.
    """
    if table_name not in ALLOWED_PURGE_TABLES:
        raise ValueError(f"table non purgeable : {table_name}")
    table = _table(table_name)
    result = await db.execute(delete(table))
    await db.flush()
    return int(result.rowcount or 0)


async def purge_table_before(db: AsyncSession, table_name: str, cutoff: datetime) -> int:
    """Purge **ciblée par rétention** : supprime les lignes de ``table_name``
    plus anciennes que ``cutoff`` (sur la colonne d'horodatage whitelistée).

    Le nom de colonne provient de ``PURGE_DATE_COLUMNS`` (jamais d'une entrée
    utilisateur) et la date est un paramètre lié → DELETE paramétré, sans
    interpolation d'identifiant ni de valeur. ``ValueError`` si la table n'est
    pas purgeable. Retourne le nombre de lignes supprimées.
    """
    if table_name not in ALLOWED_PURGE_TABLES:
        raise ValueError(f"table non purgeable : {table_name}")
    col_name = PURGE_DATE_COLUMNS[table_name]
    table = _table(table_name)
    column = table.c[col_name]
    result = await db.execute(delete(table).where(column < cutoff))
    await db.flush()
    return int(result.rowcount or 0)
