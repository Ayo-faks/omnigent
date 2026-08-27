"""Import-light naming rules for extension-contributed tools."""

from __future__ import annotations

import re

from omnigent.tool_namespaces import EXTENSION_TOOL_MARKER

TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,256}$")
_LOCAL_TOOL_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_]*[a-z0-9])?$")


def extension_tool_prefix(extension_id: str) -> str:
    """Return an injective, reserved tool prefix derived from an extension ID."""
    encoded = extension_id.replace("-", "_h_").replace(".", "_d_")
    return f"{EXTENSION_TOOL_MARKER}{encoded}__"


def validate_extension_tool_name(extension_id: str, tool_name: str) -> str | None:
    """Return an error for a malformed/non-namespaced tool name."""
    if not TOOL_NAME_RE.fullmatch(tool_name):
        return "tool name must match ^[a-zA-Z0-9_-]{1,256}$"
    prefix = extension_tool_prefix(extension_id)
    if not tool_name.startswith(prefix):
        return f"tool name must start with {prefix!r}"
    local_name = tool_name[len(prefix) :]
    if not _LOCAL_TOOL_NAME_RE.fullmatch(local_name):
        return "extension-local tool name must be lowercase snake_case"
    return None
