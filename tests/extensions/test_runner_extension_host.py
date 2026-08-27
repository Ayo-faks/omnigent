from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from pathlib import Path

import pytest

from omnigent.extensions import (
    EXTENSION_API_VERSION,
    ExtensionEntrypoints,
    ExtensionManifest,
    ToolContribution,
)
from omnigent.extensions.runner_protocol import (
    RUNNER_EXTENSION_PROTOCOL_VERSION,
    RunnerExtensionProtocolError,
    decode_response,
)
from omnigent.extensions.tool_names import extension_tool_prefix
from omnigent.runner.extension_host import RunnerExtensionHost, RunnerExtensionHostError

_ENTRYPOINT = "tests.extensions.fixtures.runner_extension:activate"


def _manifest(extension_id: str = "tests.runner", *suffixes: str) -> ExtensionManifest:
    suffixes = suffixes or ("echo",)
    prefix = extension_tool_prefix(extension_id)
    return ExtensionManifest(
        id=extension_id,
        display_name="Runner fixture",
        distribution="tests-runner-extension",
        version="1.0.0",
        requires_omnigent=">=0.11,<1",
        extension_api=EXTENSION_API_VERSION,
        entrypoints=ExtensionEntrypoints(runner=_ENTRYPOINT),
        tools=tuple(
            ToolContribution(
                id=f"{extension_id}.{suffix.replace('_', '-')}-tool",
                tool_name=f"{prefix}{suffix}",
                title=suffix.title(),
                description=f"Fixture {suffix} tool.",
                input_schema={"type": "object"},
            )
            for suffix in suffixes
        ),
    )


async def test_lazily_activates_lists_and_invokes_tool() -> None:
    host = RunnerExtensionHost()
    manifest = _manifest()
    assert host.status_snapshot() == {}
    try:
        names = await host.list_tools(manifest)
        first_status = host.status_snapshot()[manifest.id]
        assert names == frozenset({f"{extension_tool_prefix(manifest.id)}echo"})
        assert await host.list_tools(manifest) == names
        assert host.status_snapshot()[manifest.id]["pid"] == first_status["pid"]
        result = await host.invoke(manifest, next(iter(names)), {"text": "hello"})
        assert result == {"echo": {"text": "hello"}}
    finally:
        await host.shutdown()
    assert host.status_snapshot() == {}


async def test_zero_tool_runtime_reuses_activated_process() -> None:
    host = RunnerExtensionHost()
    manifest = replace(_manifest(), tools=())
    try:
        assert await host.list_tools(manifest) == frozenset()
        pid = host.status_snapshot()[manifest.id]["pid"]
        assert await host.list_tools(manifest) == frozenset()
        assert host.status_snapshot()[manifest.id]["pid"] == pid
    finally:
        await host.shutdown()


async def test_rejects_protocol_and_runtime_tool_mismatches() -> None:
    incompatible = RunnerExtensionHost(protocol_version=RUNNER_EXTENSION_PROTOCOL_VERSION + 1)
    with pytest.raises(RunnerExtensionHostError, match="does not support") as protocol_error:
        await incompatible.list_tools(_manifest())
    assert protocol_error.value.code == "ProtocolMismatch"
    await incompatible.shutdown()

    mismatch = RunnerExtensionHost()
    with pytest.raises(RunnerExtensionHostError, match="do not match") as tool_error:
        await mismatch.list_tools(_manifest("tests.mismatch", "echo"))
    assert tool_error.value.code == "ToolMismatch"
    await mismatch.shutdown()


async def test_slow_activation_does_not_block_other_extensions() -> None:
    host = RunnerExtensionHost()
    slow = _manifest("tests.slow-activation", "echo")
    fast = _manifest("tests.fast-activation", "echo")
    slow_task = asyncio.create_task(host.list_tools(slow))
    await asyncio.sleep(0.03)

    assert await host.list_tools(fast)
    assert not slow_task.done()
    assert await slow_task
    await host.shutdown()


async def test_activation_process_crash_is_typed_and_restartable() -> None:
    host = RunnerExtensionHost()
    crashing = _manifest("tests.activation-crash", "echo")
    with pytest.raises(RunnerExtensionHostError) as error:
        await host.list_tools(crashing)
    assert error.value.code == "WorkerExited"
    assert host.status_snapshot() == {}

    healthy = _manifest("tests.activation-healthy", "echo")
    assert await host.list_tools(healthy)
    await host.shutdown()


