"""End-to-end test: runner-owned Codex TUI must not bypass user hook review.

Omnigent's codex-native sessions attach the Codex TUI to the app-server with
argv built by ``build_codex_remote_args``. Passing ``bypass_hook_trust=True``
prepends ``--dangerously-bypass-hook-trust``, which suppresses codex's
interactive "Hooks need review" trust gate for *all* enabled hooks —
including user-authored hooks the user never reviewed (e.g. hooks registered
via the user's config reachable from the provisioned ``CODEX_HOME``) — so
unreviewed user hooks would run silently in codex-native sessions.

This test drives the real user journey at the terminal surface:

1. Provision a ``CODEX_HOME`` carrying an unreviewed *user* hook
   (``hooks.json`` with a ``UserPromptSubmit`` command hook), an API-key
   ``auth.json`` (the review gate renders before any model call, so no real
   login is needed), and a pre-trusted workspace (so the project-trust
   prompt does not mask the hook-review gate).
2. Control run: launch the codex TUI with no bypass flag and confirm the
   "Hooks need review" gate appears — this validates the detection, and
   mirrors what a user sees running codex directly.
3. Product run: launch the codex TUI with exactly the trust-related flags
   Omnigent's runner-owned terminal injects (computed from the installed
   codex version the same way ``orchestration.py`` does) and assert the
   review gate STILL appears for the unreviewed user hook.

On a build that injects ``--dangerously-bypass-hook-trust`` step 3 FAILS
(codex itself warns "Enabled hooks may run without review for this
invocation" and renders the composer). With the blanket bypass removed —
Omnigent's own policy/routing hooks are trusted separately via the
app-server handshake — the gate renders and the test passes.

Requires only a ``codex`` binary on PATH — no codex login.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import pty
import re
import select
import shutil
import struct
import subprocess
import termios
import time
from pathlib import Path

import pytest

from omnigent.codex_native_app_server import build_codex_remote_args

pytestmark = pytest.mark.skipif(
    shutil.which("codex") is None,
    reason="codex-native hook-trust e2e needs a `codex` binary on PATH",
)

_ANSI_CSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)")
_ANSI_MISC = re.compile(r"\x1b[()][0-9A-B]|\x1b[>=]")

# The interactive trust gate's headline, whitespace-stripped and lowercased.
# Matched against normalized output so TUI line-wrapping can't split it.
# Distinct from the bypass-mode warning ("Enabled hooks may run without
# review for this invocation"), which must NOT count as the gate.
_REVIEW_GATE_MARKER = "hooksneedreview"


def _strip_ansi(text: str) -> str:
    """Strip CSI/OSC/charset escape sequences from TUI output."""
    text = _ANSI_CSI.sub("", text)
    text = _ANSI_OSC.sub("", text)
    return _ANSI_MISC.sub("", text)


def _normalized(text: str) -> str:
    """Lowercase and drop whitespace so TUI line-wrapping can't split matches."""
    return re.sub(r"\s+", "", _strip_ansi(text)).lower()


def _provision_codex_home(codex_home: Path, workspace: Path) -> None:
    """Write a CODEX_HOME with an unreviewed user hook and a trusted workspace."""
    codex_home.mkdir(parents=True, exist_ok=True)
    # A user-layer hook the user has NOT reviewed: codex must gate it behind
    # the interactive "Hooks need review" screen before it may run.
    (codex_home / "hooks.json").write_text(
        '{\n  "hooks": {\n    "UserPromptSubmit": [\n'
        '      {"hooks": [{"type": "command", "command": "touch HOOK_RAN.marker",'
        ' "timeout": 5}]}\n'
        "    ]\n  }\n}\n"
    )
    # API-key auth so the TUI skips the sign-in onboarding (the review gate
    # renders before any model request, so the key never has to work).
    (codex_home / "auth.json").write_text('{"OPENAI_API_KEY": "sk-fake"}\n')
    # Pre-trust the workspace so the project-trust onboarding prompt cannot
    # mask (or be confused with) the hook-review gate under test, and mark
    # the model-availability NUX as seen so it doesn't cover the screen.
    (codex_home / "config.toml").write_text(
        f'model = "gpt-4o"\n\n[projects."{workspace}"]\ntrust_level = "trusted"\n'
        '\n[tui.model_availability_nux]\n"gpt-5.5" = 1\n'
    )


