"""Pins that a running or waiting native terminal pane keeps the idle watchdog alive.

``_has_active_work()`` did not consult
``_native_pane_status``, so a Codex-native session whose terminal delivery was
live (status == "running") could be invisible to the watchdog and cause the
runner to shut down mid-turn.  A "waiting" pane (awaiting user elicitation)
must also block idle shutdown so the user can still respond.

Expected behaviour: ``app.state.has_active_work()`` returns ``True`` while any
session has native_pane_status == "running" or "waiting", and the inactivity
monitor waits until the pane settles before requesting shutdown.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from omnigent.runner import create_runner_app
from omnigent.runner._entry import _run_inactivity_monitor
from tests.runner.helpers import NullServerClient


def _scaffold_app():  # type: ignore[no-untyped-def]
    """Build a scaffold runner app (no harness process manager).

    :returns: Fresh FastAPI runner app.
    """
    return create_runner_app(server_client=NullServerClient())  # type: ignore[arg-type]


def _set_pane_status(app: object, conv_id: str, status: str) -> None:
    """Set native pane status and record a fresh activity timestamp."""
    app.state.native_pane_status[conv_id] = status  # type: ignore[attr-defined]
    app.state.native_pane_activity_at[conv_id] = time.monotonic()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_running_native_pane_blocks_idle_shutdown() -> None:
    """A native terminal pane with status 'running' prevents idle shutdown.

    The runner idle watchdog must consult
    ``native_pane_status`` so a long-running Codex-native turn keeps the
    runner alive even after that session is no longer in ``_active_turns``.

    :returns: None.
    """
    app = _scaffold_app()

    # Simulate a native terminal turn that is actively running — this is the
    # state a Codex-native session is in once terminal delivery takes over from
    # _active_turns.
    conv_id = "conv_native_pane_running_test"
    _set_pane_status(app, conv_id, "running")

    assert app.state.has_active_work() is True, (
        "has_active_work() must return True while native_pane_status == 'running'"
    )

    loop = asyncio.get_running_loop()
    shutdowns: list[str] = []

    # Run the monitor with a very short timeout (already expired) and confirm it
    # does NOT shut down while the pane is running.
    monitor = asyncio.create_task(
        _run_inactivity_monitor(
            idle_timeout_s=0.01,
            get_last_activity=lambda: loop.time() - 1.0,  # timeout already elapsed
            has_active_work=app.state.has_active_work,
            request_shutdown=lambda: shutdowns.append("shutdown"),
            poll_interval_s=0.005,
        )
    )

    # Wait long enough that the monitor would have fired if it ignored the pane.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(monitor), timeout=0.05)

    assert shutdowns == [], "Idle watchdog must NOT shut down while a native pane is running"
    assert not monitor.done()

    # Simulate the native turn finishing: pane transitions to "idle".
    _set_pane_status(app, conv_id, "idle")

    # Now has_active_work() should clear and the monitor should fire.
    await asyncio.wait_for(monitor, timeout=0.5)
    assert shutdowns == ["shutdown"], (
        "Idle watchdog should shut down once the native pane becomes idle"
    )


@pytest.mark.asyncio
async def test_idle_native_pane_does_not_block_shutdown() -> None:
    """A native pane with status 'idle' does not prevent idle shutdown.

    :returns: None.
    """
    app = _scaffold_app()

    conv_id = "conv_native_pane_idle_test"
    _set_pane_status(app, conv_id, "idle")

    assert app.state.has_active_work() is False

    loop = asyncio.get_running_loop()
    shutdowns: list[str] = []

    await asyncio.wait_for(
        _run_inactivity_monitor(
            idle_timeout_s=0.01,
            get_last_activity=lambda: loop.time() - 1.0,
            has_active_work=app.state.has_active_work,
            request_shutdown=lambda: shutdowns.append("shutdown"),
            poll_interval_s=0.001,
        ),
        timeout=0.3,
    )
    assert shutdowns == ["shutdown"]


@pytest.mark.asyncio
async def test_waiting_native_pane_blocks_idle_shutdown() -> None:
    """A native pane with status 'waiting' (pending elicitation) also pins the runner.

    The 'waiting' status means the pane is awaiting user input; the runner
    must stay alive so the user can respond.

    :returns: None.
    """
    app = _scaffold_app()

    conv_id = "conv_native_pane_waiting_test"
    _set_pane_status(app, conv_id, "waiting")

    assert app.state.has_active_work() is True, (
        "has_active_work() must return True while native_pane_status == 'waiting'"
    )

    loop = asyncio.get_running_loop()
    shutdowns: list[str] = []

    monitor = asyncio.create_task(
        _run_inactivity_monitor(
            idle_timeout_s=0.01,
            get_last_activity=lambda: loop.time() - 1.0,
            has_active_work=app.state.has_active_work,
            request_shutdown=lambda: shutdowns.append("shutdown"),
            poll_interval_s=0.005,
        )
    )

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(monitor), timeout=0.05)

    assert shutdowns == []

    # Pane settles — runner may now idle out.
    _set_pane_status(app, conv_id, "idle")

    await asyncio.wait_for(monitor, timeout=0.5)
    assert shutdowns == ["shutdown"]


@pytest.mark.asyncio
async def test_absent_native_pane_status_is_idle_eligible() -> None:
    """A session with no native_pane_status entry is not considered active work.

    :returns: None.
    """
    app = _scaffold_app()
    # No entry in native_pane_status at all — should not pin the runner.
    assert app.state.has_active_work() is False
