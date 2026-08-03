"""Generic stuck-on-unknown-prompt detector for tmux-driven native harnesses.

A native TUI harness (codex, claude, agy, …) can block on a first-run
interactive prompt Omnigent never anticipated — codex's light/dark theme
picker, a telemetry-consent question, a keybinding wizard. Omnigent drives the
CLI by pasting into its tmux pane and reading output back asynchronously, so
"the TUI is waiting for a keystroke we will never send" looks identical to "the
TUI is busy working": the paste path already returned, and the read path sees a
non-empty pane that never settles to idle. The session spins forever.

We cannot enumerate every prompt (new CLI versions add new wizards), so
pattern-matching known prompt strings is permanently incomplete. Instead this
module computes a signal that needs NO prompt vocabulary:

    stuck = alive AND pane-quiescent AND idle-marker-absent

held past a debounce threshold. The intuition:

* A working turn constantly repaints (spinner, streaming tokens), so it is
  never *quiescent* — the captured pane keeps changing.
* A TUI that is genuinely ready for a new turn renders its harness-specific
  idle/ready marker (``❯`` for claude, ``? for shortcuts`` for agy), so it is
  *not idle-marker-absent*.
* A boot-crashed CLI is not *alive* (its tmux pane is gone), which the caller
  distinguishes from stuck via the liveness probe.

Only a TUI frozen on a prompt is quiescent, alive, AND showing no idle marker.

The detector is deliberately seamed on callables so it is pure and
unit-testable without a live tmux server:

* ``capture`` — returns the current pane text (production wraps each bridge's
  ``_capture_pane``). ``""`` means a failed/torn capture and is NOT treated as
  quiescent (a blank read under a busy repaint must not look settled).
* ``idle_marker_present`` — classifies a pane as "ready for a new turn"
  (production wraps ``_claude_prompt_rendered`` / an ``_AGY_IDLE_MARKER`` check).
* ``alive`` — liveness probe (production wraps each bridge's ``_session_alive``).

The detector keeps only a rolling hash of the last pane and the monotonic time
that hash was first observed, so a caller ticks it once per existing poll
without adding its own timer.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum

from omnigent.server.schemas import ElicitationRequestParams, ElicitationResult

_logger = logging.getLogger(__name__)

# ── Elicitation surfacing ───────────────────────────────────────────────────
# The stuck TUI is surfaced as a harness-agnostic elicitation carrying the pane
# tail + the terminal id, so the web inline card can show what the CLI is asking
# and forward the answer as keystrokes over the existing terminal-attach
# WebSocket. The ``phase`` string is the discriminator the web card matches on.
NATIVE_TERMINAL_INPUT_PHASE = "native_terminal_input"
NATIVE_TERMINAL_INPUT_POLICY = "native_startup_supervisor"

# Length of the hex digest slice in the deterministic elicitation id — mirrors
# ``codex_native_elicitation`` / ``antigravity_native_interactions`` so all
# native harnesses produce ids of the same shape/cardinality.
_STUCK_ELICITATION_ID_DIGEST_LENGTH = 32

# Number of trailing non-blank pane lines carried into the card. Enough to show
# the prompt + its options (a theme picker, a y/n question) without shipping the
# whole scrollback. Mirrors claude's ``_TERMINAL_FAILURE_TAIL_LINES`` intent.
_STUCK_TAIL_LINES = 20

# Default debounce windows. Cold boot (the first turn after launch) is where the
# known first-run wizards live and there is no legitimate long-quiet work yet, so
# it trips faster. Steady state must tolerate a genuinely quiet long tool call
# (a slow build, a network fetch) that emits nothing to the pane for a while, so
# it waits longer before calling a frozen pane "stuck". Both are advisory —
# callers pass their own via :class:`SupervisorConfig` (wired to env knobs like
# ``HARNESS_TURN_TIMEOUT_S``).
_DEFAULT_COLD_BOOT_QUIESCENT_S = 12.0
_DEFAULT_STEADY_QUIESCENT_S = 90.0


class Verdict(Enum):
    """Outcome of one supervisor tick.

    :cvar BUSY: The pane changed since the last tick (repainting) — actively
        working, reset the quiescence clock.
    :cvar READY: The idle/ready marker is present — the TUI is idle and can
        take a new turn; not stuck.
    :cvar DEAD: The liveness probe failed — the pane is gone (boot crash / exit);
        the caller surfaces a failure, not a stuck card.
    :cvar SETTLING: Pane unchanged and no idle marker, but the quiescence
        threshold has not yet elapsed — keep watching.
    :cvar STUCK: Pane unchanged, no idle marker, alive, past the threshold —
        blocked on an unknown prompt. Surface the inline answer card.
    """

    BUSY = "busy"
    READY = "ready"
    DEAD = "dead"
    SETTLING = "settling"
    STUCK = "stuck"


@dataclass(frozen=True)
class SupervisorConfig:
    """Debounce thresholds for the stuck detector.

    :param cold_boot_quiescent_s: Seconds a pane must stay frozen (no idle
        marker) during the first turn after launch before it is called stuck.
    :param steady_quiescent_s: Seconds a pane must stay frozen mid-session
        before it is called stuck. Longer than cold boot to tolerate a quiet
        long-running tool call.
    """

    cold_boot_quiescent_s: float = _DEFAULT_COLD_BOOT_QUIESCENT_S
    steady_quiescent_s: float = _DEFAULT_STEADY_QUIESCENT_S


def _pane_hash(pane: str) -> str:
    """Return a stable digest of pane text for change detection.

    :param pane: Captured pane text.
    :returns: Hex digest; distinct panes hash differently.
    """
    return hashlib.sha256(pane.encode("utf-8")).hexdigest()


@dataclass
class StuckDetector:
    """Rolling quiescence + not-ready + alive detector for one native session.

    Tick it once per existing poll via :meth:`observe`. It holds only the last
    pane hash and the monotonic time that hash was first seen, so it needs no
    background timer of its own. A caller acts on a :attr:`Verdict.STUCK` result
    by surfacing the inline answer card, and clears the surfaced state when a
    later tick returns :attr:`Verdict.READY` (the prompt was answered → the idle
    marker reappeared).

    :param capture: Returns current pane text (``""`` on a failed/torn read).
    :param idle_marker_present: Classifies a pane as ready for a new turn.
    :param alive: Liveness probe for the tmux pane.
    :param config: Debounce thresholds.
    :param cold_boot: Whether this session is still in its first-turn window
        (uses the shorter threshold). The caller flips it to ``False`` once the
        first turn completes.
    :param time_source: Monotonic clock; injectable for tests.
    """

    capture: Callable[[], str]
    idle_marker_present: Callable[[str], bool]
    alive: Callable[[], bool]
    config: SupervisorConfig = field(default_factory=SupervisorConfig)
    cold_boot: bool = True
    time_source: Callable[[], float] = time.monotonic

    _last_hash: str | None = field(default=None, init=False)
    _quiescent_since: float | None = field(default=None, init=False)

    def _threshold_s(self) -> float:
        """Return the active quiescence threshold for the current phase."""
        return (
            self.config.cold_boot_quiescent_s
            if self.cold_boot
            else self.config.steady_quiescent_s
        )

    def observe(self) -> Verdict:
        """Take one reading and classify the session's current state.

        The order matters: a fresh idle marker means READY even if the pane also
        happens to be unchanged (an idle TUI's composer is static), so the marker
        check precedes the quiescence math. A changed pane is BUSY and resets the
        clock. Only an unchanged, marker-less, alive pane advances toward STUCK.

        :returns: The :class:`Verdict` for this tick.
        """
        pane = self.capture()

        # A torn/failed capture ("") is not evidence of quiescence — under a busy
        # repaint ``capture-pane`` can momentarily return blank. Leave the clock
        # untouched and treat it as still settling so a blank read never
        # fast-tracks a STUCK verdict.
        if not pane:
            return Verdict.SETTLING

        if self.idle_marker_present(pane):
            # Ready for input: the TUI settled to its composer, not a prompt.
            self._reset()
            return Verdict.READY

        current = _pane_hash(pane)
        now = self.time_source()
        if current != self._last_hash:
            # Repainting → actively working. Start (or restart) the clock at this
            # new frame; the pane must hold THIS content for the whole window.
            self._last_hash = current
            self._quiescent_since = now
            return Verdict.BUSY

        # Unchanged frame, no idle marker. Confirm the pane is still alive before
        # ruling on stuck — a dead pane is a crash/exit the caller reports
        # differently (and ``capture`` may lag a hair behind ``alive``).
        if not self.alive():
            self._reset()
            return Verdict.DEAD

        elapsed = now - (self._quiescent_since if self._quiescent_since is not None else now)
        if elapsed >= self._threshold_s():
            return Verdict.STUCK
        return Verdict.SETTLING

    def mark_first_turn_complete(self) -> None:
        """Leave the cold-boot window so later ticks use the steady threshold.

        Called by the caller when the first turn after launch finishes; the
        known first-run wizards can no longer appear, so subsequent quiet
        stretches get the more forgiving steady threshold.
        """
        self.cold_boot = False

    def _reset(self) -> None:
        """Clear the quiescence clock (READY/DEAD, or after the card is answered)."""
        self._last_hash = None
        self._quiescent_since = None


def stuck_elicitation_id(session_id: str, epoch: str) -> str:
    """Build the deterministic elicitation id for a stuck-prompt card.

    Deterministic over ``(session_id, epoch)`` so re-detecting the SAME stuck
    prompt re-parks the SAME card server-side (rather than spamming a new card
    per poll), while a genuinely new stuck episode gets a fresh id. ``epoch`` is
    a caller-chosen marker for "which stuck episode" — e.g. the boot marker or
    the active turn id — so a distinct block after the first is answered gets its
    own card. Mirrors
    :func:`omnigent.codex_native_elicitation.codex_elicitation_id`.

    :param session_id: Omnigent conversation id, e.g. ``"conv_abc123"``.
    :param epoch: Stuck-episode marker (boot marker / turn id), e.g. ``"boot"``.
    :returns: Stable id beginning with ``"elicit_stuck_"``.
    """
    payload = json.dumps(
        {"session_id": session_id, "epoch": epoch},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:_STUCK_ELICITATION_ID_DIGEST_LENGTH]
    return f"elicit_stuck_{digest}"


def terminal_tail(pane: str, *, max_lines: int = _STUCK_TAIL_LINES) -> str:
    """Return the last non-blank lines of a captured pane for the card.

    :param pane: Captured pane text.
    :param max_lines: Maximum trailing non-blank lines to keep.
    :returns: The joined tail, or ``""`` when the pane has no visible text.
    """
    lines = [line.rstrip() for line in pane.splitlines() if line.strip()]
    if not lines:
        return ""
    return "\n".join(lines[-max_lines:])


def native_terminal_input_params(
    *,
    pane: str,
    terminal_id: str | None,
) -> ElicitationRequestParams:
    """Build elicitation params for a stuck-on-unknown-prompt terminal card.

    The card is intentionally free-form: we do NOT know what the CLI is asking
    (that is the whole point of the generic detector), so we ship the pane tail
    verbatim and let the user answer with raw keystrokes over the terminal WS.
    ``terminal_id`` tells the web card which terminal-attach socket to send those
    keystrokes to; ``None`` degrades to a "open the terminal to answer" hint.

    :param pane: The captured (frozen) pane text.
    :param terminal_id: The session's terminal resource id for the attach WS, or
        ``None`` when unknown.
    :returns: ``ElicitationRequestParams`` the web UI renders as an inline card.
    """
    tail = terminal_tail(pane)
    # Both extra fields are strings, so a ``dict[str, str]`` splats cleanly into
    # ``ElicitationRequestParams`` (extra="allow") without needing ``Any``.
    extras: dict[str, str] = {"terminal_tail": tail}
    if terminal_id:
        extras["terminal_id"] = terminal_id
    return ElicitationRequestParams(
        mode="form",
        message=(
            "The CLI is waiting for input it asked for on screen (often a "
            "first-run prompt). Answer it below to continue."
        ),
        requestedSchema=None,
        url=None,
        phase=NATIVE_TERMINAL_INPUT_PHASE,
        policy_name=NATIVE_TERMINAL_INPUT_POLICY,
        **extras,
    )


# ── Orchestration ───────────────────────────────────────────────────────────
# The seamed async loop a forwarder ticks. Kept here (not in a forwarder) so the
# detect → surface → withdraw dance is unit-testable with fakes and shared across
# every tmux-driven harness. The forwarder supplies the detector plus three async
# seams: publish the card (returns the verdict / None), inject the answer as
# keystrokes, and set session status.

# Publishes the card under ``elicitation_id`` and long-poll-awaits the verdict;
# ``None`` on timeout/decline/withdraw (production: request_native_elicitation).
PublishElicitation = Callable[[str, ElicitationRequestParams], Awaitable[ElicitationResult | None]]
# Withdraws a previously-published card (production: resolve_native_elicitation).
WithdrawElicitation = Callable[[str], Awaitable[None]]
# Types the answer into the pane (production: keystrokes over the terminal WS).
InjectAnswer = Callable[[ElicitationResult], Awaitable[None]]
# Sets the session status edge, e.g. "waiting" / "running"
# (production: post_external_session_status bound to the client+session).
SetStatus = Callable[[str], Awaitable[None]]


async def run_supervisor(
    detector: StuckDetector,
    *,
    session_id: str,
    epoch: str,
    capture_for_card: Callable[[], str],
    terminal_id: str | None,
    publish: PublishElicitation,
    withdraw: WithdrawElicitation,
    inject: InjectAnswer,
    set_status: SetStatus,
    poll_interval_s: float = 1.0,
    should_stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Poll the detector; surface, answer, and withdraw a stuck-prompt card.

    One episode at a time. On the first :attr:`Verdict.STUCK` tick this sets the
    session ``waiting`` and starts a BACKGROUND :paramref:`publish` task (a
    long-poll that blocks until the human answers via the inline card). Polling
    continues while that task runs, because the answer can arrive out-of-band —
    the user may type into the embedded terminal directly, which the detector
    sees as the idle marker returning (:attr:`Verdict.READY`). Two resolution
    paths, whichever fires first:

    * **Answered via the card** — the publish task returns an ``accept`` verdict;
      its keystrokes are typed into the pane (:paramref:`inject`). Status returns
      to ``running`` once the detector next sees READY (composer remounted).
    * **Answered in the terminal / declined / timed out** — the detector returns
      READY (or the publish task returns ``None``); the card is withdrawn (which
      also makes any in-flight publish long-poll return) and status returns to
      ``running``.

    Running publish in the background (not inline) is what lets the READY path
    withdraw a card the user answered in the terminal — an inline ``await
    publish`` would block the poll loop until the card itself was answered, so a
    terminal-side answer would never clear it.

    A genuinely NEW stuck block after one resolves re-enters via a fresh STUCK
    verdict under the same deterministic id (the server re-parks it as the same
    card only while it is the same unanswered episode).

    :param detector: The per-session :class:`StuckDetector`.
    :param session_id: Omnigent conversation id.
    :param epoch: Stuck-episode marker for the deterministic id (boot / turn id).
    :param capture_for_card: Returns the current pane text for the card tail
        (usually the detector's own ``capture``).
    :param terminal_id: Terminal resource id for the answer WS (or ``None``).
    :param publish: Publishes the card + long-poll-awaits the verdict.
    :param withdraw: Withdraws the card.
    :param inject: Types the verdict into the pane.
    :param set_status: Sets the session status edge.
    :param poll_interval_s: Seconds between detector ticks.
    :param should_stop: Returns ``True`` to end the loop (forwarder shutdown).
    :param sleep: Async sleep seam (injectable for tests).
    :returns: None.
    """
    eid = stuck_elicitation_id(session_id, epoch)
    publish_task: asyncio.Task[ElicitationResult | None] | None = None
    try:
        while not should_stop():
            verdict = detector.observe()

            # A completed publish task means the card was answered inline. Type an
            # accept verdict into the pane; a decline/timeout/withdraw (``None``)
            # is handled by the READY path (or already withdrawn) — nothing to do.
            if publish_task is not None and publish_task.done():
                result = _publish_result(publish_task, session_id, eid)
                publish_task = None
                if result is not None and result.action == "accept":
                    await _safe_inject(inject, result, session_id, eid)

            if verdict is Verdict.READY and publish_task is not None:
                # Answered in the terminal directly (or the composer remounted
                # after an inline answer): clear the card — which also unblocks any
                # in-flight publish long-poll — and return to running.
                await withdraw(eid)
                await _cancel_publish(publish_task)
                publish_task = None
                await set_status("running")
            elif verdict is Verdict.STUCK and publish_task is None:
                await set_status("waiting")
                params = native_terminal_input_params(
                    pane=capture_for_card(), terminal_id=terminal_id
                )
                publish_task = asyncio.ensure_future(publish(eid, params))

            await sleep(poll_interval_s)
    finally:
        if publish_task is not None:
            await _cancel_publish(publish_task)


def _publish_result(
    task: asyncio.Task[ElicitationResult | None],
    session_id: str,
    eid: str,
) -> ElicitationResult | None:
    """Read a finished publish task's result, swallowing its errors.

    :param task: The completed publish task.
    :param session_id: Omnigent conversation id (for the log line).
    :param eid: Elicitation id (for the log line).
    :returns: The verdict, or ``None`` on cancellation / error.
    """
    if task.cancelled():
        return None
    exc = task.exception()
    if exc is not None:
        _logger.warning(
            "native stuck-prompt publish failed (session=%s, eid=%s): %r",
            session_id,
            eid,
            exc,
        )
        return None
    return task.result()


async def _safe_inject(
    inject: InjectAnswer,
    result: ElicitationResult,
    session_id: str,
    eid: str,
) -> None:
    """Type the verdict into the pane, logging (not raising) any failure.

    The user can still answer in the terminal, so a flaky pane must never crash
    the long-lived supervisor loop.
    """
    try:
        await inject(result)
    except Exception:  # noqa: BLE001 — boundary catch: a flaky pane must never
        # crash the long-lived supervisor loop; the user can still answer in the
        # terminal directly.
        _logger.warning(
            "native stuck-prompt answer injection failed (session=%s, eid=%s)",
            session_id,
            eid,
            exc_info=True,
        )


async def _cancel_publish(task: asyncio.Task[ElicitationResult | None]) -> None:
    """Cancel an in-flight publish task and await its teardown.

    Swallows both cancellation and any error the task raised while unwinding — the
    caller only needs it stopped, and this runs on the loop's cleanup paths where
    a raised teardown error would mask the real exit reason.

    :param task: The publish task to stop.
    :returns: None.
    """
    if not task.done():
        task.cancel()
    # ``CancelledError`` is a ``BaseException`` (not ``Exception``) since 3.8, so
    # both must be suppressed for the cancel we just issued.
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
