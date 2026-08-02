"""Add gateway_inference to hosts (wave-0 empty revision; wave-1 stream 4 fills).

Revision ID: 66b439064d06
Revises: c4d5e6f7a8b9
Create Date: 2026-08-02

Adds ``hosts.gateway_inference`` — the JSON-encoded per-harness map a host
reports alongside its readiness, recording whether that harness family's launch
on the host resolves AI-Gateway-backed inference (e.g.
``'{"claude-native": true, "codex": false}'``). A family the host could not
evaluate is omitted from the map; NULL means the host never reported the map at
all (an older host build) and is treated as unknown, never as "nothing is
gateway-backed". Surfaced via ``GET /v1/hosts`` and the session snapshot so the
web UI only offers Smart Routing where the routing apply layer can actually
rewrite the launch model (plan 3f).

Wave 0 pre-created this revision chained off the true alembic head on
origin/main, ``c4d5e6f7a8b9`` (verified via ``alembic heads``); wave-1 stream 4
fills the body. Do NOT change the revision id / down_revision — the chain must
resolve to the single head ``66b439064d06``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "66b439064d06"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable ``gateway_inference`` column to ``hosts``.

    Batch mode so the DDL runs on SQLite too, and so the project's
    migration-safety test (which requires every schema change to go through
    ``batch_alter_table``) passes.
    """
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.add_column(sa.Column("gateway_inference", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the ``gateway_inference`` column from ``hosts``.

    Batch mode so ``DROP COLUMN`` works on SQLite (rejected by the bare ``op``
    proxy pre-3.35).
    """
    with op.batch_alter_table("hosts") as batch_op:
        batch_op.drop_column("gateway_inference")
