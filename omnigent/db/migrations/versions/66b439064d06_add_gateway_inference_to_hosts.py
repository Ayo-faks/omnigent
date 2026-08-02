"""Add gateway_inference to hosts (wave-0 empty revision; wave-1 stream 4 fills).

Revision ID: 66b439064d06
Revises: c4d5e6f7a8b9
Create Date: 2026-08-02

Wave 0 pre-creates this revision so no two concurrent streams edit the migration
chain (plan 4b item 2, 4f). It chains off the true alembic head on origin/main,
``c4d5e6f7a8b9`` (verified via ``alembic heads``). Wave-1 stream 4 (the
gateway-inference signal, plan 3f) fills ``upgrade()`` / ``downgrade()`` with the
column(s) that persist a host's last-reported per-family gateway backing. It is
intentionally a no-op until then, so the chain resolves to a single head during
wave 1 and the branch stays migratable.
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "66b439064d06"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op — wave-1 stream 4 adds the gateway_inference column(s) here."""


def downgrade() -> None:
    """No-op — mirrors :func:`upgrade`."""
