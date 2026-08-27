"""Real child-process extension used by runner host tests."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

from omnigent.extensions.runner_api import (
    RunnerExtension,
    RunnerExtensionContext,
    RunnerToolCallContext,
)


def activate(context: RunnerExtensionContext) -> RunnerExtension:
    if "slow-activation" in context.extension_id:
        time.sleep(0.5)
    if "activation-crash" in context.extension_id:
        os._exit(19)
    if "activation-fail" in context.extension_id:
        raise RuntimeError("fixture activation failed")

    async def echo(arguments: dict[str, Any], _call: RunnerToolCallContext) -> Any:
        return {"echo": arguments}

    async def sleep_tool(arguments: dict[str, Any], _call: RunnerToolCallContext) -> Any:
        await asyncio.sleep(float(arguments.get("seconds", 60)))
        return {"slept": True}

    def crash(_arguments: dict[str, Any], _call: RunnerToolCallContext) -> Any:
        os._exit(17)

    def badstdout(_arguments: dict[str, Any], _call: RunnerToolCallContext) -> Any:
        sys.stdout.write("not-json\n")
        sys.stdout.flush()
        return {"unreachable": True}

    def stderr_blob(arguments: dict[str, Any], _call: RunnerToolCallContext) -> Any:
        size = int(arguments.get("size", 2 * 1024 * 1024))
        sys.stderr.write("x" * size)
        sys.stderr.flush()
        return {"bytes": size}

    def stderr_tool(arguments: dict[str, Any], _call: RunnerToolCallContext) -> Any:
        count = int(arguments.get("count", 100))
        for index in range(count):
            print(f"fixture stderr line {index}", file=sys.stderr, flush=True)
        return {"lines": count}

    available = {
        "echo": echo,
        "sleep": sleep_tool,
        "crash": crash,
        "badstdout": badstdout,
        "stderr_blob": stderr_blob,
        "stderr": stderr_tool,
    }
    tools = {
        name: available[name.rsplit("__", 1)[-1]]
        for name in context.declared_tools
        if name.rsplit("__", 1)[-1] in available
    }
    if "mismatch" in context.extension_id and tools:
        tools.pop(next(iter(tools)))

    def shutdown() -> None:
        path = os.environ.get("EXTENSION_SHUTDOWN_LOG")
        if path:
            with Path(path).open("a", encoding="utf-8") as stream:
                stream.write(f"{context.extension_id}\n")

    return RunnerExtension(tools=tools, shutdown=shutdown)
