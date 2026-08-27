"""Tests for idempotent add_column in the task_summary migration.

Migration ``za2b3c4d5e6f`` calls ``op.add_column`` unconditionally. If
``omnigent_conversation_metadata.task_summary`` already exists — because a
hotfix or a previous deployment added it outside of Alembic — the migration
raises::

    OperationalError: (sqlite3.OperationalError) duplicate column name: task_summary

The fix is to guard the ``add_column`` call so it is a no-op when the column
is already present.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.engine import Engine

from omnigent.db.utils import _build_alembic_config, clear_engine_cache

# Revision ID of the migration under test.
_TARGET_REVISION = "za2b3c4d5e6f"
# The revision that precedes it in the chain.
_PREV_REVISION = "d5e9f1a2b3c4"


def _upgrade(uri: str, engine: Engine, revision: str) -> None:
    config = _build_alembic_config(uri)
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, revision)


@pytest.fixture
def pre_migration_engine(tmp_path: Path):
    """SQLite engine upgraded to the revision *before* za2b3c4d5e6f."""
    db_path = tmp_path / "test.db"
    uri = f"sqlite:///{db_path}"
    engine = sa.create_engine(uri)
    _upgrade(uri, engine, _PREV_REVISION)
    try:
        yield uri, engine
    finally:
        engine.dispose()
        clear_engine_cache()


def test_upgrade_succeeds_when_task_summary_already_exists(
    pre_migration_engine: tuple[str, Engine],
) -> None:
    """Migration za2b3c4d5e6f must not crash when task_summary already exists.

    Scenario: a hotfix (or a previous deployment) added
    ``omnigent_conversation_metadata.task_summary`` to the live DB *before*
    the Alembic migration that officially introduces it was applied.  When the
    server next starts, ``alembic upgrade head`` runs and hits
    ``op.add_column`` unconditionally — resulting in::

        OperationalError: duplicate column name: task_summary

    The migration must guard the add so it is idempotent: a pre-existing
    column should cause the step to succeed (no-op), not raise.
    """
    uri, engine = pre_migration_engine

    # Pre-condition: task_summary already exists in the table (e.g. hotfix).
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "ALTER TABLE omnigent_conversation_metadata"
                " ADD COLUMN task_summary VARCHAR(128)"
            )
        )

    # Confirm the column is really there before running the migration.
    cols_before = {
        c["name"] for c in sa.inspect(engine).get_columns("omnigent_conversation_metadata")
    }
    assert "task_summary" in cols_before, (
        "Pre-condition failed: task_summary column should already exist before migration."
    )

    # Running the migration must succeed without raising.
    try:
        _upgrade(uri, engine, _TARGET_REVISION)
    except Exception as exc:
        pytest.fail(
            f"Migration {_TARGET_REVISION!r} raised {type(exc).__name__} when "
            f"task_summary column already existed: {exc}"
        )

    # Column must still be present and nullable afterward.
    cols_after = {
        c["name"]: c
        for c in sa.inspect(engine).get_columns("omnigent_conversation_metadata")
    }
    assert "task_summary" in cols_after, (
        "task_summary column is missing after migration — migration may have dropped it."
    )
    assert cols_after["task_summary"]["nullable"], (
        "task_summary must be nullable after migration."
    )


def test_upgrade_normal_path_adds_task_summary(
    pre_migration_engine: tuple[str, Engine],
) -> None:
    """Migration za2b3c4d5e6f adds task_summary on a clean DB (happy path).

    Verifies the column is created when it did not pre-exist, so the normal
    first-time migration path still works after any idempotency fix.
    """
    uri, engine = pre_migration_engine

    # Confirm the column does NOT exist before the migration.
    cols_before = {
        c["name"] for c in sa.inspect(engine).get_columns("omnigent_conversation_metadata")
    }
    assert "task_summary" not in cols_before, (
        "Pre-condition failed: task_summary should not exist before the migration."
    )

    _upgrade(uri, engine, _TARGET_REVISION)

    cols_after = {
        c["name"]: c
        for c in sa.inspect(engine).get_columns("omnigent_conversation_metadata")
    }
    assert "task_summary" in cols_after, (
        "Migration did not add task_summary column on a clean database."
    )
    assert cols_after["task_summary"]["nullable"], (
        "task_summary must be nullable."
    )
