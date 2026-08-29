"""
Regression e2e test: unreachable server must not crash the CLI.

``omnigent run --server <url>`` (and a bare ``omnigent run`` with a
``server:`` recorded in ``~/.omnigent/config.yaml`` by ``omnigent
login``) crashes with a raw ``httpx.ConnectError`` traceback + the
crash-handler screen when the server URL is unreachable — a DNS
failure (``[Errno 8] nodename nor servname provided, or not known``
on macOS, ``[Errno -3] Temporary failure in name resolution`` on
Linux) or a refused connection.

The failing call path is::

    run → _dispatch_run → run_chat → _chat_with_server
        → _pick_agent → httpx.get(f"{base_url}/v1/sessions")

``_pick_agent`` has no ``except (httpx.ConnectError, ...)`` guard, so
the transport error escapes all the way to the crash handler and the
user is shown a crash screen + "File an issue" prompt for what is an
environment problem, not a bug. The sibling daemon path
(``_prepare_chat_session_via_daemon``) already catches exactly these
errors and re-raises ``click.ClickException(
_unreachable_server_message(base_url))`` — the clean, actionable
message this journey should produce too.

Journey (drives the REAL user command under a PTY, per the reporter):
  1. User has a stale/unreachable server URL — from ``omnigent login``
     recording it in config, or passed with ``--server``.
  2. User runs ``omnigent run`` (here: ``omnigent run --server <url>``
     with an unresolvable hostname / a closed loopback port).
  3. BUG: raw ``httpx.ConnectError`` traceback + crash screen
     ("Omnigent ran into an issue", crash report, file-an-issue link).
  4. EXPECTED (post-fix): a clean "Could not connect …" error naming
     the URL and how to recover, no crash screen.

Usage::

    python -m pytest tests/e2e/test_run_unreachable_server_crash_e2e.py -v
"""

from __future__ import annotations

import contextlib
import os
import socket
import sys
from pathlib import Path

import pytest

pexpect = pytest.importorskip("pexpect")

_REPO_ROOT = Path(__file__).resolve().parents[2]

# The clean, user-facing message _unreachable_server_message() builds —
# what this journey must print instead of crashing.
_CLEAN_MARKER = "Could not connect"

# Crash-handler chrome that must NOT appear for an unreachable server.
_CRASH_SCREEN_MARKER = "ran into an issue"
_CRASH_REPORT_MARKER = "crash report was saved"
_RAW_TRACEBACK_MARKER = "httpx.ConnectError"

_PROXY_ENV_VARS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)


def _free_loopback_port() -> int:
    """Reserve-and-release a loopback port so connecting to it is refused.

    :returns: A port number with no listener, e.g. ``54321``.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _cli_env(tmp_path: Path) -> dict[str, str]:
    """Build a clean env for the spawned ``omnigent`` CLI.

    - Fresh ``HOME`` with a persisted theme + ``auto_open_conversation:
      false`` so no first-run picker or browser tab blocks the PTY
      (same shape as ``tests/e2e/test_repl_approval_e2e.py``).
    - Proxy env stripped and ``NO_PROXY=*`` so httpx dials the target
      directly — a corporate proxy would turn the DNS failure into a
      ``ProxyError`` and mask the reported shape.
    - ``PYTHONPATH`` pinned to this repo root so ``python -m omnigent``
      resolves this worktree's code regardless of cwd.

    :param tmp_path: Pytest per-test temp dir for the fake HOME.
    :returns: Env mapping for ``pexpect.spawn``.
    """
    env: dict[str, str] = {**os.environ}
    for var in _PROXY_ENV_VARS:
        env.pop(var, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"

    fake_home = tmp_path / "home"
    config_home = fake_home / ".omnigent"
    config_home.mkdir(parents=True, exist_ok=True)
    (config_home / "config.yaml").write_text(
        "auto_open_conversation: false\ntui:\n  theme: dark\n"
    )
    env["HOME"] = str(fake_home)
    env["OMNIGENT_CONFIG_HOME"] = str(config_home)
    env["OMNIGENT_SKIP_ONBOARD"] = "1"
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["TERM"] = "xterm-256color"
    return env


@pytest.mark.parametrize(
    "server_url_factory",
    [
        # DNS failure — the reported shape ([Errno 8] on macOS,
        # [Errno -3] on Linux). ``.invalid`` is RFC 2606-reserved, so
        # resolution fails deterministically without network access.
        lambda: "https://unresolvable-omnigent-server.invalid",
        # Connection refused — the stopped-local-server shape of the
        # same unguarded call ([Errno 111] / [Errno 61]).
        lambda: f"http://127.0.0.1:{_free_loopback_port()}",
    ],
    ids=["dns_failure", "connection_refused"],
)
def test_run_unreachable_server_shows_clean_error_not_crash(
    server_url_factory,
    tmp_path: Path,
) -> None:
    """An unreachable ``--server`` must produce a clean error, not a crash.

    Spawns the real ``omnigent run --server <unreachable-url>`` under a
    pseudo-TTY (the reporter's interactive shape) and asserts the CLI
    prints the actionable "Could not connect …" message — never the
    crash-handler screen with the raw ``httpx.ConnectError`` traceback.

    Regression guard: ``_pick_agent`` called ``httpx.get`` without
    catching transport errors, so the DNS/connect failure hit the
    crash handler and users were asked to file a bug for a stale
    server URL.
    """
    server_url = server_url_factory()
    child = pexpect.spawn(
        sys.executable,
        ["-m", "omnigent", "run", "--server", server_url],
        env=_cli_env(tmp_path),
        cwd=str(_REPO_ROOT),
        encoding="utf-8",
        codec_errors="replace",
        dimensions=(40, 120),  # rows, cols
        timeout=90,
    )
    try:
        idx = child.expect(
            [
                _CLEAN_MARKER,
                _CRASH_SCREEN_MARKER,
                _RAW_TRACEBACK_MARKER,
                pexpect.EOF,
            ],
            timeout=90,
        )
        if idx in (1, 2):
            pytest.fail(
                "BUG: `omnigent run --server "
                f"{server_url}` crashed with the crash-handler screen / raw "
                "httpx.ConnectError traceback instead of a clean "
                "'Could not connect' error. Output tail:\n"
                f"{child.before}"
            )
        if idx == 3:
            pytest.fail(
                "CLI exited without printing either the clean 'Could not "
                "connect' message or the crash screen. Output:\n"
                f"{child.before}"
            )

        # Clean message seen — drain to exit and make sure no crash
        # chrome follows it (the crash screen prints the report path
        # after the traceback, so a late marker would betray the bug).
        with contextlib.suppress(pexpect.TIMEOUT):
            child.expect(pexpect.EOF, timeout=30)
        trailing = child.before or ""
        for marker in (
            _CRASH_SCREEN_MARKER,
            _CRASH_REPORT_MARKER,
            _RAW_TRACEBACK_MARKER,
        ):
            assert marker not in trailing, (
                f"Crash-handler output {marker!r} appeared after the clean "
                f"error message. Output tail:\n{trailing}"
            )
    finally:
        with contextlib.suppress(Exception):
            child.close(force=True)
