"""Generic launcher binding the stuck detector to ANY tmux-driven TUI harness.

:mod:`omnigent.native_startup_supervisor` is the pure detect → surface → answer →
withdraw loop; :mod:`omnigent.native_startup_elicitation` is its request/withdraw
transport. This module is the third generic piece: it binds those to a tmux pane
using only ``tmux`` subprocess calls (capture-pane / has-session / send-keys), so
it depends on NO harness-specific bridge. Every tmux-driven native harness
(antigravity, claude, cursor, goose, hermes, kimi, kiro, …) plugs in by supplying
just two things:

* its ``(socket_path, tmux_target)`` — where its TUI pane lives; and
* an ``idle_marker_present(pane) -> bool`` classifier — "is my composer mounted
  (ready or actively working), as opposed to blocked on a surprise prompt?".

Everything else — capturing the pane, the liveness probe, typing the answer's
keys, the elicitation round-trip, the ``waiting``/``running`` status edges — is
harness-agnostic and lives here. A per-harness module is then a ~15-line binding
(read its tmux info + name its idle marker), not a copy of this wiring.

Answer contract: the inline web card posts an ``ElicitationResult`` whose
``content["keys"]`` is a list of tmux key names, e.g. ``["1", "Enter"]`` to pick
option 1 of a theme picker, ``["y", "Enter"]`` for a yes/no, or the characters of
a free-text answer followed by ``"Enter"``.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from collections.abc import Callable

import httpx

from omnigent.native_startup_elicitation import (
    request_native_elicitation,
    resolve_native_elicitation,
)
from omnigent.native_startup_supervisor import (
    StuckDetector,
    SupervisorConfig,
    run_supervisor,
)
from omnigent.server.schemas import ElicitationRequestParams, ElicitationResult

_logger = logging.getLogger(__name__)

# Bound on each tmux subprocess call. Matches the native bridges' own
# ``_TMUX_SEND_TIMEOUT_S`` so a wedged tmux server can't stall the detector.
_TMUX_TIMEOUT_S = 5.0

# Classifies a captured pane as "ready for a new turn / actively working" (i.e.
# NOT blocked on a surprise prompt). Harness-supplied — the only harness-specific
# knowledge the launcher needs.
IdleMarkerClassifier = Callable[[str], bool]


def _capture_pane(socket_path: str, tmux_target: str) -> str:
    """Capture the visible pane text; ``""`` on any failure (not treated as idle).

    :param socket_path: tmux server socket path.
    :param tmux_target: tmux pane target.
    :returns: The pane text, or ``""`` when the capture fails / times out.
    """
    try:
        proc = subprocess.run(
            ["tmux", "-S", socket_path, "capture-pane", "-p", "-t", tmux_target],
            check=False,
            capture_output=True,
            text=True,
            timeout=_TMUX_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def _session_alive(socket_path: str, tmux_target: str) -> bool:
    """Return whether the tmux pane still exists (the TUI is still running).

    :param socket_path: tmux server socket path.
    :param tmux_target: tmux pane target.
    :returns: ``True`` when ``has-session`` reports the target exists.
    """
    try:
        proc = subprocess.run(
            ["tmux", "-S", socket_path, "has-session", "-t", tmux_target],
            check=False,
            capture_output=True,
            text=True,
            timeout=_TMUX_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def _send_keys(socket_path: str, tmux_target: str, keys: list[str]) -> None:
    """Type an ordered tmux key sequence into the pane as ONE ``send-keys`` call.

    Keys are interpreted by tmux (no ``-l``), so named keys like ``Enter`` /
    ``Escape`` work alongside literal characters. Sent in one invocation so a
    digit and its confirming ``Enter`` cannot be split across the pane's input
    handling.

    :param socket_path: tmux server socket path.
    :param tmux_target: tmux pane target.
    :param keys: Ordered tmux key arguments, e.g. ``["1", "Enter"]``.
    :raises RuntimeError: On a non-zero exit or timeout.
    """
    try:
        proc = subprocess.run(
            ["tmux", "-S", socket_path, "send-keys", "-t", tmux_target, *keys],
            check=False,
            capture_output=True,
            text=True,
            timeout=_TMUX_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"tmux send-keys timed out after {_TMUX_TIMEOUT_S:.0f}s") from exc
    except OSError as exc:
        raise RuntimeError(f"tmux could not be executed: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "<no output>"
        raise RuntimeError(f"tmux send-keys failed (rc={proc.returncode}): {detail}")


def keys_from_result(result: ElicitationResult) -> list[str]:
    """Extract the tmux key list from an accepted stuck-prompt verdict.

    :param result: The web-submitted verdict; ``content["keys"]`` is the ordered
        tmux key names to type.
    :returns: The key list (empty when malformed / absent — the caller then types
        nothing rather than guessing).
    """
    content = result.content
    if not isinstance(content, dict):
        return []
    keys = content.get("keys")
    if not isinstance(keys, list):
        return []
    return [k for k in keys if isinstance(k, str) and k]


def start_stuck_supervisor(
    *,
    socket_path: str,
    tmux_target: str,
    idle_marker_present: IdleMarkerClassifier,
    session_id: str,
    client: httpx.AsyncClient,
    harness: str,
    epoch: str = "boot",
    terminal_id: str | None = None,
    stop: Callable[[], bool] | None = None,
    config: SupervisorConfig | None = None,
    poll_interval_s: float = 1.0,
) -> asyncio.Task[None]:
    """Launch the stuck-prompt supervisor for a tmux-driven harness.

    Binds the pure detector + elicitation transport to the pane at
    ``(socket_path, tmux_target)`` and returns the running background task. The
    caller (a per-harness reader/forwarder) owns the task's lifetime — cancel it
    on teardown.

    :param socket_path: tmux server socket path for the harness's TUI pane.
    :param tmux_target: tmux pane target.
    :param idle_marker_present: Harness classifier — pane ready/working vs stuck.
    :param session_id: Omnigent conversation id.
    :param client: The reader's HTTP client (reused for hook + event posts).
    :param harness: Harness name for log lines / the task name, e.g.
        ``"antigravity"``.
    :param epoch: Stuck-episode marker for the deterministic elicitation id.
    :param terminal_id: Terminal resource id for the answer WS (or ``None``).
    :param stop: Predicate to end the loop (reused from the reader's ``stop``).
    :param config: Optional detector thresholds (defaults to module defaults).
    :param poll_interval_s: Seconds between detector ticks.
    :returns: The created background task.
    """

    def capture() -> str:
        return _capture_pane(socket_path, tmux_target)

    detector = StuckDetector(
        capture=capture,
        idle_marker_present=idle_marker_present,
        alive=lambda: _session_alive(socket_path, tmux_target),
        config=config or SupervisorConfig(),
    )

    async def publish(
        eid: str, params: ElicitationRequestParams
    ) -> ElicitationResult | None:
        return await request_native_elicitation(
            client, session_id, elicitation_id=eid, params=params
        )

    async def withdraw(eid: str) -> None:
        await resolve_native_elicitation(client, session_id, eid)

    async def inject(result: ElicitationResult) -> None:
        keys = keys_from_result(result)
        if not keys:
            _logger.info(
                "%s stuck-prompt verdict carried no keys to inject (session=%s)",
                harness,
                session_id,
            )
            return
        # tmux send-keys is blocking; off-load it so the loop is not stalled.
        await asyncio.to_thread(_send_keys, socket_path, tmux_target, keys)

    async def set_status(status: str) -> None:
        # Lazy import: keep this module importable from the lightweight CLI
        # without eagerly pulling the post-delivery helpers.
        from omnigent._native_post_delivery import post_external_session_status

        try:
            await post_external_session_status(
                client, session_id=session_id, status=status
            )
        except httpx.HTTPError:
            _logger.warning(
                "%s stuck-prompt status POST failed (session=%s, status=%s)",
                harness,
                session_id,
                status,
                exc_info=True,
            )

    async def _run() -> None:
        await run_supervisor(
            detector,
            session_id=session_id,
            epoch=epoch,
            capture_for_card=capture,
            terminal_id=terminal_id,
            publish=publish,
            withdraw=withdraw,
            inject=inject,
            set_status=set_status,
            poll_interval_s=poll_interval_s,
            should_stop=stop if stop is not None else (lambda: False),
        )

    return asyncio.create_task(_run(), name=f"{harness}-stuck-supervisor")
