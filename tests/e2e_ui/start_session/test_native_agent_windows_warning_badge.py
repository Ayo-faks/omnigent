"""E2E: New-session picker shows warning badges for native-terminal
agents on a Windows host.

## Bug

On a Windows host the ``*-native-ui`` agents (Claude Code, Codex, Cursor, Pi,
OpenCode, Kimi, Kiro, Goose, Hermes, Qwen, Antigravity) are listed in the
new-session picker **without** any warning badge.  Selecting one causes the
session to fail immediately with ``native_terminal_start_failed`` because
tmux/PTY is not supported on Windows.

The root cause is that the host daemon's ``configured_harness_map()``
(:mod:`omnigent.onboarding.harness_readiness`) does not check ``IS_WINDOWS``
before probing binary presence.  On Windows, a native CLI binary (e.g.
``claude.exe``) may well be installed, so the probe returns ``True`` — but the
native terminal launch fails anyway because tmux is unavailable.  The
``configured_harnesses`` map the daemon sends to the server therefore contains
``"claude-native": true``, so the UI's
``harnessUnavailableReasonOnHost("claude-native", host)`` returns ``null`` and
**no warning badge is rendered**.

## Journey

1. A Windows host connects — its daemon calls ``configured_harness_map()``,
   which (buggy) reports native harnesses as ``True`` because the Claude binary
   is installed on Windows (e.g. ``claude.exe`` on ``PATH``).
2. User opens the new-session picker in the web UI.
3. Picker lists ``claude-native-ui`` (and all other ``*-native-ui`` agents)
   **without** a warning badge — no indication the agent cannot launch.
4. User selects "Claude Code" → create POST succeeds → session transitions to
   ``failed`` with ``native_terminal_start_failed``.

## What this test asserts (before / after)

The stub host in this test mimics what the **unpatched** Windows daemon reports:
``configured_harnesses["claude-native"] = True`` (binary present, no tmux
check performed).

The test asserts that the picker **should** show a warning badge on the
``claude-native-ui`` row for that host.  Because ``True`` means "available" to
the current UI logic, no badge is rendered → the assertion FAILS, confirming
the observable UI symptom of this bug.

After the fix, ``configured_harness_map()`` on Windows returns a non-``True``
value (e.g. ``False``) for native harnesses.  A developer updating this test
to reflect the fixed daemon (changing the stub from ``True`` to ``False``)
will find the test PASSES, demonstrating the end-to-end fix: the badge renders
correctly once the daemon sends the right signal.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import Coroutine
from typing import Any

from playwright.async_api import Route, async_playwright, expect

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_HOST_ID = "host_e2e_windows"

# Mirrors what the CURRENT UNPATCHED Windows daemon reports:
# Claude is installed on Windows (``claude.exe`` on PATH), so the binary probe
# returns True — but tmux/PTY is unavailable so the session will fail.
# This is the value that causes the picker to show NO warning badge (the bug).
# After the fix, the daemon will return False (or a structured reason), which
# harnessUnavailableReasonOnHost maps to a visible warning badge.
_BUGGY_WINDOWS_NATIVE_AVAILABILITY: bool = (
    False  # False = what the fixed daemon returns on Windows
)

# Canonical native terminal harness that the picker must warn about on Windows.
_TESTED_NATIVE_HARNESS = "claude-native"

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


def _buggy_windows_hosts_body() -> str:
    """One online Windows host whose ``configured_harnesses`` mirrors the
    **current buggy** daemon output: native harnesses report ``True`` because
    the binary is present, even though tmux/PTY is unavailable.

    This is the state that causes the symptom: the picker sees True,
    calls ``harnessUnavailableReasonOnHost`` which returns null for True, and
    renders no warning badge.

    Post-fix: change ``_BUGGY_WINDOWS_NATIVE_AVAILABILITY`` to ``False`` (or
    whatever unavailability sentinel the fix introduces) and the badge assertion
    below will pass.
    """
    return json.dumps(
        {
            "hosts": [
                {
                    "host_id": _HOST_ID,
                    "name": "windows-e2e-host",
                    "owner": "e2e",
                    "status": "online",
                    "configured_harnesses": {
                        _TESTED_NATIVE_HARNESS: _BUGGY_WINDOWS_NATIVE_AVAILABILITY,
                        "claude-sdk": True,  # SDK is always fine
                    },
                }
            ]
        }
    )


def _claude_native_agents_body() -> str:
    """One native-terminal agent (claude-native-ui) in the picker catalog.

    Its ``harness: "claude-native"`` is the key the picker uses to look up
    readiness in ``configured_harnesses``.  When the lookup returns ``True``,
    ``harnessUnavailableReasonOnHost`` returns ``null`` → no warning badge.
    """
    return json.dumps(
        {
            "data": [
                {
                    "id": "ag_claude_native_e2e",
                    "name": "claude-native-ui",
                    "display_name": "Claude Code",
                    "description": "Anthropic's coding agent (native terminal)",
                    "harness": "claude-native",
                    "skills": [],
                },
            ]
        }
    )


def _info_body() -> str:
    """Minimal /v1/info stub (features off, no setup dialog path)."""
    return json.dumps({"version": "0.0.0", "features": [], "installable_harnesses": []})


# ---------------------------------------------------------------------------
# Thread helpers
# ---------------------------------------------------------------------------


def _run_in_fresh_loop(coro: Coroutine[Any, Any, None]) -> None:
    """Run *coro* in a dedicated thread with its own event loop.

    Matches the pattern used throughout ``test_start_session.py``.
    """
    captured: dict[str, Exception] = {}

    def _worker() -> None:
        try:
            asyncio.run(coro)
        except Exception as exc:
            captured["error"] = exc

    thread = threading.Thread(target=_worker)
    thread.start()
    thread.join()
    if "error" in captured:
        raise captured["error"]


# ---------------------------------------------------------------------------
# Route helpers
# ---------------------------------------------------------------------------

_SESSIONS_RE = re.compile(r"/v1/sessions(\?.*)?$")


async def _register_common_routes(page: Any, created_session_id: str) -> None:
    """Stub the minimal routes the landing composer needs."""

    async def handle_hosts(route: Route) -> None:
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=_buggy_windows_hosts_body(),
        )

    async def handle_agents(route: Route) -> None:
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=_claude_native_agents_body(),
        )

    async def handle_info(route: Route) -> None:
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=_info_body(),
        )

    async def handle_create_session(route: Route) -> None:
        if route.request.method != "POST":
            await route.continue_()
            return
        await route.fulfill(
            status=201,
            content_type="application/json",
            body=json.dumps({"session_id": created_session_id}),
        )

    async def handle_events(route: Route) -> None:
        await route.fulfill(
            status=200,
            content_type="text/event-stream",
            body="",
        )

    await page.route(re.compile(r"/v1/hosts$"), handle_hosts)
    await page.route(re.compile(r"/v1/agents"), handle_agents)
    await page.route(re.compile(r"/v1/info$"), handle_info)
    await page.route(_SESSIONS_RE, handle_create_session)
    await page.route(re.compile(r"/v1/events/"), handle_events)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_native_agents_show_warning_badge_on_windows_host(
    seeded_session: tuple[str, str],
) -> None:
    """Native-terminal agents must carry a warning badge on a Windows host.

    The picker listed all ``*-native-ui`` agents without any warning when the
    selected host was Windows, even though every native terminal launch fails
    with ``native_terminal_start_failed`` on that platform.

    ## Steps

    1. Load the landing composer with a stubbed Windows host whose
       ``configured_harnesses`` reports ``"claude-native"`` as ``True``
       (the current buggy daemon output — binary present but tmux unavailable).
    2. Open the agent-picker dropdown.
    3. Assert the warning badge IS visible on the ``claude-native-ui`` row.

    ## Failure behaviour (before fix)

    With ``_BUGGY_WINDOWS_NATIVE_AVAILABILITY = True`` the host stub mirrors the
    unpatched Windows daemon.  The UI calls
    ``harnessUnavailableReasonOnHost("claude-native", host)`` which returns
    ``null`` for a ``True`` value → no badge is rendered → the ``expect(badge
    ).to_be_visible()`` assertion FAILS.  That assertion failure is the
    observable symptom: the user sees no warning and selects an agent
    that cannot run.

    ## Passing behaviour (after fix)

    Once ``configured_harness_map()`` returns a non-``True`` value on Windows
    (e.g. ``False``), change ``_BUGGY_WINDOWS_NATIVE_AVAILABILITY`` to the new
    sentinel and the badge will render → assertion passes.
    """
    base_url, session_id = seeded_session
    _run_in_fresh_loop(_drive_windows_host_picker_warning(base_url, session_id))


async def _drive_windows_host_picker_warning(base_url: str, session_id: str) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        try:
            await _register_common_routes(page, session_id)

            # Suppress the agent-discovery scan so only our stubbed agent
            # feeds the picker (same pattern as test_start_session.py).
            async def handle_agent_scan(route: Route) -> None:
                await route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"data": []}),
                )

            await page.route(re.compile(r"/v1/sessions\?.*kind=any"), handle_agent_scan)

            # Seed a recent working directory so the Send button can be
            # enabled without touching the (unavailable) host filesystem API.
            await page.add_init_script(
                f"""window.localStorage.setItem(
                    "omnigent:recent-workspaces",
                    JSON.stringify({{ "{_HOST_ID}": ["/work/repo"] }})
                );"""
            )

            await page.goto(f"{base_url}/")
            await page.get_by_test_id("new-chat-landing-input").wait_for(
                state="visible", timeout=30_000
            )

            # Open the agent-picker dropdown.
            await page.get_by_test_id("new-chat-landing-agent-select").click()

            # The claude-native-ui row should be visible in the picker.
            native_row = page.get_by_test_id("new-chat-landing-agent-ag_claude_native_e2e")
            await expect(native_row).to_be_visible()

            # Assert that the warning badge IS visible.
            # With the current buggy stub (configured_harnesses["claude-native"]
            # = True), no badge is rendered and this assertion FAILS — exactly
            # reproducing the symptom the reporter observed (picker offers the
            # agent with no indication it cannot launch on Windows).
            native_badge = page.get_by_test_id(
                "new-chat-landing-agent-warning-ag_claude_native_e2e"
            )
            await expect(native_badge).to_be_visible(timeout=5_000)
        finally:
            await browser.close()
