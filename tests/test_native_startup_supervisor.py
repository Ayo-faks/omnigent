"""Tests for the generic native stuck-on-unknown-prompt detector.

These exercise :class:`omnigent.native_startup_supervisor.StuckDetector` through
its three injectable seams (``capture``, ``idle_marker_present``, ``alive``) plus
an injected clock, so every branch of the quiescence + not-ready + alive logic is
unit-tested WITHOUT a live tmux server.

The load-bearing behaviour is that "stuck" is derived with NO prompt vocabulary:

    stuck = alive AND pane-quiescent AND idle-marker-absent  (past a threshold)

Scenarios:
- busy: the pane keeps changing → never stuck, clock resets each frame.
- ready: the idle marker is present → READY, even if the pane is unchanged.
- stuck: pane frozen, no marker, alive, past the threshold → STUCK.
- settling: pane frozen, no marker, but the threshold has not elapsed yet.
- dead: pane frozen, no marker, but the liveness probe fails → DEAD (not stuck).
- torn capture: a blank ("") read never fast-tracks STUCK.
- withdraw: after STUCK, a later idle marker returns READY (card can be cleared).
- cold-boot vs steady: the shorter cold-boot threshold trips before the longer
  steady one; ``mark_first_turn_complete`` switches to the steady threshold.
"""

from __future__ import annotations

import pytest

from omnigent.native_startup_supervisor import (
    NATIVE_TERMINAL_INPUT_PHASE,
    StuckDetector,
    SupervisorConfig,
    Verdict,
    native_terminal_input_params,
    run_supervisor,
    stuck_elicitation_id,
    terminal_tail,
)
from omnigent.server.schemas import ElicitationResult


