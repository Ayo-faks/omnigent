"""Guard: the terminal close codes stay reachable without FastAPI.

The 4xxx close codes are the wire contract between the server's
``/attach`` route, the runner, the native CLI client, and the browser.
They used to live in :mod:`omnigent.terminals.ws_bridge`, so the CLI's
``omnigent claude`` launch imported FastAPI (~120ms) and the tmux
registry (~100ms) just to compare an integer.

These tests pin the codes to their published values — a change here is a
protocol break that also needs the browser mirror updated — and pin the
import boundary that keeps them cheap to read.
"""

from __future__ import annotations

import subprocess
import sys

from omnigent.terminals import close_codes

# Mirrored in ``web/src/components/blocks/TerminalSession.ts``.
_PUBLISHED_CODES = {
    "WS_CLOSE_WRONG_REPLICA": 4400,
    "WS_CLOSE_TERMINAL_NOT_FOUND": 4404,
    "WS_CLOSE_TERMINAL_DETACHED": 4405,
    "WS_CLOSE_INTERNAL_ERROR": 4500,
}

_MUST_NOT_LOAD = (
    "fastapi",
    "omnigent.inner.terminal",
    "omnigent.terminals.registry",
    "omnigent.terminals.ws_bridge",
    "starlette",
)


def test_published_close_codes_are_stable() -> None:
    """These integers are on the wire — they cannot drift silently."""
    actual = {name: getattr(close_codes, name) for name in _PUBLISHED_CODES}
    assert actual == _PUBLISHED_CODES


def test_all_lists_every_code() -> None:
    """``__all__`` must not fall behind the module's contents."""
    assert sorted(close_codes.__all__) == sorted(_PUBLISHED_CODES)


def test_reading_a_close_code_does_not_import_the_bridge() -> None:
    """A CLI client must not pay for FastAPI to compare an integer."""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from omnigent.terminals.close_codes import WS_CLOSE_TERMINAL_NOT_FOUND\n"
            "import sys\n"
            f"print(sorted(m for m in {_MUST_NOT_LOAD!r} if m in sys.modules))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.strip() == "[]", (
        f"close_codes pulled in heavy modules: {proc.stdout.strip()}"
    )


def test_native_client_does_not_import_fastapi() -> None:
    """``omnigent claude``'s module graph must stay FastAPI-free."""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import omnigent.claude_native\nimport sys\n"
            "print(sorted(m for m in ('fastapi', 'starlette') if m in sys.modules))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.strip() == "[]", (
        f"claude_native imports a server framework: {proc.stdout.strip()}"
    )
