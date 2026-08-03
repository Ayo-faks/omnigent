"""Directed tests for codex subagent-routing hooks generation + trust.

Covers wave-1 stream 7 (plan 2c): the ``PreToolUse`` route-subagent hook
generation, the user-hooks merge, the ``python -I`` isolation trap, the
version-probe "unparseable = supported" rule, and the trust handshake's
module-scoped filtering. The cut enforcement stack (canary / audit / watcher /
banner, plan 3b) is deliberately NOT generated, and this suite asserts its
absence.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from omnigent.inner import codex_executor
from omnigent.inner.codex_executor import (
    _MIN_ROUTER_HOOK_CODEX_VERSION,
    CODEX_ROUTER_DIR_ENV_VAR,
    CODEX_ROUTER_SESSION_ID_ENV_VAR,
    _codex_router_hooks_supported,
    _CodexAppServerSession,
    _populate_codex_home_config,
    codex_router_bridge_dir,
    codex_router_hooks_settings,
    codex_router_session_id,
    merge_codex_user_hooks,
    trust_codex_router_hooks,
    write_codex_router_hooks_file,
)
from omnigent.server.routing_contract import SUBAGENT_LOOPBACK_PATH

_USER_HOOKS = {
    "hooks": {
        "PreToolUse": [{"hooks": [{"type": "command", "command": "user-pre"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "user-stop"}]}],
    }
}


def _write_user_home(tmp_path: Path, *, hooks: dict[str, object] | None = None) -> Path:
    source = tmp_path / "user-codex"
    source.mkdir()
    (source / "auth.json").write_text("{}")
    (source / "config.toml").write_text('model = "gpt-5.4-mini"\n')
    if hooks is not None:
        (source / "hooks.json").write_text(json.dumps(hooks))
    return source


# ── hook generation ─────────────────────────────────────────────────────


def test_router_hooks_settings_registers_only_the_route_gate(tmp_path: Path) -> None:
    """One PreToolUse gate; NO canary / audit (the cut stack, plan 3b)."""
    payload = codex_router_hooks_settings(
        tmp_path / "bridge",
        session_id="conv_abc",
        python_executable="/usr/bin/python3",
    )

    hooks = payload["hooks"]
    # Only the route-subagent gate — the SessionStart canary and the
    # SubagentStart audit belong to the cut enforcement stack.
    assert set(hooks) == {"PreToolUse"}
    assert "SessionStart" not in hooks
    assert "SubagentStart" not in hooks
    (pre_entry,) = hooks["PreToolUse"]
    # Regex, never the flattened literal ``collaborationspawn_agent``.
    assert pre_entry["matcher"] == r".*spawn_agent"
    (pre_hook,) = pre_entry["hooks"]
    assert pre_hook["type"] == "command"
    assert "route-subagent" in pre_hook["command"]
    assert "--session-id conv_abc" in pre_hook["command"]
    assert "--harness codex" in pre_hook["command"]
    # Assert on the split argv: shlex.join shell-quotes the bridge dir and the
    # loopback path (the ``{session_id}`` braces are shell-special), so the raw
    # command string carries quotes the argv does not.
    argv = shlex.split(pre_hook["command"])
    assert argv[argv.index("--bridge-dir") + 1] == str(tmp_path / "bridge")
    # The frozen loopback path from the contract is threaded through verbatim.
    assert argv[argv.index("--loopback-path") + 1] == SUBAGENT_LOOPBACK_PATH


def test_router_hook_timeout_brackets_the_request_budget(tmp_path: Path) -> None:
    """Codex's kill is the outermost bound: just above the hook's budget."""
    payload = codex_router_hooks_settings(tmp_path, python_executable="/usr/bin/python3")
    (pre_hook,) = payload["hooks"]["PreToolUse"][0]["hooks"]
    request_budget = codex_executor._CODEX_ROUTER_HOOK_REQUEST_TIMEOUT_SECONDS
    assert request_budget < pre_hook["timeout"] < 2 * request_budget


def test_router_hooks_settings_omits_session_flag_when_unknown(tmp_path: Path) -> None:
    payload = codex_router_hooks_settings(tmp_path, python_executable="/usr/bin/python3")

    command = payload["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "--session-id" not in command


def test_router_hook_carries_native_harness_label(tmp_path: Path) -> None:
    payload = codex_router_hooks_settings(
        tmp_path, harness="codex-native", python_executable="/usr/bin/python3"
    )

    command = payload["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "--harness codex-native" in command


# ── python -I isolation trap (0d) ─────────────────────────────────────────


def test_router_hook_command_runs_python_isolated(tmp_path: Path) -> None:
    """The routing hook command passes ``-I`` before ``-m``.

    Codex runs hooks with the session's *workspace* as cwd, and ``-m``
    prepends cwd to ``sys.path``. A workspace holding a directory named
    ``omnigent`` — any checkout of this project, the most likely workspace of
    all — then shadows the installed package and the hook dies on
    ``ModuleNotFoundError``. Codex discards the failure, so the routing gate
    fails open in total silence.
    """
    hooks = codex_router_hooks_settings(
        tmp_path, session_id="conv_abc", python_executable="/venv/bin/python"
    )["hooks"]
    commands = [h["command"] for entries in hooks.values() for e in entries for h in e["hooks"]]
    assert commands, "no routing hook commands generated"
    for command in commands:
        argv = shlex.split(command)
        assert argv[1:3] == ["-I", "-m"], f"expected isolated python in {command!r}"


def test_router_hook_isolated_python_ignores_a_shadowing_workspace(tmp_path: Path) -> None:
    """The generated ``-I -m`` command reaches the real installed package.

    The end-to-end proof of the isolation flag: runs the real generated
    argv from a workspace that shadows the installed package with a decoy
    ``omnigent/`` dir — exactly the live failure. The decoy's ``__init__``
    raises on import, so if cwd were on ``sys.path`` (no ``-I``) the process
    would execute it. With ``-I`` Python skips cwd and imports the real
    editable package instead, so the decoy sentinel never appears.

    The wave-2 hook script module (``hook_scripts.codex_router_hook``) does
    not exist in this wave, so the run ends on that submodule being missing
    — but crucially NOT on the decoy. That distinction is the whole proof:
    the decoy sentinel in stderr means cwd shadowed the package; its absence
    means ``-I`` did its job.
    """
    workspace = tmp_path / "workspace"
    decoy = workspace / "omnigent"
    decoy.mkdir(parents=True)
    sentinel = "DECOY_PACKAGE_IMPORTED"
    (decoy / "__init__.py").write_text(f"raise AssertionError({sentinel!r})\n")
    command = codex_router_hooks_settings(
        tmp_path / "bridge", session_id="conv_abc", python_executable=sys.executable
    )["hooks"]["PreToolUse"][0]["hooks"][0]["command"]

    result = subprocess.run(
        shlex.split(command),
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=120,
    )

    # -I kept cwd off sys.path, so the decoy package was never imported.
    assert sentinel not in result.stderr, result.stderr
    assert "decoy" not in result.stderr.lower(), result.stderr

    # Control: WITHOUT -I the same argv executes the decoy and raises.
    argv = shlex.split(command)
    assert argv[1] == "-I"
    no_isolation = [argv[0], *argv[2:]]
    control = subprocess.run(
        no_isolation,
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert sentinel in control.stderr, control.stderr


# ── user-hooks merge (no clobber) ─────────────────────────────────────────


def test_merge_user_hooks_preserves_user_entries_after_omnigent(tmp_path: Path) -> None:
    user_hooks = tmp_path / "hooks.json"
    user_hooks.write_text(json.dumps(_USER_HOOKS))
    payload = codex_router_hooks_settings(tmp_path, python_executable="/usr/bin/python3")

    merged = merge_codex_user_hooks(payload, user_hooks)

    pre = merged["hooks"]["PreToolUse"]
    assert len(pre) == 2
    # Omnigent's route gate stays first so it gates before user hooks.
    assert pre[0]["matcher"] == r".*spawn_agent"
    assert pre[1]["hooks"][0]["command"] == "user-pre"
    # Events the user declares alone are added wholesale.
    assert merged["hooks"]["Stop"][0]["hooks"][0]["command"] == "user-stop"
    # The original payload is not mutated.
    assert len(payload["hooks"]["PreToolUse"]) == 1


def test_merge_user_hooks_tolerates_malformed_user_file(tmp_path: Path) -> None:
    user_hooks = tmp_path / "hooks.json"
    user_hooks.write_text("{not json")
    payload = codex_router_hooks_settings(tmp_path, python_executable="/usr/bin/python3")

    # A bad user file must never break routing — payload is unchanged.
    assert merge_codex_user_hooks(payload, user_hooks) == payload


def test_write_router_hooks_file_replaces_symlink_and_merges(tmp_path: Path) -> None:
    source = _write_user_home(tmp_path, hooks=_USER_HOOKS)
    codex_home = tmp_path / "private"
    codex_home.mkdir()
    _populate_codex_home_config(codex_home, source)
    assert (codex_home / "hooks.json").is_symlink()

    path = write_codex_router_hooks_file(
        codex_home,
        tmp_path / "bridge",
        session_id="conv_abc",
        python_executable="/usr/bin/python3",
    )

    # The symlink is replaced by a real merged file.
    assert not path.is_symlink()
    payload = json.loads(path.read_text())
    assert [entry.get("matcher") for entry in payload["hooks"]["PreToolUse"]] == [
        r".*spawn_agent",
        None,
    ]
    assert payload["hooks"]["Stop"][0]["hooks"][0]["command"] == "user-stop"
    # The user's real hooks.json on disk is untouched.
    assert json.loads((source / "hooks.json").read_text()) == _USER_HOOKS


def test_write_router_hooks_file_without_user_hooks(tmp_path: Path) -> None:
    codex_home = tmp_path / "private"
    codex_home.mkdir()

    path = write_codex_router_hooks_file(
        codex_home,
        tmp_path / "bridge",
        user_hooks_source=tmp_path / "missing" / "hooks.json",
        python_executable="/usr/bin/python3",
    )

    payload = json.loads(path.read_text())
    assert len(payload["hooks"]["PreToolUse"]) == 1


def test_populate_skips_hooks_symlink_when_routing_on(tmp_path: Path) -> None:
    source = _write_user_home(tmp_path, hooks=_USER_HOOKS)
    codex_home = tmp_path / "private"
    codex_home.mkdir()

    _populate_codex_home_config(codex_home, source, subagent_routing=True)

    # The generated file will own hooks.json — no symlink shadowing it.
    assert not (codex_home / "hooks.json").exists()
    assert (codex_home / "auth.json").is_symlink()
    assert (codex_home / "config.toml").is_file()


def test_populate_symlinks_hooks_when_routing_off(tmp_path: Path) -> None:
    source = _write_user_home(tmp_path, hooks=_USER_HOOKS)
    codex_home = tmp_path / "private"
    codex_home.mkdir()

    _populate_codex_home_config(codex_home, source)

    assert (codex_home / "hooks.json").is_symlink()
    assert (codex_home / "hooks.json").resolve() == (source / "hooks.json").resolve()


# ── env discovery ─────────────────────────────────────────────────────────


def test_router_env_discovery(tmp_path: Path) -> None:
    env = {
        CODEX_ROUTER_DIR_ENV_VAR: str(tmp_path),
        CODEX_ROUTER_SESSION_ID_ENV_VAR: " conv_abc ",
    }

    assert codex_router_bridge_dir(env) == tmp_path
    assert codex_router_session_id(env) == "conv_abc"
    assert codex_router_bridge_dir({}) is None
    assert codex_router_session_id({}) is None


# ── version probe: unparseable = supported (0d) ────────────────────────────


def test_unparseable_codex_version_counts_as_supported() -> None:
    """A ``None`` version (probe could not parse) is treated as supported.

    A flaky ``codex --version`` must never silently disable routing — that
    would wedge a terminal on a spawn prompt no subagent can answer. A
    genuinely old codex fails loudly later at the trust gate instead.
    """
    assert _codex_router_hooks_supported(None) is True


def test_supported_and_too_old_codex_versions() -> None:
    assert _codex_router_hooks_supported(_MIN_ROUTER_HOOK_CODEX_VERSION) is True
    assert _codex_router_hooks_supported((0, 200, 0)) is True
    major, minor, patch = _MIN_ROUTER_HOOK_CODEX_VERSION
    assert _codex_router_hooks_supported((major, minor - 1, patch)) is False


# ── trust handshake: module-scoped filtering ───────────────────────────────


class _RecordingRequest:
    """A stub app-server ``request`` returning scripted ``hooks/list`` data."""

    def __init__(self, listings: list[list[dict[str, Any]]], cwd: str) -> None:
        self._listings = listings
        self._cwd = cwd
        self._list_calls = 0
        self.batch_write_payloads: list[dict[str, Any]] = []

    async def __call__(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "hooks/list":
            hooks = self._listings[min(self._list_calls, len(self._listings) - 1)]
            self._list_calls += 1
            return {"result": {"data": [{"cwd": self._cwd, "hooks": hooks}]}}
        if method == "config/batchWrite":
            self.batch_write_payloads.append(params)
            return {"result": {}}
        raise AssertionError(f"unexpected method {method!r}")


_ROUTER_CMD = "/venv/bin/python -I -m omnigent.inner.hook_scripts.codex_router_hook route-subagent"
_POLICY_CMD = "/venv/bin/python -I -m omnigent.codex_native_hook evaluate-policy"
_USER_CMD = "user-pre"


def test_trust_only_touches_the_routing_hook_module() -> None:
    """The handshake trusts ONLY hooks whose command runs the router module.

    A user-contributed hook and the native policy hook share the merged
    hooks.json; the routing trust pass must leave both alone (the policy hook
    has its own trust pass; a user hook is never auto-trusted by Omnigent).
    """
    cwd = "/home/user/repo"
    untrusted_router = {
        "key": "router-key",
        "currentHash": "h-router",
        "trustStatus": "untrusted",
        "command": _ROUTER_CMD,
    }
    policy = {
        "key": "policy-key",
        "currentHash": "h-policy",
        "trustStatus": "untrusted",
        "command": _POLICY_CMD,
    }
    user = {
        "key": "user-key",
        "currentHash": "h-user",
        "trustStatus": "untrusted",
        "command": _USER_CMD,
    }
    trusted_router = {**untrusted_router, "trustStatus": "trusted"}
    request = _RecordingRequest(
        listings=[
            [untrusted_router, policy, user],  # first hooks/list
            [trusted_router, policy, user],  # re-list after batchWrite
        ],
        cwd=cwd,
    )

    still = asyncio.run(trust_codex_router_hooks(request, cwd=cwd))

    assert still == []
    # Exactly one batchWrite, carrying ONLY the router hook's key.
    assert len(request.batch_write_payloads) == 1
    written = request.batch_write_payloads[0]["edits"][0]["value"]
    assert set(written) == {"router-key"}
    assert written["router-key"] == {"trusted_hash": "h-router"}


def test_trust_noop_when_no_routing_hook_registered() -> None:
    cwd = "/home/user/repo"
    request = _RecordingRequest(
        listings=[[{"key": "policy-key", "trustStatus": "untrusted", "command": _POLICY_CMD}]],
        cwd=cwd,
    )

    assert asyncio.run(trust_codex_router_hooks(request, cwd=cwd)) == []
    # Nothing to trust → no batchWrite issued.
    assert request.batch_write_payloads == []


def test_trust_noop_when_routing_hook_already_trusted() -> None:
    cwd = "/home/user/repo"
    request = _RecordingRequest(
        listings=[[{"key": "router-key", "trustStatus": "trusted", "command": _ROUTER_CMD}]],
        cwd=cwd,
    )

    assert asyncio.run(trust_codex_router_hooks(request, cwd=cwd)) == []
    assert request.batch_write_payloads == []


def test_trust_reports_keys_still_untrusted_after_write() -> None:
    """A hook still untrusted after the write is reported, never raised."""
    cwd = "/home/user/repo"
    untrusted = {
        "key": "router-key",
        "currentHash": "h-router",
        "trustStatus": "untrusted",
        "command": _ROUTER_CMD,
    }
    request = _RecordingRequest(listings=[[untrusted], [untrusted]], cwd=cwd)

    still = asyncio.run(trust_codex_router_hooks(request, cwd=cwd))

    assert still == ["router-key"]


# ── SDK app-server integration: generation + trust wiring ──────────────────


class _HooksSnapshot:
    def __init__(self, path: Path) -> None:
        self.is_symlink = path.is_symlink()
        self.payload: dict[str, Any] | None = None
        if path.is_file():
            self.payload = json.loads(path.read_text())


class _FakeProc:
    """Minimal stand-in for the launched codex app-server subprocess.

    ``returncode`` is preset to 0 so ``close()``'s process-tree teardown
    short-circuits (the fake never really ran).
    """

    returncode: int | None = 0
    pid = -1

    def __init__(self) -> None:
        self.stdin = None


def _run_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    env: dict[str, str],
    version: tuple[int, int, int] | None = (0, 145, 0),
) -> tuple[_HooksSnapshot, list[tuple[str, dict[str, Any]]]]:
    """Drive ``_CodexAppServerSession.start`` with codex fully stubbed.

    The launch is stubbed to *succeed* (a fake proc + no-op reader/stderr
    loops) so the post-launch trust handshake actually runs and can be
    observed. hooks.json is snapshotted from the private home right after
    ``start()`` returns and before ``close()`` tears the home down.
    """
    source = _write_user_home(tmp_path, hooks=_USER_HOOKS)
    workspace = tmp_path / "work"
    workspace.mkdir()
    requests: list[tuple[str, dict[str, Any]]] = []

    async def fake_version(_codex_path: str) -> tuple[int, int, int] | None:
        return version

    async def fake_exec(*argv: str, **kwargs: Any) -> _FakeProc:
        return _FakeProc()

    async def noop_loop(self: Any) -> None:
        return None

    monkeypatch.setattr(codex_executor, "populate_codex_skills_from_bundle", lambda *a, **k: None)
    monkeypatch.setattr(codex_executor, "_codex_home_config_source_from_env", lambda: source)
    monkeypatch.setattr(codex_executor, "_codex_cli_version", fake_version)
    monkeypatch.setattr(codex_executor, "_create_subprocess_exec", fake_exec)
    # No real subprocess, so the reader / stderr loops have nothing to read.
    monkeypatch.setattr(_CodexAppServerSession, "_reader_loop", noop_loop, raising=True)
    monkeypatch.setattr(_CodexAppServerSession, "_stderr_loop", noop_loop, raising=True)
    session = _CodexAppServerSession(
        codex_path="/bin/echo",
        cwd=str(workspace),
        env=env,
        tool_executor=None,
    )

    async def fake_request(method: str, params: dict[str, Any]) -> dict[str, Any]:
        requests.append((method, params))
        if method == "hooks/list":
            router = {
                "key": "router-key",
                "currentHash": "h-router",
                "trustStatus": "trusted",
                "command": _ROUTER_CMD,
            }
            return {"result": {"data": [{"cwd": str(workspace), "hooks": [router]}]}}
        return {"result": {}}

    monkeypatch.setattr(session, "_request", fake_request)

    async def drive() -> _HooksSnapshot:
        await session.start()
        assert session._codex_home_dir is not None, "start() left no private home"
        snapshot = _HooksSnapshot(session._codex_home_dir / "hooks.json")
        await session.close()
        return snapshot

    snapshot = asyncio.run(drive())
    return snapshot, requests


def test_start_generates_merged_hooks_and_trusts_when_routing_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = tmp_path / "bridge"
    bridge.mkdir()

    hooks, requests = _run_start(
        tmp_path,
        monkeypatch,
        env={
            CODEX_ROUTER_DIR_ENV_VAR: str(bridge),
            CODEX_ROUTER_SESSION_ID_ENV_VAR: "conv_abc",
        },
    )

    # A merged regular file (route gate + user hooks), not a symlink.
    assert hooks.payload is not None
    assert not hooks.is_symlink
    pre = hooks.payload["hooks"]["PreToolUse"]
    assert len(pre) == 2
    assert "--session-id conv_abc" in pre[0]["hooks"][0]["command"]
    assert hooks.payload["hooks"]["Stop"][0]["hooks"][0]["command"] == "user-stop"

    # The trust handshake ran after initialize and before the first turn.
    methods = [m for m, _ in requests]
    assert methods[0] == "initialize"
    assert "hooks/list" in methods


def test_start_keeps_symlinked_hooks_when_routing_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hooks, requests = _run_start(tmp_path, monkeypatch, env={})

    # No routing endpoint advertised → user's hooks.json symlinked untouched.
    assert hooks.is_symlink
    # No trust handshake when routing is off.
    assert "hooks/list" not in [m for m, _ in requests]


def test_start_skips_hook_generation_on_too_old_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A codex below the trust-protocol floor generates no hooks file.

    It would only be silently discarded; the user's hooks.json is symlinked
    in instead so it is not left missing.
    """
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    major, minor, patch = _MIN_ROUTER_HOOK_CODEX_VERSION

    hooks, requests = _run_start(
        tmp_path,
        monkeypatch,
        env={CODEX_ROUTER_DIR_ENV_VAR: str(bridge)},
        version=(major, minor - 1, patch),
    )

    assert hooks.is_symlink
    assert "hooks/list" not in [m for m, _ in requests]


def test_start_generates_hooks_when_codex_version_unparseable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unparseable version still generates + trusts (0d: never wedge)."""
    bridge = tmp_path / "bridge"
    bridge.mkdir()

    hooks, requests = _run_start(
        tmp_path,
        monkeypatch,
        env={CODEX_ROUTER_DIR_ENV_VAR: str(bridge)},
        version=None,
    )

    assert hooks.payload is not None
    assert not hooks.is_symlink
    assert "hooks/list" in [m for m, _ in requests]
