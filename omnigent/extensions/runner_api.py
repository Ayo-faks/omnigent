"""Public runtime values for out-of-process extension runners.

The worker subprocess improves lifecycle and crash isolation. It is not an OS
sandbox and has the same operating-system authority as the runner process.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class RunnerExtensionContext:
    """Activation context supplied once inside the extension worker."""

    extension_id: str
    declared_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunnerToolCallContext:
    """Per-invocation context supplied to an extension tool handler."""

    extension_id: str
    tool_name: str
    request_id: str


class RunnerToolHandler(Protocol):
    def __call__(
        self,
        arguments: dict[str, Any],
        context: RunnerToolCallContext,
    ) -> Any:
        """Execute a tool; async callables are also accepted at runtime."""
        ...


@dataclass
class RunnerExtension:
    """Activated extension runtime returned by a runner entrypoint factory."""

    tools: Mapping[str, Callable[..., Any]]
    shutdown: Callable[[], Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