class _Clock:
    """A manually advanced monotonic clock for deterministic threshold tests."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _detector(
    *,
    panes: list[str],
    idle_marker: str = "READY_MARKER",
    alive: bool = True,
    clock: _Clock | None = None,
    config: SupervisorConfig | None = None,
    cold_boot: bool = True,
) -> tuple[StuckDetector, _Clock]:
    """Build a detector whose ``capture`` returns *panes* in order (last repeats).

    :param panes: Pane frames returned by successive ``capture`` calls; once
        exhausted the final frame repeats (so a "frozen" pane is easy to model).
    :param idle_marker: Substring whose presence marks a pane as ready.
    :param alive: Fixed liveness value.
    :param clock: Optional shared clock (defaults to a fresh one).
    :param config: Optional thresholds (defaults to the module defaults).
    :param cold_boot: Initial cold-boot flag.
    :returns: The detector and its clock.
    """
    clk = clock or _Clock()
    frames = list(panes)

    def capture() -> str:
        if len(frames) > 1:
            return frames.pop(0)
        return frames[0] if frames else ""

    detector = StuckDetector(
        capture=capture,
        idle_marker_present=lambda pane: idle_marker in pane,
        alive=lambda: alive,
        config=config or SupervisorConfig(cold_boot_quiescent_s=10.0, steady_quiescent_s=60.0),
        cold_boot=cold_boot,
        time_source=clk,
    )
    return detector, clk


def test_busy_pane_never_stuck_and_resets_clock() -> None:
    """A pane that keeps changing is BUSY and never advances toward stuck."""
    clock = _Clock()
    detector, _ = _detector(panes=["frame-a"], clock=clock)
    # Distinct frames each tick.
    detector.capture = iter(["frame-a", "frame-b", "frame-c"]).__next__  # type: ignore[assignment]
    assert detector.observe() == Verdict.BUSY
    clock.advance(100.0)
    assert detector.observe() == Verdict.BUSY
    clock.advance(100.0)
    assert detector.observe() == Verdict.BUSY


def test_idle_marker_is_ready_even_if_unchanged() -> None:
    """A pane showing the idle marker is READY regardless of quiescence."""
    detector, clock = _detector(panes=["type here ❯ READY_MARKER"])
    assert detector.observe() == Verdict.READY
    clock.advance(1000.0)
    # Still ready — a settled composer is static but not stuck.
    assert detector.observe() == Verdict.READY


def test_frozen_pane_becomes_stuck_past_threshold() -> None:
    """A frozen, marker-less, alive pane is STUCK once the window elapses."""
    detector, clock = _detector(panes=["Choose a theme: (1) dark (2) light"])
    # First observation starts the quiescence clock.
    assert detector.observe() == Verdict.BUSY
    # Not yet past the 10s cold-boot threshold.
    clock.advance(5.0)
    assert detector.observe() == Verdict.SETTLING
    # Past the threshold now.
    clock.advance(6.0)
    assert detector.observe() == Verdict.STUCK


def test_frozen_pane_dead_is_not_stuck() -> None:
    """A frozen pane whose liveness probe fails is DEAD, not STUCK."""
    detector, clock = _detector(panes=["half-drawn crash output"], alive=False)
    assert detector.observe() == Verdict.BUSY
    clock.advance(30.0)
    assert detector.observe() == Verdict.DEAD


def test_torn_capture_never_fast_tracks_stuck() -> None:
    """A blank ("") capture is SETTLING — a torn read is not quiescence."""
    detector, clock = _detector(panes=[""])
    clock.advance(1000.0)
    assert detector.observe() == Verdict.SETTLING


def test_stuck_then_ready_allows_withdraw() -> None:
    """After STUCK, an idle marker reappearing returns READY so the card clears."""
    clock = _Clock()
    detector = StuckDetector(
        capture=iter(
            [
                "Choose a theme: (1) dark (2) light",  # frozen
                "Choose a theme: (1) dark (2) light",  # still frozen → stuck
                "welcome ❯ READY_MARKER",  # answered → composer mounted
            ]
        ).__next__,
        idle_marker_present=lambda pane: "READY_MARKER" in pane,
        alive=lambda: True,
        config=SupervisorConfig(cold_boot_quiescent_s=10.0, steady_quiescent_s=60.0),
        time_source=clock,
    )
    assert detector.observe() == Verdict.BUSY
    clock.advance(11.0)
    assert detector.observe() == Verdict.STUCK
    assert detector.observe() == Verdict.READY


def test_cold_boot_threshold_shorter_than_steady() -> None:
    """The steady threshold tolerates a quiet stretch that cold boot flags."""
    config = SupervisorConfig(cold_boot_quiescent_s=10.0, steady_quiescent_s=60.0)
    detector, clock = _detector(
        panes=["running a slow build…"], config=config, cold_boot=False
    )
    assert detector.observe() == Verdict.BUSY
    # 30s of quiet: past cold-boot's 10s, but under steady's 60s → still settling.
    clock.advance(30.0)
    assert detector.observe() == Verdict.SETTLING
    clock.advance(31.0)
    assert detector.observe() == Verdict.STUCK


def test_mark_first_turn_complete_switches_threshold() -> None:
    """Leaving the cold-boot window applies the longer steady threshold."""
    config = SupervisorConfig(cold_boot_quiescent_s=10.0, steady_quiescent_s=60.0)
    detector, clock = _detector(panes=["frozen"], config=config, cold_boot=True)
    assert detector.observe() == Verdict.BUSY
    detector.mark_first_turn_complete()
    # 15s: would be stuck under cold boot, but steady (60s) is not reached.
    clock.advance(15.0)
    assert detector.observe() == Verdict.SETTLING


# ── Pure helpers ─────────────────────────────────────────────────────────────


def test_stuck_elicitation_id_is_deterministic_and_epoch_scoped() -> None:
    """Same (session, epoch) → same id; a new epoch → a new id."""
    a = stuck_elicitation_id("conv_1", "boot")
    assert a == stuck_elicitation_id("conv_1", "boot")
    assert a.startswith("elicit_stuck_")
    assert a != stuck_elicitation_id("conv_1", "turn_2")
    assert a != stuck_elicitation_id("conv_2", "boot")


def test_terminal_tail_keeps_last_nonblank_lines() -> None:
    """The tail drops blank lines and keeps only the trailing window."""
    pane = "a\n\nb\n   \nc\nd"
    assert terminal_tail(pane, max_lines=2) == "c\nd"
    assert terminal_tail("   \n\n  ") == ""


def test_params_carry_tail_terminal_id_and_phase() -> None:
    """The card params expose the tail, terminal id, and matching phase."""
    params = native_terminal_input_params(
        pane="pick a theme: (1) dark (2) light", terminal_id="term_7"
    )
    dumped = params.model_dump()
    assert params.phase == NATIVE_TERMINAL_INPUT_PHASE
    assert dumped["terminal_tail"] == "pick a theme: (1) dark (2) light"
    assert dumped["terminal_id"] == "term_7"


def test_params_omit_terminal_id_when_unknown() -> None:
    """A missing terminal id is omitted so the card falls back to a hint."""
    dumped = native_terminal_input_params(pane="frozen", terminal_id=None).model_dump()
    assert "terminal_id" not in dumped


# ── Orchestration loop ───────────────────────────────────────────────────────


class _Recorder:
    """Collects the async side effects a supervisor run drives, in order.

    ``publish`` blocks on an :class:`asyncio.Event` so a test controls exactly
    when the (background) long-poll returns its verdict, mirroring production
    where the human answers at an arbitrary later tick.
    """

    def __init__(self, verdict: ElicitationResult | None) -> None:
        self.verdict = verdict
        self.statuses: list[str] = []
        self.published: list[str] = []
        self.withdrawn: list[str] = []
        self.injected: list[ElicitationResult] = []
        self.release = __import__("asyncio").Event()

    async def publish(self, eid, params):  # type: ignore[no-untyped-def]
        self.published.append(eid)
        await self.release.wait()
        return self.verdict

    async def withdraw(self, eid):  # type: ignore[no-untyped-def]
        self.withdrawn.append(eid)

    async def inject(self, result):  # type: ignore[no-untyped-def]
        self.injected.append(result)

    async def set_status(self, status):  # type: ignore[no-untyped-def]
        self.statuses.append(status)


def _panes_then_ready(frozen: str, ready: str, *, after: int):  # type: ignore[no-untyped-def]
    """Return a ``capture`` returning *frozen* for *after* calls, then *ready*."""
    calls = {"n": 0}

    def capture() -> str:
        calls["n"] += 1
        return frozen if calls["n"] <= after else ready

    return capture


@pytest.mark.asyncio
async def test_supervisor_surfaces_then_injects_when_card_answered() -> None:
    """STUCK sets waiting + publishes; an accept verdict is injected into the pane."""
    clock = _Clock()
    frozen = "Choose a theme: (1) dark (2) light"
    detector = StuckDetector(
        capture=lambda: frozen,
        idle_marker_present=lambda pane: "❯" in pane,
        alive=lambda: True,
        config=SupervisorConfig(cold_boot_quiescent_s=0.0, steady_quiescent_s=0.0),
        time_source=clock,
    )
    verdict = ElicitationResult(action="accept", content={"answer": "1"})
    rec = _Recorder(verdict)
    ticks = {"n": 0}

    async def controlled_sleep(_seconds: float) -> None:
        # Release the publish long-poll after the card has been surfaced, and
        # yield to the loop so the background publish task can actually run.
        ticks["n"] += 1
        if ticks["n"] == 2:
            rec.release.set()
        await __import__("asyncio").sleep(0)

    def stop() -> bool:
        # Stop as soon as the answer has been injected, so a forever-frozen fake
        # pane (real injection would change it) doesn't re-surface a fresh card.
        return bool(rec.injected)

    await run_supervisor(
        detector,
        session_id="conv_1",
        epoch="boot",
        capture_for_card=lambda: frozen,
        terminal_id="term_1",
        publish=rec.publish,
        withdraw=rec.withdraw,
        inject=rec.inject,
        set_status=rec.set_status,
        should_stop=stop,
        sleep=controlled_sleep,
    )
    assert rec.statuses[0] == "waiting"
    assert rec.published[0] == stuck_elicitation_id("conv_1", "boot")
    assert rec.injected == [verdict]


@pytest.mark.asyncio
async def test_supervisor_withdraws_when_answered_in_terminal() -> None:
    """A READY tick (answered in the terminal) withdraws the card, no injection."""
    clock = _Clock()
    frozen = "Choose a theme: (1) dark (2) light"
    ready = "welcome ❯ "
    # Frozen for the first 2 observe() calls (BUSY→STUCK), then READY.
    capture = _panes_then_ready(frozen, ready, after=2)
    detector = StuckDetector(
        capture=capture,
        idle_marker_present=lambda pane: "❯" in pane,
        alive=lambda: True,
        config=SupervisorConfig(cold_boot_quiescent_s=0.0, steady_quiescent_s=0.0),
        time_source=clock,
    )
    # publish never returns on its own — the terminal-side answer (READY) is what
    # resolves this episode, so its long-poll is cancelled by the withdraw path.
    rec = _Recorder(verdict=None)
    ticks = {"n": 0}

    async def controlled_sleep(_seconds: float) -> None:
        ticks["n"] += 1

    def stop() -> bool:
        return ticks["n"] >= 4

    await run_supervisor(
        detector,
        session_id="conv_1",
        epoch="boot",
        capture_for_card=lambda: frozen,
        terminal_id="term_1",
        publish=rec.publish,
        withdraw=rec.withdraw,
        inject=rec.inject,
        set_status=rec.set_status,
        should_stop=stop,
        sleep=controlled_sleep,
    )
    assert "waiting" in rec.statuses
    assert "running" in rec.statuses
    assert rec.withdrawn == [stuck_elicitation_id("conv_1", "boot")]
    assert rec.injected == []
