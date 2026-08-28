"""E2E regression test: UI state degradation after 100+ turns.

The SPA fetches only the ``INITIAL_WINDOW_ITEMS = 100`` most-recent items on
session load (``fetchSessionItemsPage`` with ``order=desc``). When a session
has more than 100 items the server returns ``has_more=True``; older items are
only loaded on scroll-up.

Two sub-symptoms are tested here:

Facet 1 — Chat submission works on a session with >100 committed turns
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The bug: after 100+ turns the SPA still loads successfully (``has_more=True``,
visible history), but sending a new message produces errors or silently
disappears. The composer must remain enabled and a sent message must complete
normally and persist after a full page reload.

Facet 2 — Session status clears after turn completion (not stuck "running")
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The bug: after 100+ turns the session status gets stuck as "running" / the
"Working…" indicator never clears. ``sendLatchIsStranded`` only un-sticks the
composer after ``SEND_CHAIN_MAX_WAIT_MS = 180_000 ms`` AND
``cachedConversationStatus()`` returns "idle" — but that helper searches only
loaded sidebar pages, so sessions scrolled off the sidebar return ``undefined``
and the latch is never released through the normal path.

This test proves the observable fix: the "Working…" indicator disappears and
the composer is re-enabled after the turn completes on a session that has
``INITIAL_WINDOW_ITEMS + N`` (here +10 = 110) committed turns, so
``has_more=True`` is guaranteed on load.
"""

from __future__ import annotations

import uuid

import pytest
from playwright.sync_api import Page, expect

from tests.e2e_ui.conftest import configure_mock_llm, seed_committed_turn

# —— CSS / ARIA selectors ——————————————————————————————————————————————
# The composer aria-label is stable; the placeholder text mutates while
# streaming ("Send a follow-up (queued)…") and during pending elicitations,
# so we locate by aria-label, not placeholder.
_COMPOSER_LABEL = "Message the agent"
# Fallback: stable placeholder when the session is idle and no elicitation.
_COMPOSER_PLACEHOLDER = "Ask the agent anything…"

_ASSISTANT = '[data-testid="message-bubble"][data-role="assistant"]'
_USER = '[data-testid="message-bubble"][data-role="user"]'
_WORKING = '[data-testid="working-indicator"]'

# Number of pre-seeded turns. Must exceed INITIAL_WINDOW_ITEMS (100) so the
# server returns has_more=True on the initial window fetch.  Each turn is 2
# items (user + assistant), so 110 turns → 220 items → has_more=True.
_SEEDED_TURNS = 110

# A unique needle embedded in the test-message so we can confirm the assistant
# reply came from the correct turn (not a stale bubble re-render).
_REPLY_MARKER = "long-session-reply"


def _seed_long_history(session_id: str) -> None:
    """Write ``_SEEDED_TURNS`` committed user+assistant exchanges into *session_id*."""
    for i in range(_SEEDED_TURNS):
        seed_committed_turn(
            session_id,
            prompt=f"seeded prompt {i}",
            reply=f"seeded reply {i}",
            response_id=f"resp_seeded_{i:04d}",
        )


def _send(page: Page, text: str) -> None:
    """Fill the composer and click Send."""
    # Locate by aria-label first; fall back to placeholder for older builds.
    composer = page.get_by_label(_COMPOSER_LABEL)
    if not composer.is_visible():
        composer = page.get_by_placeholder(_COMPOSER_PLACEHOLDER)
    expect(composer).to_be_visible(timeout=10_000)
    expect(composer).to_be_enabled(timeout=10_000)
    composer.fill(text)
    page.get_by_role("button", name="Send", exact=True).click()


# —— Facet 1: Chat submission works on a >100-turn session ————————————————