async def test_activation_failure_isolated_and_no_process_left() -> None:
    host = RunnerExtensionHost()
    manifest = _manifest("tests.activation-fail", "echo")
    with pytest.raises(RunnerExtensionHostError, match="fixture activation failed") as error:
        await host.list_tools(manifest)
    assert error.value.code == "ActivationError"
    assert host.status_snapshot() == {}
    await host.shutdown()


async def test_cancel_midflight_then_worker_remains_usable() -> None:
    host = RunnerExtensionHost()
    manifest = _manifest("tests.cancel", "echo", "sleep")
    sleep_name = f"{extension_tool_prefix(manifest.id)}sleep"
    echo_name = f"{extension_tool_prefix(manifest.id)}echo"
    try:
        await host.list_tools(manifest)
        invocation = asyncio.create_task(
            host.invoke(
                manifest,
                sleep_name,
                {"seconds": 30},
                request_id="sleep-request",
            )
        )
        await asyncio.sleep(0.05)
        assert await host.cancel(manifest.id, "sleep-request") is True
        with pytest.raises(RunnerExtensionHostError) as cancelled:
            await invocation
        assert cancelled.value.code == "Cancelled"
        assert await host.invoke(manifest, echo_name, {"ok": True}) == {"echo": {"ok": True}}
    finally:
        await host.shutdown()


async def test_caller_task_cancellation_propagates_to_worker() -> None:
    host = RunnerExtensionHost()
    manifest = _manifest("tests.task-cancel", "echo", "sleep")
    try:
        invocation = asyncio.create_task(
            host.invoke(
                manifest,
                f"{extension_tool_prefix(manifest.id)}sleep",
                {"seconds": 30},
                request_id="caller-cancel",
            )
        )
        await asyncio.sleep(0.05)
        invocation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await invocation
        await asyncio.sleep(0.05)
        assert await host.invoke(
            manifest,
            f"{extension_tool_prefix(manifest.id)}echo",
            {},
        ) == {"echo": {}}
    finally:
        await host.shutdown()


async def test_invocation_timeout_cancels_without_poisoning_host() -> None:
    host = RunnerExtensionHost(invocation_timeout=0.05)
    manifest = _manifest("tests.timeout", "echo", "sleep")
    try:
        with pytest.raises(RunnerExtensionHostError) as timeout:
            await host.invoke(
                manifest,
                f"{extension_tool_prefix(manifest.id)}sleep",
                {"seconds": 30},
            )
        assert timeout.value.code == "Timeout"
        await asyncio.sleep(0.05)
        assert await host.invoke(
            manifest,
            f"{extension_tool_prefix(manifest.id)}echo",
            {},
        ) == {"echo": {}}
    finally:
        await host.shutdown()


async def test_crash_fails_one_worker_and_restarts_on_next_call() -> None:
    host = RunnerExtensionHost()
    crashing = _manifest("tests.crasher", "crash", "echo")
    healthy = _manifest("tests.healthy", "echo")
    try:
        await host.list_tools(crashing)
        old_pid = host.status_snapshot()[crashing.id]["pid"]
        await host.list_tools(healthy)
        with pytest.raises(RunnerExtensionHostError) as crash:
            await host.invoke(crashing, f"{extension_tool_prefix(crashing.id)}crash", {})
        assert crash.value.code == "WorkerExited"
        assert await host.invoke(
            healthy,
            f"{extension_tool_prefix(healthy.id)}echo",
            {"healthy": True},
        ) == {"echo": {"healthy": True}}
        await host.list_tools(crashing)
        assert host.status_snapshot()[crashing.id]["pid"] != old_pid
    finally:
        await host.shutdown()


async def test_oversized_frame_and_unknown_tool_are_typed_errors() -> None:
    host = RunnerExtensionHost()
    manifest = _manifest()
    try:
        with pytest.raises(RunnerExtensionHostError) as oversized:
            await host.invoke(
                manifest,
                f"{extension_tool_prefix(manifest.id)}echo",
                {"value": "x" * (1024 * 1024)},
            )
        assert oversized.value.code == "ProtocolError"
        with pytest.raises(RunnerExtensionHostError) as unknown:
            await host.invoke(manifest, "missing", {})
        assert unknown.value.code == "UnknownTool"
    finally:
        await host.shutdown()


