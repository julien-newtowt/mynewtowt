"""baseline — post Sprint 2 schema

Revision ID: 0001_baseline_post_sprint2
Revises:
Create Date: 2026-04-21 00:00:00

This revision is intentionally a no-op. It anchors the Alembic history
at the schema state produced by ``Base.metadata.create_all`` at the end
of Sprint 2 (Phase 1 reset + ``must_change_password`` column on users
+ ``token_hash`` column on portal_access_logs + ``rate_limit_attempts``
table).

Run ``alembic stamp 0001_baseline_post_sprint2`` on existing databases
so future migrations apply cleanly. See ``migrations/README.md``.
"""
from typing import Sequence, Union


revision: str = "0001_baseline_post_sprint2"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op: Sprint 2 schema is already produced by Base.metadata.create_all.
    pass


def downgrade() -> None:
    # No-op: Alembic downgrade below the baseline is undefined.
    pass