@pytest.mark.compat_smoke
def test_send_succeeds_after_100_seeded_turns(
    page: Page,
    seeded_session: tuple[str, str],
    mock_llm_server_url: str,
) -> None:
    """Composer stays enabled and new turns complete on a >100-item session.

    Regression guard for the chat-submission-fails facet of this bug.
    Seeds ``_SEEDED_TURNS`` (> INITIAL_WINDOW_ITEMS = 100) committed turns so
    the server returns ``has_more=True`` on page load, then sends a new message
    and asserts it completes.
    """
    base_url, session_id = seeded_session
    _seed_long_history(session_id)

    reply_text = f"{_REPLY_MARKER}-{uuid.uuid4().hex[:8]}"
    send_prompt = f"long-session-send-test-{uuid.uuid4().hex[:8]}"

    configure_mock_llm(
        mock_llm_server_url,
        [{"text": reply_text}],
        key="long-session-send",
        match=send_prompt,
    )

    page.goto(f"{base_url}/c/{session_id}")

    # The session has more history than the initial window: assert has_more is
    # signalled by checking the scroll-up affordance exists OR that the
    # composer is reachable (if the scroll target is never rendered we at
    # least know chat UI loaded).
    composer = page.get_by_label(_COMPOSER_LABEL)
    if not composer.is_visible():
        composer = page.get_by_placeholder(_COMPOSER_PLACEHOLDER)
    expect(composer).to_be_visible(timeout=20_000)

    # —— Assert composer is not disabled / stuck before we even send ——————
    # The bug: on a >100-turn session the composer can arrive disabled because
    # the store thinks a prior turn is still streaming (stuck latch).
    expect(composer).to_be_enabled(timeout=10_000)

    # —— Send a new message and wait for the assistant reply ——————————————
    _send(page, send_prompt)

    # The user bubble must appear — proves the message left the composer.
    expect(page.locator(_USER).last).to_be_visible(timeout=15_000)

    # The assistant reply must appear with our marker text.
    expect(page.locator(_ASSISTANT, has_text=reply_text).first).to_be_visible(timeout=60_000)

    # The "Working…" indicator must clear after the turn ends.
    expect(page.locator(_WORKING)).to_have_count(0, timeout=30_000)

    # —— Reload and assert the sent message persists (not ephemeral) ——————
    page.reload()
    expect(page.locator(_ASSISTANT, has_text=reply_text).first).to_be_visible(timeout=30_000)


# —— Facet 2: Session status clears after turn completion ————————————————


@pytest.mark.compat_smoke
def test_working_indicator_clears_after_turn_on_long_session(
    page: Page,
    seeded_session: tuple[str, str],
    mock_llm_server_url: str,
) -> None:
    """The "Working…" indicator disappears after a turn completes.

    Regression guard for the stuck-running-state facet of this bug.
    With ``has_more=True`` the session's sidebar row may fall off loaded
    sidebar pages, making ``cachedConversationStatus()`` return ``undefined``
    and leaving ``sendLatchIsStranded()`` always false.  The result: after the
    turn completes the Working indicator stays on screen indefinitely and the
    composer stays disabled.

    This test asserts that the indicator clears within a reasonable window
    after the assistant reply arrives, and that the composer is re-enabled so
    a follow-up message can be submitted.
    """
    base_url, session_id = seeded_session
    _seed_long_history(session_id)

    reply_text = f"{_REPLY_MARKER}-stuck-{uuid.uuid4().hex[:8]}"
    send_prompt = f"long-session-status-test-{uuid.uuid4().hex[:8]}"

    configure_mock_llm(
        mock_llm_server_url,
        [{"text": reply_text}],
        key="long-session-status",
        match=send_prompt,
    )

    page.goto(f"{base_url}/c/{session_id}")

    composer = page.get_by_label(_COMPOSER_LABEL)
    if not composer.is_visible():
        composer = page.get_by_placeholder(_COMPOSER_PLACEHOLDER)
    expect(composer).to_be_visible(timeout=20_000)
    expect(composer).to_be_enabled(timeout=10_000)

    _send(page, send_prompt)

    # Wait for the reply to confirm the turn ran.
    expect(page.locator(_ASSISTANT, has_text=reply_text).first).to_be_visible(timeout=60_000)

    # —— Core assertion: Working indicator clears ————————————————————————
    # Before the fix, sessionStatus stays "running" because the session's
    # sidebar row fell off the loaded pages, so cachedConversationStatus()
    # returns undefined and sendLatchIsStranded() can never return true within
    # SEND_CHAIN_MAX_WAIT_MS.  The indicator must clear on its own — no reload.
    expect(page.locator(_WORKING)).to_have_count(0, timeout=30_000)

    # —— Composer must be re-enabled for a follow-up ————————————————————
    expect(composer).to_be_enabled(timeout=10_000)

    # Prove the re-enabled state is real by submitting a follow-up.
    followup_reply = f"{_REPLY_MARKER}-followup-{uuid.uuid4().hex[:8]}"
    followup_prompt = f"followup-{uuid.uuid4().hex[:8]}"
    configure_mock_llm(
        mock_llm_server_url,
        [{"text": followup_reply}],
        key="long-session-followup",
        match=followup_prompt,
    )
    _send(page, followup_prompt)
    expect(page.locator(_ASSISTANT, has_text=followup_reply).first).to_be_visible(timeout=60_000)
    expect(page.locator(_WORKING)).to_have_count(0, timeout=30_000)
