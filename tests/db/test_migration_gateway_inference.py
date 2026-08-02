"""Tests for the hosts.gateway_inference migration (66b439064d06).

Verifies that at head the ``hosts.gateway_inference`` column exists, that the
host store round-trips a per-family gateway-inference map through it (upsert on
connect and the live readiness refresh), and that upgrade→downgrade round-trips
(the column is dropped on downgrade, back to the prior revision).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.engine import Engine

from omnigent.db.utils import (
    _build_alembic_config,
    clear_engine_cache,
    get_or_create_engine,
)
from omnigent.stores.host_store import HostStore

# The revision just below 66b439064d06 in the chain; downgrade targets it.
_PRIOR_REVISION = "c4d5e6f7a8b9"


@pytest.fixture
def db_engine(tmp_path: Path) -> Iterator[Engine]:
    """Fresh SQLite database with the full migration chain applied."""
    db_path = tmp_path / "test.db"
    uri = f"sqlite:///{db_path}"
    engine = get_or_create_engine(uri)
    try:
        yield engine
    finally:
        clear_engine_cache()


def test_gateway_inference_column_present_at_head(db_engine: Engine) -> None:
    """At head the nullable ``hosts.gateway_inference`` column exists."""
    columns = {c["name"]: c for c in sa.inspect(db_engine).get_columns("hosts")}
    assert "gateway_inference" in columns
    assert columns["gateway_inference"]["nullable"] is True


def test_host_store_round_trips_gateway_inference(db_uri: str) -> None:
    """upsert_on_connect persists the per-family map and reads it back."""
    store = HostStore(db_uri)
    gw = {"claude-native": True, "codex": False}
    created = store.upsert_on_connect(
        host_id="bdda8ba7e34130318b54dd872eb160af",
        name="test-laptop",
        user_id="alice@example.com",
        gateway_inference=gw,
    )
    assert created.gateway_inference == gw
    # Re-read through get_host: the JSON column decodes back to the map.
    fetched = store.get_host("bdda8ba7e34130318b54dd872eb160af")
    assert fetched is not None
    assert fetched.gateway_inference == gw


def test_host_store_none_gateway_inference_is_unknown(db_uri: str) -> None:
    """A host that reports nothing stores NULL, read back as None (unknown)."""
    store = HostStore(db_uri)
    created = store.upsert_on_connect(
        host_id="aaaa8ba7e34130318b54dd872eb160af",
        name="older-host",
        user_id="alice@example.com",
        gateway_inference=None,
    )
    assert created.gateway_inference is None
    fetched = store.get_host("aaaa8ba7e34130318b54dd872eb160af")
    assert fetched is not None
    assert fetched.gateway_inference is None


def test_update_harness_readiness_writes_gateway_inference(db_uri: str) -> None:
    """The live readiness refresh updates the stored gateway-inference map."""
    store = HostStore(db_uri)
    host_id = "cccc8ba7e34130318b54dd872eb160af"
    store.upsert_on_connect(
        host_id=host_id,
        name="laptop",
        user_id="alice@example.com",
        gateway_inference={"claude-native": False, "codex": False},
    )
    store.update_harness_readiness(
        host_id,
        {"claude-native": True, "codex": True},
        {"claude-native": True, "codex": True},
    )
    fetched = store.get_host(host_id)
    assert fetched is not None
    assert fetched.gateway_inference == {"claude-native": True, "codex": True}


def test_downgrade_drops_gateway_inference_column(tmp_path: Path) -> None:
    """Downgrade to the prior revision removes the column (round-trip)."""
    db_path = tmp_path / "downgrade.db"
    uri = f"sqlite:///{db_path}"
    engine = get_or_create_engine(uri)

    # Sanity: head state before downgrade carries the column.
    columns = {c["name"] for c in sa.inspect(engine).get_columns("hosts")}
    assert "gateway_inference" in columns

    config = _build_alembic_config(uri)
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.downgrade(config, _PRIOR_REVISION)

    remaining = {c["name"] for c in sa.inspect(engine).get_columns("hosts")}
    assert "gateway_inference" not in remaining
    # A sibling column survives — downgrade dropped only the new column.
    assert "configured_harnesses" in remaining

    engine.dispose()
    clear_engine_cache()