async def test_extension_stdout_is_redirected_without_corrupting_protocol() -> None:
    host = RunnerExtensionHost()
    manifest = _manifest("tests.badstdout", "badstdout", "echo")
    try:
        assert await host.invoke(
            manifest,
            f"{extension_tool_prefix(manifest.id)}badstdout",
            {},
        ) == {"unreachable": True}
        assert await host.invoke(
            manifest,
            f"{extension_tool_prefix(manifest.id)}echo",
            {},
        ) == {"echo": {}}
    finally:
        await host.shutdown()


async def test_unknown_worker_method_is_rejected() -> None:
    host = RunnerExtensionHost()
    manifest = _manifest()
    try:
        await host.list_tools(manifest)
        entry = host._entries[manifest.id]
        with pytest.raises(RunnerExtensionHostError) as error:
            await host._request(entry, "unknown", {}, timeout=1)
        assert error.value.code == "MethodNotFound"
    finally:
        await host.shutdown()


async def test_stderr_is_drained_without_deadlock() -> None:
    host = RunnerExtensionHost(invocation_timeout=5)
    manifest = _manifest("tests.stderr", "stderr", "stderr_blob", "echo")
    try:
        result = await host.invoke(
            manifest,
            f"{extension_tool_prefix(manifest.id)}stderr",
            {"count": 2_000},
        )
        assert result == {"lines": 2_000}
        assert await host.invoke(
            manifest,
            f"{extension_tool_prefix(manifest.id)}stderr_blob",
            {"size": 2 * 1024 * 1024},
        ) == {"bytes": 2 * 1024 * 1024}
        assert await host.invoke(
            manifest,
            f"{extension_tool_prefix(manifest.id)}echo",
            {},
        ) == {"echo": {}}
    finally:
        await host.shutdown()


async def test_release_waits_for_inflight_invocation() -> None:
    host = RunnerExtensionHost()
    manifest = _manifest("tests.release-active", "sleep")
    await host.acquire(manifest, "session-a")
    invocation = asyncio.create_task(
        host.invoke(
            manifest,
            f"{extension_tool_prefix(manifest.id)}sleep",
            {"seconds": 0.1},
            request_id="active-request",
        )
    )
    await asyncio.sleep(0.03)
    await host.release(manifest.id, "session-a")

    assert await invocation == {"slept": True}
    for _ in range(20):
        if manifest.id not in host.status_snapshot():
            break
        await asyncio.sleep(0.02)
    assert manifest.id not in host.status_snapshot()
    await host.shutdown()


async def test_release_refcounts_and_reverse_shutdown_order(tmp_path: Path) -> None:
    shutdown_log = tmp_path / "shutdown.log"
    env = os.environ.copy()
    env["EXTENSION_SHUTDOWN_LOG"] = str(shutdown_log)
    host = RunnerExtensionHost(env=env)
    first = _manifest("tests.first", "echo")
    second = _manifest("tests.second", "echo")
    await host.acquire(first, "session-a")
    await host.acquire(first, "session-b")
    await host.release(first.id, "session-a")
    assert first.id in host.status_snapshot()
    await host.acquire(second, "session-a")
    await host.shutdown()

    assert shutdown_log.read_text(encoding="utf-8").splitlines() == [second.id, first.id]


async def test_parent_watchdog_exits_when_runner_parent_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnigent.runner import extension_worker

    monkeypatch.setattr(extension_worker.os, "getppid", lambda: 999)

    def exit_process(code: int) -> None:
        raise RuntimeError(f"exit {code}")

    monkeypatch.setattr(extension_worker.os, "_exit", exit_process)
    with pytest.raises(RuntimeError, match="exit 1"):
        await asyncio.wait_for(extension_worker._watch_parent(123), timeout=2)


def test_malformed_response_frame_is_rejected() -> None:
    with pytest.raises(RunnerExtensionProtocolError, match="valid JSON"):
        decode_response(b"not-json\n")


def test_manifest_helper_is_valid() -> None:
    # Guard fixture names against future contract changes before subprocess tests
    # turn a validation regression into an opaque activation failure.
    from omnigent.extensions.registry import validate_manifest

    validate_manifest(
        _manifest(
            "tests.fixture",
            "echo",
            "sleep",
            "crash",
            "badstdout",
            "stderr",
            "stderr_blob",
        )
    )
    assert replace(_manifest(), display_name="Other").id == "tests.runner"
