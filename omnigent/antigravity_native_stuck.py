"""Antigravity (agy) binding for the generic stuck-prompt supervisor.

The detect → surface → answer → withdraw machinery is harness-agnostic and lives
in :mod:`omnigent.native_startup_launcher` (wiring),
:mod:`omnigent.native_startup_supervisor` (detector), and
:mod:`omnigent.native_startup_elicitation` (transport). This module is the thin
agy-specific binding: it locates agy's tmux pane (``tmux.json``) and names agy's
idle-marker so the generic launcher can watch it. Any other tmux-driven harness
(claude, cursor, goose, …) adds an equivalent ~15-line binding rather than
duplicating the loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import httpx

from omnigent.antigravity_native_bridge import (
    _AGY_ACTIVE_MARKER,
    _AGY_IDLE_MARKER,
    read_tmux_info,
)
from omnigent.native_startup_launcher import start_stuck_supervisor
from omnigent.native_startup_supervisor import SupervisorConfig


def _agy_idle_marker_present(pane: str) -> bool:
    """Return whether the agy composer is ready for a new turn.

    EITHER footer marker means the input box is mounted and NOT blocked on a
    surprise prompt: :data:`_AGY_IDLE_MARKER` (idle, ready) or
    :data:`_AGY_ACTIVE_MARKER` (a turn is running — also "not stuck on an
    unexpected prompt", since agy is actively working). A first-run wizard that
    pre-empts the composer shows neither, which is exactly the stuck signal.

    :param pane: Captured pane text.
    :returns: ``True`` when a footer marker is present.
    """
    return _AGY_IDLE_MARKER in pane or _AGY_ACTIVE_MARKER in pane


def start_agy_stuck_supervisor(
    *,
    bridge_dir: Path,
    session_id: str,
    client: httpx.AsyncClient,
    epoch: str = "boot",
    stop: Callable[[], bool] | None = None,
    config: SupervisorConfig | None = None,
    poll_interval_s: float = 1.0,
) -> asyncio.Task[None] | None:
    """Launch the agy stuck-prompt supervisor over the generic launcher.

    Returns ``None`` (a no-op) when ``tmux.json`` is not yet advertised — the
    caller runs before the terminal exists on a cold boot, so there is nothing to
    watch. The reader retries binding each run, so a later launch picks it up.

    :param bridge_dir: Native agy bridge directory (locates ``tmux.json``).
    :param session_id: Omnigent conversation id.
    :param client: The reader's HTTP client (reused for hook + event posts).
    :param epoch: Stuck-episode marker for the deterministic elicitation id.
    :param stop: Predicate to end the loop (reused from the reader's ``stop``).
    :param config: Optional detector thresholds (defaults to module defaults).
    :param poll_interval_s: Seconds between detector ticks.
    :returns: The created task, or ``None`` when no tmux pane is advertised.
    """
    tmux = read_tmux_info(bridge_dir)
    if tmux is None:
        return None
    return start_stuck_supervisor(
        socket_path=tmux["socket_path"],
        tmux_target=tmux["tmux_target"],
        idle_marker_present=_agy_idle_marker_present,
        session_id=session_id,
        client=client,
        harness="antigravity",
        epoch=epoch,
        stop=stop,
        config=config,
        poll_interval_s=poll_interval_s,
    )
