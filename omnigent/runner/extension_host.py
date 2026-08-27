"""Supervisor for runner-side extension subprocesses.

Each extension runs in one lazily started subprocess per runner. This isolates
imports, lifecycle failures, and crashes; it is not an OS security sandbox.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

from omnigent.extensions.api import ExtensionManifest
from omnigent.extensions.runner_protocol import (
    MAX_RUNNER_EXTENSION_FRAME_BYTES,
    RUNNER_EXTENSION_PROTOCOL_VERSION,
    RunnerExtensionProtocolError,
    RunnerRequest,
    decode_response,
    encode_frame,
)

_logger = logging.getLogger(__name__)


class RunnerExtensionHostError(RuntimeError):
    """A typed activation, transport, or tool failure from an extension host."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _ProcessEntry:
    manifest: ExtensionManifest
    process: asyncio.subprocess.Process
    generation: str
    pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)
    tools: frozenset[str] = frozenset()
    activated: bool = False
    close_when_idle: bool = False
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    reader_task: asyncio.Task[None] | None = None
    stderr_task: asyncio.Task[None] | None = None
    owners: set[str] = field(default_factory=set)


class RunnerExtensionHost:
    """Lazily activate and invoke installed runner extensions over JSON lines."""

    def __init__(
        self,
        *,
        activation_timeout: float = 10.0,
        invocation_timeout: float = 60.0,
        env: dict[str, str] | None = None,
        protocol_version: int = RUNNER_EXTENSION_PROTOCOL_VERSION,
    ) -> None:
        self.activation_timeout = activation_timeout
        self.invocation_timeout = invocation_timeout
        self.env = env
        self.protocol_version = protocol_version
        self._entries: dict[str, _ProcessEntry] = {}
        self._spawn_order: list[str] = []
        self._entry_locks: dict[str, asyncio.Lock] = {}
        self._closed = False
        self._background_tasks: set[asyncio.Task[Any]] = set()

    async def _spawn(self, manifest: ExtensionManifest) -> _ProcessEntry:
        runner = manifest.entrypoints.runner
        if runner is None:
            raise RunnerExtensionHostError("NoRunner", "extension has no runner entrypoint")
        generation = uuid.uuid4().hex
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "omnigent.runner.extension_worker",
            "--entrypoint",
            runner,
            "--extension-id",
            manifest.id,
            "--generation",
            generation,
            "--parent-pid",
            str(os.getpid()),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env or os.environ.copy(),
            limit=MAX_RUNNER_EXTENSION_FRAME_BYTES + 1024,
        )
        entry = _ProcessEntry(manifest=manifest, process=process, generation=generation)
        self._entries[manifest.id] = entry
        if manifest.id in self._spawn_order:
            self._spawn_order.remove(manifest.id)
        self._spawn_order.append(manifest.id)
        entry.reader_task = asyncio.create_task(
            self._read_responses(entry),
            name=f"extension-reader:{manifest.id}",
        )
        entry.stderr_task = asyncio.create_task(
            self._drain_stderr(entry),
            name=f"extension-stderr:{manifest.id}",
        )
        return entry

    async def _ensure_entry(self, manifest: ExtensionManifest) -> _ProcessEntry:
        if self._closed:
            raise RunnerExtensionHostError("HostClosed", "runner extension host is closed")
        lock = self._entry_locks.setdefault(manifest.id, asyncio.Lock())
        async with lock:
            entry = self._entries.get(manifest.id)
            if entry is not None and entry.process.returncode is None and entry.activated:
                return entry
            if entry is not None:
                await self._close_entry(entry)
            entry = await self._spawn(manifest)
            try:
                result = await self._request(
                    entry,
                    "activate",
                    {
                        "extension_id": manifest.id,
                        "tools": sorted(tool.tool_name for tool in manifest.tools),
                    },
                    timeout=self.activation_timeout,
                )
                tools = result.get("tools") if isinstance(result, dict) else None
                worker_version = (
                    result.get("protocol_version") if isinstance(result, dict) else None
                )
                if worker_version != self.protocol_version:
                    raise RunnerExtensionHostError(
                        "ProtocolMismatch",
                        f"worker protocol {worker_version!r} does not match host protocol "
                        f"{self.protocol_version}",
                    )
                if not isinstance(tools, list) or not all(isinstance(name, str) for name in tools):
                    raise RunnerExtensionHostError(
                        "InvalidActivation",
                        "extension activation returned an invalid tool list",
                    )
                declared = {tool.tool_name for tool in manifest.tools}
                if set(tools) != declared:
                    mismatch = (
                        f"runtime tools {sorted(tools)!r} do not match "
                        f"manifest {sorted(declared)!r}"
                    )
                    raise RunnerExtensionHostError("ToolMismatch", mismatch)
                entry.tools = frozenset(tools)
                entry.activated = True
                return entry
            except BaseException:
                await self._close_entry(entry)
                raise

    async def _request(
        self,
        entry: _ProcessEntry,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
        request_id: str | None = None,
    ) -> Any:
        if entry.process.returncode is not None or entry.process.stdin is None:
            raise RunnerExtensionHostError("WorkerExited", "extension worker is not running")
        request_id = request_id or uuid.uuid4().hex
        if request_id in entry.pending:
            raise RunnerExtensionHostError("DuplicateRequest", "request id is already active")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        entry.pending[request_id] = future
        try:
            frame = encode_frame(
                RunnerRequest(
                    request_id=request_id,
                    generation=entry.generation,
                    method=method,
                    params=params,
                    version=self.protocol_version,
                )
            )
            async with entry.write_lock:
                entry.process.stdin.write(frame)
                await entry.process.stdin.drain()
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except (RunnerExtensionProtocolError, TypeError, ValueError) as exc:
            entry.pending.pop(request_id, None)
            raise RunnerExtensionHostError("ProtocolError", str(exc)) from exc
        except asyncio.CancelledError:
            entry.pending.pop(request_id, None)
            if method == "invoke":
                cancel_task = asyncio.create_task(self._cancel_best_effort(entry, request_id))
                self._background_tasks.add(cancel_task)
                cancel_task.add_done_callback(self._background_tasks.discard)
            raise
        except TimeoutError as exc:
            entry.pending.pop(request_id, None)
            if method == "invoke":
                cancel_task = asyncio.create_task(self._cancel_best_effort(entry, request_id))
                self._background_tasks.add(cancel_task)
                cancel_task.add_done_callback(self._background_tasks.discard)
            raise RunnerExtensionHostError(
                "Timeout",
                f"extension worker method {method!r} timed out",
            ) from exc
        except (BrokenPipeError, ConnectionError) as exc:
            entry.pending.pop(request_id, None)
            raise RunnerExtensionHostError("WorkerExited", "extension worker pipe closed") from exc
        finally:
            if future.cancelled():
                entry.pending.pop(request_id, None)
            if entry.close_when_idle and not entry.pending and not entry.owners:
                close_task = asyncio.create_task(self._close_if_idle(entry))
                self._background_tasks.add(close_task)
                close_task.add_done_callback(self._background_tasks.discard)

    async def _close_if_idle(self, entry: _ProcessEntry) -> None:
        lock = self._entry_locks.setdefault(entry.manifest.id, asyncio.Lock())
        async with lock:
            if (
                self._entries.get(entry.manifest.id) is entry
                and entry.close_when_idle
                and not entry.pending
                and not entry.owners
            ):
                await self._close_entry(entry)

    async def _cancel_best_effort(self, entry: _ProcessEntry, request_id: str) -> None:
        with contextlib.suppress(Exception):
            await self._request(
                entry,
                "cancel",
                {"request_id": request_id, "generation": entry.generation},
                timeout=1.0,
            )

    async def _read_responses(self, entry: _ProcessEntry) -> None:
        stdout = entry.process.stdout
        assert stdout is not None
        failure: RunnerExtensionHostError | None = None
        try:
            while line := await stdout.readline():
                response = decode_response(line)
                if response.generation != entry.generation:
                    continue
                future = entry.pending.pop(response.request_id, None)
                if future is None or future.done():
                    continue
                if response.error is not None:
                    future.set_exception(
                        RunnerExtensionHostError(
                            response.error["code"],
                            response.error["message"],
                        )
                    )
                else:
                    future.set_result(response.result)
        except (RunnerExtensionProtocolError, ValueError) as exc:
            failure = RunnerExtensionHostError("ProtocolError", str(exc))
        except asyncio.CancelledError:
            return
        finally:
            if failure is None and not self._closed:
                failure = RunnerExtensionHostError(
                    "WorkerExited",
                    f"extension worker exited with status {entry.process.returncode}",
                )
            if failure is not None:
                if entry.process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        entry.process.terminate()
                    try:
                        await asyncio.wait_for(entry.process.wait(), timeout=1.0)
                    except TimeoutError:
                        entry.process.kill()
                        await entry.process.wait()
                for future in entry.pending.values():
                    if not future.done():
                        future.set_exception(failure)
                entry.pending.clear()
            if self._entries.get(entry.manifest.id) is entry:
                self._entries.pop(entry.manifest.id, None)

    async def _drain_stderr(self, entry: _ProcessEntry) -> None:
        stderr = entry.process.stderr
        assert stderr is not None
        try:
            while chunk := await stderr.read(64 * 1024):
                _logger.warning(
                    "extension %s stderr: %s",
                    entry.manifest.id,
                    chunk.decode(errors="replace").rstrip()[:4096],
                )
        except asyncio.CancelledError:
            return

    async def list_tools(self, manifest: ExtensionManifest) -> frozenset[str]:
        """Activate lazily and return the runtime-verified tool names."""
        return (await self._ensure_entry(manifest)).tools

    async def invoke(
        self,
        manifest: ExtensionManifest,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        request_id: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Invoke one declared tool in its extension subprocess."""
        entry = await self._ensure_entry(manifest)
        if tool_name not in entry.tools:
            raise RunnerExtensionHostError("UnknownTool", f"unknown extension tool {tool_name!r}")
        return await self._request(
            entry,
            "invoke",
            {"tool_name": tool_name, "arguments": arguments},
            timeout=self.invocation_timeout if timeout is None else timeout,
            request_id=request_id,
        )

    async def cancel(self, extension_id: str, request_id: str) -> bool:
        """Cancel one in-flight invocation in the current worker generation."""
        entry = self._entries.get(extension_id)
        if entry is None or request_id not in entry.pending:
            return False
        result = await self._request(
            entry,
            "cancel",
            {"request_id": request_id, "generation": entry.generation},
            timeout=1.0,
        )
        return bool(isinstance(result, dict) and result.get("cancelled"))

    async def acquire(self, manifest: ExtensionManifest, owner: str) -> None:
        """Hold a session-scoped reference to an extension worker."""
        entry = await self._ensure_entry(manifest)
        entry.owners.add(owner)

    async def release(self, extension_id: str, owner: str) -> None:
        """Release a session reference and close the now-unowned worker."""
        lock = self._entry_locks.setdefault(extension_id, asyncio.Lock())
        async with lock:
            entry = self._entries.get(extension_id)
            if entry is None:
                return
            entry.owners.discard(owner)
            if entry.owners:
                return
            if entry.pending:
                entry.close_when_idle = True
                return
            await self._close_entry(entry)

    async def _close_entry(self, entry: _ProcessEntry) -> None:
        if entry.process.returncode is None:
            with contextlib.suppress(Exception):
                await self._request(entry, "shutdown", {}, timeout=2.0)
        if entry.process.stdin is not None:
            with contextlib.suppress(BrokenPipeError, ConnectionError):
                entry.process.stdin.close()
        try:
            await asyncio.wait_for(entry.process.wait(), timeout=2.0)
        except TimeoutError:
            entry.process.kill()
            await entry.process.wait()
        current = asyncio.current_task()
        cleanup_tasks = []
        for task in (entry.reader_task, entry.stderr_task):
            if task is not None and task is not current and not task.done():
                task.cancel()
                cleanup_tasks.append(task)
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        for future in entry.pending.values():
            if not future.done():
                future.set_exception(
                    RunnerExtensionHostError("WorkerClosed", "extension worker was closed")
                )
        entry.pending.clear()
        if self._entries.get(entry.manifest.id) is entry:
            self._entries.pop(entry.manifest.id, None)

    def status_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return JSON-safe worker diagnostics without activating extensions."""
        return {
            extension_id: {
                "pid": entry.process.pid,
                "generation": entry.generation,
                "running": entry.process.returncode is None,
                "tools": sorted(entry.tools),
                "owners": sorted(entry.owners),
            }
            for extension_id, entry in sorted(self._entries.items())
        }

    async def shutdown(self) -> None:
        """Close workers in reverse spawn order; repeated calls are harmless."""
        if self._closed:
            return
        self._closed = True
        for extension_id in reversed(self._spawn_order):
            entry = self._entries.get(extension_id)
            if entry is not None:
                await self._close_entry(entry)
        self._spawn_order.clear()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()
