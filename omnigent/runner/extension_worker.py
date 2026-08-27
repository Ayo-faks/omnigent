"""Child process for one runner-side extension.

The module speaks bounded JSON lines over stdin/stdout. Extension imports and
handlers run here rather than in the runner, but this process is not sandboxed.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import os
import sys
from collections.abc import Callable, Mapping
from typing import Any, BinaryIO

from omnigent.extensions.runner_api import (
    RunnerExtension,
    RunnerExtensionContext,
    RunnerToolCallContext,
)
from omnigent.extensions.runner_protocol import (
    RUNNER_EXTENSION_PROTOCOL_VERSION,
    RunnerExtensionProtocolError,
    RunnerRequest,
    RunnerResponse,
    decode_request,
    encode_frame,
)


class ExtensionWorker:
    def __init__(
        self,
        entrypoint: str,
        extension_id: str,
        generation: str,
        protocol_output: BinaryIO,
    ) -> None:
        self.entrypoint = entrypoint
        self.extension_id = extension_id
        self.generation = generation
        self.runtime: RunnerExtension | None = None
        self.protocol_output = protocol_output
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self._write_lock = asyncio.Lock()
        self._shutdown_called = False

    async def respond(
        self,
        request: RunnerRequest,
        *,
        result: Any = None,
        code: str | None = None,
        message: str | None = None,
    ) -> None:
        error = {"code": code, "message": message or code} if code is not None else None
        response = RunnerResponse(
            request_id=request.request_id,
            generation=self.generation,
            result=result,
            error=error,
        )
        try:
            frame = encode_frame(response)
        except (RunnerExtensionProtocolError, TypeError, ValueError):
            frame = encode_frame(
                RunnerResponse(
                    request_id=request.request_id,
                    generation=self.generation,
                    error={"code": "SerializationError", "message": "result is not serializable"},
                )
            )
        async with self._write_lock:
            await asyncio.to_thread(self._write, frame)

    def _write(self, frame: bytes) -> None:
        self.protocol_output.write(frame)
        self.protocol_output.flush()

    async def activate(self, request: RunnerRequest) -> None:
        if self.runtime is not None:
            await self.respond(
                request,
                result={
                    "tools": sorted(self.runtime.tools),
                    "protocol_version": RUNNER_EXTENSION_PROTOCOL_VERSION,
                },
            )
            return
        try:
            factory = _load_object(self.entrypoint)
            declared_tools = request.params.get("tools")
            if not isinstance(declared_tools, list) or not all(
                isinstance(name, str) for name in declared_tools
            ):
                raise TypeError("activation must provide declared tool names")
            context = RunnerExtensionContext(
                extension_id=self.extension_id,
                declared_tools=tuple(declared_tools),
            )
            runtime = factory(context)
            if inspect.isawaitable(runtime):
                runtime = await runtime
            if isinstance(runtime, Mapping):
                runtime = RunnerExtension(tools=runtime)
            if not isinstance(runtime, RunnerExtension):
                raise TypeError("runner factory must return RunnerExtension or a tool mapping")
            if not all(
                isinstance(name, str) and callable(handler)
                for name, handler in runtime.tools.items()
            ):
                raise TypeError(
                    "runner tool mapping must contain string names and callable handlers"
                )
            self.runtime = runtime
            await self.respond(
                request,
                result={
                    "tools": sorted(runtime.tools),
                    "protocol_version": RUNNER_EXTENSION_PROTOCOL_VERSION,
                },
            )
        except Exception as exc:  # noqa: BLE001
            await self.respond(request, code="ActivationError", message=str(exc)[:512])

    async def handle(self, request: RunnerRequest) -> None:
        if request.version != RUNNER_EXTENSION_PROTOCOL_VERSION:
            await self.respond(
                request,
                code="ProtocolMismatch",
                message=(
                    f"worker protocol {RUNNER_EXTENSION_PROTOCOL_VERSION} does not support "
                    f"host protocol {request.version}"
                ),
            )
            return
        if request.generation != self.generation:
            await self.respond(
                request, code="StaleGeneration", message="worker generation changed"
            )
            return
        if request.method == "activate":
            await self.activate(request)
            return
        if request.method == "list_tools":
            if self.runtime is None:
                await self.respond(request, code="NotActive", message="extension is not active")
            else:
                await self.respond(request, result={"tools": sorted(self.runtime.tools)})
            return
        if request.method == "invoke":
            await self.invoke(request)
            return
        if request.method == "cancel":
            target = request.params.get("request_id")
            target_generation = request.params.get("generation")
            task = (
                self.tasks.get(target)
                if isinstance(target, str) and target_generation == self.generation
                else None
            )
            if task is not None:
                task.cancel()
            await self.respond(request, result={"cancelled": task is not None})
            return
        if request.method == "shutdown":
            await self.shutdown()
            await self.respond(request, result={"shutdown": True})
            return
        await self.respond(
            request, code="MethodNotFound", message="unknown extension worker method"
        )

    async def invoke(self, request: RunnerRequest) -> None:
        if self.runtime is None:
            await self.respond(request, code="NotActive", message="extension is not active")
            return
        tool_name = request.params.get("tool_name")
        arguments = request.params.get("arguments")
        if not isinstance(tool_name, str) or not isinstance(arguments, dict):
            await self.respond(
                request, code="InvalidParams", message="invoke parameters are invalid"
            )
            return
        handler = self.runtime.tools.get(tool_name)
        if handler is None:
            await self.respond(request, code="UnknownTool", message=f"unknown tool {tool_name!r}")
            return
        context = RunnerToolCallContext(
            extension_id=self.extension_id,
            tool_name=tool_name,
            request_id=request.request_id,
        )
        try:
            if inspect.iscoroutinefunction(handler):
                result = await handler(arguments, context)
            else:
                result = await asyncio.to_thread(handler, arguments, context)
            await self.respond(request, result=result)
        except asyncio.CancelledError:
            await self.respond(request, code="Cancelled", message="tool invocation cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            await self.respond(request, code="ToolError", message=str(exc)[:512])

    async def shutdown(self) -> None:
        if self._shutdown_called:
            return
        self._shutdown_called = True
        for task in tuple(self.tasks.values()):
            if task is not asyncio.current_task():
                task.cancel()
        if self.runtime is not None and self.runtime.shutdown is not None:
            result = self.runtime.shutdown()
            if inspect.isawaitable(result):
                await result


def _load_object(import_path: str) -> Callable[..., Any]:
    module_name, separator, attribute = import_path.partition(":")
    if not separator:
        raise ValueError("runner entrypoint must use module:attribute syntax")
    module = importlib.import_module(module_name)
    value = getattr(module, attribute)
    if not callable(value):
        raise TypeError("runner entrypoint is not callable")
    return value


async def _watch_parent(parent_pid: int) -> None:
    while True:
        await asyncio.sleep(1)
        if os.getppid() != parent_pid:
            os._exit(1)


async def run_worker(
    entrypoint: str,
    extension_id: str,
    generation: str,
    parent_pid: int,
) -> int:
    # Preserve the original stdout pipe exclusively for protocol frames, then
    # redirect extension prints to stderr so debug output cannot corrupt RPC.
    protocol_output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    worker = ExtensionWorker(entrypoint, extension_id, generation, protocol_output)
    parent_watchdog = asyncio.create_task(_watch_parent(parent_pid))
    try:
        while True:
            line = await asyncio.to_thread(sys.stdin.buffer.readline)
            if not line:
                break
            try:
                request = decode_request(line, allow_version_mismatch=True)
            except RunnerExtensionProtocolError as exc:
                print(f"invalid runner extension frame: {exc}", file=sys.stderr, flush=True)
                continue
            if request.method in {"activate", "cancel", "shutdown"}:
                await worker.handle(request)
                if request.method == "shutdown":
                    break
                continue
            task = asyncio.create_task(worker.handle(request))
            worker.tasks[request.request_id] = task
            task.add_done_callback(
                lambda _task, request_id=request.request_id: worker.tasks.pop(request_id, None)
            )
    finally:
        parent_watchdog.cancel()
        await asyncio.gather(parent_watchdog, return_exceptions=True)
        await worker.shutdown()
        if worker.tasks:
            _done, pending = await asyncio.wait(tuple(worker.tasks.values()), timeout=1.0)
            if pending:
                os._exit(1)
        protocol_output.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entrypoint", required=True)
    parser.add_argument("--extension-id", required=True)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--parent-pid", required=True, type=int)
    args = parser.parse_args()
    return asyncio.run(
        run_worker(args.entrypoint, args.extension_id, args.generation, args.parent_pid)
    )


if __name__ == "__main__":
    raise SystemExit(main())