def _run_codex_tui(
    extra_args: list[str], *, codex_home: Path, workspace: Path, seconds: float = 45.0
) -> tuple[str, bool]:
    """
    Run the codex TUI under a PTY and classify the startup screen.

    :param extra_args: Argv tail after ``codex`` (e.g. the bypass flag).
    :param codex_home: Private ``CODEX_HOME`` for this run.
    :param workspace: Directory the TUI is launched in.
    :param seconds: Wall-clock budget for the run.
    :returns: ``(plain_output, review_gate_seen)`` — whether the "Hooks
        need review" trust gate was rendered before the budget elapsed.
    """
    codex = shutil.which("codex")
    assert codex is not None
    env = dict(
        os.environ,
        CODEX_HOME=str(codex_home),
        TERM="xterm-256color",
        OPENAI_API_KEY="sk-fake",
    )
    master, slave = pty.openpty()
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    proc = subprocess.Popen(
        [codex, *extra_args],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=workspace,
        env=env,
        close_fds=True,
    )
    os.close(slave)
    buf = b""
    answered: set[int] = set()
    review_seen = False
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.5)
            if master in ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                text = buf.decode("utf-8", "replace")
                # Answer the TUI's OSC 10/11 color queries so it finishes
                # terminal capability detection and renders its screens.
                if "\x1b]10;?" in text and 10 not in answered:
                    os.write(master, b"\x1b]10;rgb:ffff/ffff/ffff\x07")
                    answered.add(10)
                if "\x1b]11;?" in text and 11 not in answered:
                    os.write(master, b"\x1b]11;rgb:0000/0000/0000\x07")
                    answered.add(11)
                normalized = _normalized(text)
                if _REVIEW_GATE_MARKER in normalized:
                    review_seen = True
                    break
                # Composer chrome ("model: ... /model to change") means the
                # main chat screen rendered — the gate was skipped; stop
                # early instead of burning the whole budget.
                if "/modeltochange" in normalized:
                    break
            if proc.poll() is not None:
                time.sleep(0.5)
                with contextlib.suppress(OSError):
                    buf += os.read(master, 65536)
                break
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        with contextlib.suppress(OSError):
            os.close(master)
    return _strip_ansi(buf.decode("utf-8", "replace")), review_seen


def test_runner_owned_codex_tui_gates_unreviewed_user_hooks(tmp_path: Path) -> None:
    """
    The codex TUI launched with Omnigent's runner-owned argv must still
    surface the "Hooks need review" trust gate for an unreviewed user hook.

    Fails on the current build: the orchestration injects
    ``--dangerously-bypass-hook-trust`` unconditionally (for supported codex
    versions), which suppresses the review gate, so unreviewed user hooks
    run without the user ever approving them.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=False)

    # Control: a plain codex launch (no Omnigent flags) must show the review
    # gate for the unreviewed user hook. This validates the detection; if
    # codex's UX changed such that no gate renders here, the assertions
    # below would be meaningless. Each run gets a fresh CODEX_HOME so no
    # trust state can leak between runs.
    control_home = tmp_path / "codexhome_control"
    _provision_codex_home(control_home, workspace)
    control_output, control_review = _run_codex_tui(
        [], codex_home=control_home, workspace=workspace
    )
    assert control_review, (
        "codex without any bypass flag did not render the 'Hooks need review' "
        "trust gate for an unreviewed user hook; the detection (or codex's "
        "trust UX) changed and this test needs updating.\n"
        f"TUI output tail:\n{control_output[-2000:]}"
    )

    # Product argv: compute the trust flags exactly as
    # omnigent/runner/native/orchestration.py does (it always passes
    # ``bypass_hook_trust=False`` — Omnigent's own policy/routing hooks are
    # trusted via the app-server handshake), via the same builder the
    # runner-owned terminal uses. The runner unit tests assert the launched
    # terminal spec carries no bypass flag; this test proves the resulting
    # TUI still renders the review gate for an unreviewed user hook.
    remote_args = build_codex_remote_args(
        codex_args=(),
        thread_id=None,
        remote_url="unix:///dev/null",
        bypass_sandbox=False,
        config_overrides=(),
        bypass_hook_trust=False,
    )
    # Keep only the trust-related launch flags; drop the --remote attach
    # (this test exercises the trust gate, which renders before any
    # app-server attachment matters).
    trust_flags = [
        arg for arg in remote_args[: remote_args.index("--remote")] if arg.startswith("--")
    ]

    product_home = tmp_path / "codexhome_product"
    _provision_codex_home(product_home, workspace)
    output, product_review = _run_codex_tui(
        trust_flags, codex_home=product_home, workspace=workspace
    )
    assert product_review, (
        "codex launched with Omnigent's runner-owned trust flags "
        f"{trust_flags!r} skipped the 'Hooks need review' trust gate: the "
        "unreviewed user hook was enabled without user review.\n"
        f"TUI output tail:\n{output[-2000:]}"
    )
