r"""UI journey: a large multi-line continuation turn delivered to a claude-native session.

OMNI-3699 / GitHub #4847 — "a paste whose commit is never confirmed is dropped
silently and latches the session at running".

After a first turn completes, a second (continuation) send with a large
multi-line prompt (> 1 937 chars, ≥ 14 newlines — the range that triggers the
paste-placeholder path in Claude Code's TUI) must:

1. Reach Claude Code's input box and be submitted.
2. Complete a turn (the assistant answers and the session returns to idle).
3. Surface the user message and assistant reply in the transcript.

The failure mode was a blind submit: ``inject_user_message`` polled for the
draft for 5 s, never saw it (the TUI was still consuming the large paste), then
sent a single un-verified Enter that was absorbed into the paste burst as a
newline — so the message sat unsent, the harness returned success, and the
session latched at ``status: "running"`` indefinitely.
"""

from __future__ import annotations

import logging
import uuid

import httpx
import pytest
from playwright.sync_api import Page, expect

from tests.e2e_ui.conftest import reset_mock_llm, set_fallback_mock_llm

from .test_message_render_parity import (
    _ASSISTANT,
    _USER,
    _WORKING,
    _ensure_chat_view,
    _item_text,
    _ordered_message_items,
    _select_view_mode,
    _send,
)

_log = logging.getLogger(__name__)

# Mock LLM responds instantly; budget covers native CLI boot + terminal attach.
_MOCK_TURN_TIMEOUT_MS = 60_000
# claude-native auto-launch + first-run pre-accept + WS attach.
_TERMINAL_READY_TIMEOUT_MS = 120_000
# How long each turn's transcript item is allowed to settle into the API.
_TRANSCRIPT_SETTLE_TIMEOUT_S = 30.0

# Must match the model set in the mock anthropic provider config written by the
# native_claude_mock_session fixture (conftest._CLAUDE_MOCK_MODEL).
_CLAUDE_MOCK_MODEL = "claude-sonnet-4-20250514"

_TERMINAL_VIEW = '[data-testid="terminal-view"]'

# The minimum first-line content length and newline count that triggers the
# paste-placeholder code path in Claude Code, and which the reported failures
# all shared.  The continuation prompt below is built to exceed both.
_MIN_CONTINUATION_LEN = 1_937
_MIN_CONTINUATION_NEWLINES = 14


def _open_terminal_view(page: Page) -> None:
    """Switch the session to Terminal view and wait for connection.

    :param page: The Playwright page, on the session's chat surface.
    """
    expect(page.get_by_test_id("view-mode-toggle")).to_be_visible(
        timeout=_TERMINAL_READY_TIMEOUT_MS
    )
    _select_view_mode(page, "Terminal")
    terminal = page.locator(_TERMINAL_VIEW).last
    expect(terminal).to_have_attribute(
        "data-state", "connected", timeout=_TERMINAL_READY_TIMEOUT_MS
    )


def _build_continuation_prompt(nonce: str) -> str:
    """Build a multi-line continuation prompt in the reported failing size range.

    The prompt must have > ``_MIN_CONTINUATION_LEN`` characters and
    > ``_MIN_CONTINUATION_NEWLINES`` newlines so Claude Code's TUI renders
    the paste as a ``[Pasted text #N +M lines]`` placeholder rather than
    verbatim text.

    :param nonce: Unique token embedded in the prompt so the test can
        confirm this specific message reached the transcript.
    :returns: The prompt string.
    """
    lines = [
        f"Continuation turn marker: {nonce}",
        "",
        "Please review the following implementation and provide a detailed assessment:",
        "",
    ]
    for i in range(1, 35):
        lines.append(
            f"Step {i:02d}: Examine the component at path omnigent/inner/claude_native_executor.py "
            f"and verify that the continuation-paste delivery path in inject_user_message "
            f"correctly polls for the draft before sending Enter."
        )
    prompt = "\n".join(lines)
    assert len(prompt) >= _MIN_CONTINUATION_LEN, (
        f"Prompt too short ({len(prompt)} chars); increase the step count."
    )
    assert prompt.count("\n") >= _MIN_CONTINUATION_NEWLINES, (
        f"Too few newlines ({prompt.count(chr(10))}); increase the step count."
    )
    return prompt


