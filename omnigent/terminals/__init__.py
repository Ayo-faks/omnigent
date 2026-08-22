"""Per-AP-process tmux terminal subsystem.

Hosts the :class:`TerminalRegistry`, the conversation-scoped registry
of live ``inner.terminal.TerminalInstance`` objects backing the
``sys_terminal_*`` tool family.

See ``designs/OMNIGENT_TERMINAL_BRIDGE.md`` for the design and the
:class:`TerminalInstance` documentation in
:mod:`omnigent.inner.terminal` for the underlying tmux machinery.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from omnigent.terminals.registry import TerminalListEntry, TerminalRegistry

# Resolved on first access (PEP 562) so importing a leaf module such as
# ``omnigent.terminals.close_codes`` does not build the tmux registry —
# ``registry`` reaches into ``omnigent.inner.terminal``, ~100ms of import
# a CLI client reading a WebSocket close code has no use for.
_REGISTRY_EXPORTS: frozenset[str] = frozenset({"TerminalListEntry", "TerminalRegistry"})


def __getattr__(name: str) -> object:
    """
    Import :mod:`omnigent.terminals.registry` on first attribute access.

    :param name: A public attribute, e.g. ``"TerminalRegistry"``.
    :returns: The requested object.
    :raises AttributeError: If *name* is not exported.
    """
    if name not in _REGISTRY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from omnigent.terminals import registry

    value = getattr(registry, name)
    globals()[name] = value  # cache so later reads skip __getattr__
    return value


def __dir__() -> list[str]:
    """
    List the package's public names without importing the registry.

    :returns: Sorted :data:`__all__`.
    """
    return sorted(__all__)


__all__ = [
    "TerminalListEntry",
    "TerminalRegistry",
]
