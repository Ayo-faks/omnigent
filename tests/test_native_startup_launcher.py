"""Tests for the generic tmux-driven stuck-prompt launcher.

Covers the harness-agnostic pieces in
:mod:`omnigent.native_startup_launcher`: the answer-key extraction contract and
that ``start_stuck_supervisor`` returns a running background task any tmux-driven
harness can drive. The pane-capture / send-keys / liveness helpers are thin
``tmux`` subprocess wrappers exercised end-to-end by the harness bindings.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from omnigent.native_startup_launcher import keys_from_result, start_stuck_supervisor
from omnigent.native_startup_supervisor import SupervisorConfig
from omnigent.server.schemas import ElicitationResult


def test_keys_from_result_reads_key_list() -> None:
    """An accepted verdict's ``content['keys']`` becomes the tmux key list."""
    result = ElicitationResult(action="accept", content={"keys": ["1", "Enter"]})
    assert keys_from_result(result) == ["1", "Enter"]


def test_keys_from_result_rejects_malformed() -> None:
    """Missing / non-list keys yield an empty list (the injector types nothing)."""
    assert keys_from_result(ElicitationResult(action="accept", content=None)) == []
    assert keys_from_result(ElicitationResult(action="accept", content={})) == []
    # ``keys`` present but a scalar, not a list.
    assert keys_from_result(ElicitationResult(action="accept", content={"keys": "1"})) == []


def test_keys_from_result_drops_empty_entries() -> None:
    """Empty-string entries are dropped so a stray blank never types a no-op key."""
    result = ElicitationResult(action="accept", content={"keys": ["", "Enter"]})
    assert keys_from_result(result) == ["Enter"]


@pytest.mark.asyncio
async def test_start_stuck_supervisor_returns_running_task() -> None:
    """The launcher returns a live task the caller owns and can cancel."""
    client = httpx.AsyncClient(base_url="http://test")
    async with client:
        task = start_stuck_supervisor(
            socket_path="/tmp/does-not-exist.sock",
            tmux_target="main",
            idle_marker_present=lambda pane: "READY" in pane,
            session_id="conv_1",
            client=client,
            harness="antigravity",
            # Never trips: a missing socket makes capture return "" (SETTLING),
            # so no elicitation is ever published during this smoke test.
            config=SupervisorConfig(cold_boot_quiescent_s=999.0, steady_quiescent_s=999.0),
            stop=lambda: False,
            poll_interval_s=0.01,
        )
        assert isinstance(task, asyncio.Task)
        assert "antigravity-stuck-supervisor" in (task.get_name() or "")
        # Let it tick once, then cancel — it must unwind cleanly.
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