def _wait_for_transcript_message(
    page: Page,
    base_url: str,
    session_id: str,
    marker: str,
    *,
    role: str,
) -> None:
    """Block until the canonical transcript holds *marker* in a *role* message.

    :param page: The Playwright page (used for its polling sleep).
    :param base_url: Spawned server base URL.
    :param session_id: The session/conversation id.
    :param marker: Unique text the turn carried.
    :param role: ``"user"`` or ``"assistant"``.
    :raises AssertionError: If the marker never reaches such an item within
        ``_TRANSCRIPT_SETTLE_TIMEOUT_S``.
    """
    import time

    deadline = time.monotonic() + _TRANSCRIPT_SETTLE_TIMEOUT_S
    while time.monotonic() < deadline:
        items = _ordered_message_items(base_url, session_id)
        if any(item.get("role") == role and marker in _item_text(item) for item in items):
            return
        page.wait_for_timeout(500)
    raise AssertionError(
        f"{marker!r} never reached the canonical transcript as a {role!r} message "
        f"within {_TRANSCRIPT_SETTLE_TIMEOUT_S}s — the continuation turn was not delivered"
    )


@pytest.mark.nightly
@pytest.mark.timeout(300)
def test_native_claude_continuation_large_paste_delivered(
    page: Page,
    native_claude_mock_session: tuple[str, str],
    mock_llm_server_url: str,
) -> None:
    r"""A large multi-line continuation paste reaches Claude Code and gets answered.

    Exercises the reported failure path: the second (continuation) send uses a
    prompt that exceeds the paste-placeholder threshold
    (> ``_MIN_CONTINUATION_LEN`` chars, ≥ ``_MIN_CONTINUATION_NEWLINES`` newlines),
    so Claude Code's TUI shows it as ``[Pasted text #N +M lines]``.  The
    harness must poll until the draft is confirmed in the input box, then
    submit — not send a blind unverified Enter that gets absorbed into the
    paste burst while the TUI is still processing it.

    Failure: ``inject_user_message`` returns without confirming delivery
    (``draft_seen=False``), the session latches at ``status: "running"`` with
    no new message items, and the working indicator never clears.

    Pass: the assistant reply appears, the working indicator clears, and both
    the user message and the reply land in the canonical transcript.
    """
    base_url, session_id = native_claude_mock_session
    _log.info(
        "continuation-paste journey: base_url=%s session_id=%s", base_url, session_id
    )

    page.goto(f"{base_url}/c/{session_id}")
    _open_terminal_view(page)
    _ensure_chat_view(page)
    reset_mock_llm(mock_llm_server_url)

    nonces = [uuid.uuid4().hex[:8] for _ in range(2)]

    # ---- Turn 1: initial send (always works, establishes the session). ----
    user_1, token_1 = f"usr-1-{nonces[0]}", f"ast-1-{nonces[0]}"
    set_fallback_mock_llm(mock_llm_server_url, "default", token_1)
    set_fallback_mock_llm(mock_llm_server_url, _CLAUDE_MOCK_MODEL, token_1)
    _log.info("turn 1: sending initial prompt (user=%s token=%s)", user_1, token_1)
    _send(page, f"Turn 1 marker {user_1}. Reply with exactly: {token_1}")
    expect(page.locator(_ASSISTANT, has_text=token_1).first).to_be_visible(
        timeout=_MOCK_TURN_TIMEOUT_MS
    )
    expect(page.locator(_WORKING)).to_have_count(0, timeout=_MOCK_TURN_TIMEOUT_MS)
    expect(page.locator(_USER)).to_have_count(1, timeout=30_000)
    _log.info("turn 1: settled")

    # ---- Turn 2: large multi-line continuation (the OMNI-3699 failing path). ----
    user_2, token_2 = f"usr-2-{nonces[1]}", f"ast-2-{nonces[1]}"
    continuation = _build_continuation_prompt(user_2)
    _log.info(
        "turn 2: sending continuation (%d chars, %d newlines, user=%s token=%s)",
        len(continuation),
        continuation.count("\n"),
        user_2,
        token_2,
    )
    set_fallback_mock_llm(mock_llm_server_url, "default", token_2)
    set_fallback_mock_llm(mock_llm_server_url, _CLAUDE_MOCK_MODEL, token_2)
    _send(page, continuation)

    # The bug: when draft_seen=False the harness sends a blind Enter that is
    # absorbed into the paste burst.  The session stays at "running" and the
    # working indicator never clears.  The pass condition is that the working
    # indicator clears AND the reply is visible.
    expect(page.locator(_ASSISTANT, has_text=token_2).first).to_be_visible(
        timeout=_MOCK_TURN_TIMEOUT_MS,
    )
    expect(page.locator(_WORKING)).to_have_count(0, timeout=_MOCK_TURN_TIMEOUT_MS)
    expect(page.locator(_USER)).to_have_count(2, timeout=30_000)
    _log.info("turn 2: continuation settled (assistant reply visible)")

    # Canonical transcript must carry both the user continuation and the reply.
    _wait_for_transcript_message(page, base_url, session_id, user_2, role="user")
    _wait_for_transcript_message(page, base_url, session_id, token_2, role="assistant")
    _log.info("turn 2: user message and assistant reply confirmed in transcript")
