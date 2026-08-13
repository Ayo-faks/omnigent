"""E2E: switching between two already-open sessions shows no loading gate.

Keeping conversation streams open in the background (#4113) means that once a
conversation has been opened, switching back to it repaints instantly from the
retained stream. The ``loadingConversation`` gate — which renders the
"Loading conversation…" ``HydratingPlaceholder`` and unmounts the whole chat
surface (``ChatPage.tsx``) — only arms for a COLD entry; a live (background-kept)
entry is painted with no placeholder, which is the entire point of the feature.

This locks that in: seed two sessions with committed history, cold-open both so
each stream is retained (slot capacity is >= 3, so neither evicts the other),
then switch back and forth and assert the hydrating placeholder never appears
while the destination transcript swaps in.

No LLM turn is needed — both transcripts are seeded as committed history via
``seed_committed_turn`` — so the test is deterministic and never waits on a
model.
"""

from __future__ import annotations

import json
import uuid

import httpx
from playwright.sync_api import Page, expect

from tests.e2e_ui.conftest import seed_committed_turn

# Substring of the HydratingPlaceholder copy (ChatPage.tsx). Matched as a
# substring so the trailing ellipsis character can't break the assertion.
_PLACEHOLDER = "Loading conversation"


def _title(base_url: str, session_id: str, title: str) -> None:
    """Give a session a stable title via ``PATCH /v1/sessions/{id}``.

    Only so the sidebar row is legible in a trace; the switch targets rows by
    ``href``, not title.
    """
    resp = httpx.patch(
        f"{base_url}/v1/sessions/{session_id}",
        json={"title": title},
        timeout=10.0,
    )
    resp.raise_for_status()


def _arm_placeholder_watch(page: Page) -> None:
    """Count every appearance of the hydrating placeholder into a page global.

    A post-hoc ``to_have_count(0)`` can miss a placeholder that flashed for a
    frame and cleared before the assertion ran; a ``MutationObserver`` catches
    it even then. The counter is reset right before each warm switch, so only a
    flash during that switch is counted. Installed as an init script so the one
    observer survives the SPA's client-side navigations (no full reload).
    """
    page.add_init_script(
        f"""
        (() => {{
          window.__hydrationFlashes = 0;
          const marker = {json.dumps(_PLACEHOLDER)};
          const check = () => {{
            if (document.body && document.body.innerText.includes(marker)) {{
              window.__hydrationFlashes += 1;
            }}
          }};
          const start = () => new MutationObserver(check).observe(
            document.documentElement,
            {{ childList: true, subtree: true, characterData: true }},
          );
          if (document.documentElement) start();
          else document.addEventListener("DOMContentLoaded", start);
        }})();
        """
    )


def _switch_to(page: Page, base_url: str, session_id: str) -> None:
    """Click the sidebar row for ``session_id`` and wait for the route to land."""
    page.locator(f'a[href="/c/{session_id}"]').click()
    expect(page).to_have_url(f"{base_url}/c/{session_id}")


def test_switching_between_open_sessions_shows_no_loading_gate(
    page: Page,
    seeded_session_pair: tuple[str, str, str],
) -> None:
    """Warm both sessions, then assert every switch repaints with no placeholder."""
    base_url, session_a, session_b = seeded_session_pair
    marker = uuid.uuid4().hex[:8]
    reply_a = f"alpha reply {marker}"
    reply_b = f"bravo reply {marker}"
    seed_committed_turn(session_a, prompt="ping a", reply=reply_a)
    seed_committed_turn(session_b, prompt="ping b", reply=reply_b)
    _title(base_url, session_a, f"switch-a-{marker}")
    _title(base_url, session_b, f"switch-b-{marker}")

    _arm_placeholder_watch(page)
    page.set_viewport_size({"width": 1280, "height": 720})

    # Cold-open A, then B. Each cold open legitimately shows the placeholder;
    # the point is that after both are open their streams stay live.
    page.goto(f"{base_url}/c/{session_a}")
    log = page.get_by_role("log")
    expect(log.get_by_text(reply_a, exact=True)).to_be_visible(timeout=30_000)
    # Both rows must be in the sidebar to be switch targets.
    expect(page.locator(f'a[href="/c/{session_b}"]')).to_be_visible(timeout=30_000)

    _switch_to(page, base_url, session_b)
    expect(log.get_by_text(reply_b, exact=True)).to_be_visible(timeout=30_000)

    # Both are warm now: every switch must swap the transcript instantly with
    # no hydrating placeholder in between.
    for target, reply, other in (
        (session_a, reply_a, reply_b),
        (session_b, reply_b, reply_a),
        (session_a, reply_a, reply_b),
    ):
        page.evaluate("window.__hydrationFlashes = 0")
        _switch_to(page, base_url, target)
        # The destination transcript is on screen and the previous one is gone,
        # proving a real swap (not both lingering) happened.
        expect(log.get_by_text(reply, exact=True)).to_be_visible(timeout=10_000)
        expect(log.get_by_text(other, exact=True)).to_have_count(0)
        flashes = page.evaluate("window.__hydrationFlashes")
        assert flashes == 0, (
            f"the 'Loading conversation…' placeholder flashed {flashes} time(s) "
            f"switching to a warm session ({target}); background streams should "
            "make the switch instant"
        )

    # And it is not lingering on screen at rest either.
    expect(page.get_by_text(_PLACEHOLDER, exact=False)).to_have_count(0)
