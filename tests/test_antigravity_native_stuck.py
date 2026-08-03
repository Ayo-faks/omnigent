"""Tests for the agy binding of the generic stuck-prompt supervisor.

These verify only the thin agy-specific binding in
:mod:`omnigent.antigravity_native_stuck` — the generic launcher, detector, and
transport are covered by ``tests/test_native_startup_launcher.py`` and
``tests/test_native_startup_supervisor.py``. Focus:

- ``start_agy_stuck_supervisor`` is a no-op (returns ``None``) when ``tmux.json``
  is not advertised yet (cold boot before the terminal exists).
- the agy idle-marker classifier treats BOTH agy footer markers as "ready"
  (idle composer or an active turn) and a marker-less pane as stuck-eligible.
"""

from __future__ import annotations

from pathlib import Path

from omnigent.antigravity_native_stuck import (
    _agy_idle_marker_present,
    start_agy_stuck_supervisor,
)


def test_start_supervisor_no_tmux_is_noop(tmp_path: Path) -> None:
    """With no ``tmux.json`` advertised, launching the supervisor is a no-op."""
    # ``client`` is never touched on this path, so a sentinel is fine.
    task = start_agy_stuck_supervisor(
        bridge_dir=tmp_path,
        session_id="conv_1",
        client=object(),  # type: ignore[arg-type]
    )
    assert task is None


def test_idle_marker_present_for_both_footers() -> None:
    """Either agy footer marker counts as ready (not stuck)."""
    assert _agy_idle_marker_present("some output\n? for shortcuts")
    assert _agy_idle_marker_present("thinking…\nesc to cancel")
    # A first-run wizard shows neither footer → not ready → stuck-eligible.
    assert not _agy_idle_marker_present("Choose a theme:\n(1) dark  (2) light")
