"""Shared subagent-routing decision logic for harness hooks.

Stdlib-only on purpose: the Claude-native hook runs as a per-spawn subprocess
(``python -I -m omnigent.inner.hook_scripts.claude_router_hook``) and blocks the
spawn, so importing anything heavier would show up as spawn latency.

The runner advertises its ``route-subagent`` endpoint by writing
``subagent_router.json`` (``{"url": ..., "token": ...}``) into the session
bridge directory. A missing, malformed, non-loopback or dead-pid advertisement
means the router is unreachable: the hook allows the spawn unchanged and emits
nothing.

Every routing decision call POSTs a request body to the loopback endpoint,
which forwards to the server relay and returns a :class:`SubagentRouteDecision`.
On any failure, the hook allows the spawn unchanged (routing is advisory).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

#: Advertisement file written by the runner's subagent-routing endpoint.
ADVERTISEMENT_FILE = "subagent_router.json"

#: Explicit advertisement directory. Set for harnesses that have no
#: claude-native bridge dir (e.g. the claude-agent-sdk executor).
ROUTER_DIR_ENV_VAR = "OMNIGENT_SUBAGENT_ROUTER_DIR"

#: Session the spawn belongs to, when the harness knows it out of band.
SESSION_ID_ENV_VAR = "OMNIGENT_SUBAGENT_ROUTER_SESSION_ID"

#: Claude-native bridge discovery, already exported to the harness.
BRIDGE_DIR_ENV_VAR = "HARNESS_CLAUDE_NATIVE_BRIDGE_DIR"
NATIVE_SESSION_ID_ENV_VAR = "HARNESS_CLAUDE_NATIVE_REQUEST_SESSION_ID"

#: Hosts an advertised router URL may name (advisory, prevent same-uid exfiltration).
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})

#: Timeout for HTTP requests to the loopback endpoint (hop 2 of the timeout budget).
REQUEST_TIMEOUT_S = 30.0

#: Subagent types that inherit context instead of starting fresh.
FORK_SUBAGENT_TYPES = frozenset({"fork"})
_FORK_SUFFIXES = ("-fork", "_fork", ":fork")


class RouterEndpoint:
    """Advertised ``route-subagent`` endpoint."""

    def __init__(self, url: str, token: str, session_id: str | None = None):
        self.url = url
        self.token = token
        self.session_id = session_id


def discover_router_dir(bridge_dir: str | Path | None = None) -> Path | None:
    """
    Locate the directory holding the router advertisement.

    Falls back to environment variables if no explicit directory is provided.

    :param bridge_dir: Explicit directory path, or ``None`` to check env vars.
    :returns: Directory path, or ``None`` when nothing advertises one.
    """
    if bridge_dir:
        return Path(bridge_dir)
    for env_var in (ROUTER_DIR_ENV_VAR, BRIDGE_DIR_ENV_VAR):
        raw = os.environ.get(env_var, "").strip()
        if raw:
            return Path(raw)
    return None


def read_router_endpoint(router_dir: str | Path | None) -> RouterEndpoint | None:
    """
    Read the advertised endpoint.

    The advertisement is validated before use: the URL must be plain ``http``
    on a loopback address. A missing, malformed, or unsafe advertisement
    means "router unreachable".

    :param router_dir: Directory containing :data:`ADVERTISEMENT_FILE`.
    :returns: Endpoint, or ``None`` when the advertisement is missing or invalid.
    """
    if router_dir is None:
        return None
    try:
        raw = (Path(router_dir) / ADVERTISEMENT_FILE).read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None

    url = payload.get("url")
    token = payload.get("token")

    if not url or not token:
        return None

    # Validate the URL: must be http (not https) on a loopback host.
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS:
            return None
    except Exception:  # noqa: BLE001
        return None

    session_id = payload.get("session_id")
    return RouterEndpoint(url, token, session_id)


def spawn_task_name(tool_input: dict[str, Any], task_keys: tuple[str, ...] = ()) -> str | None:  # type: ignore[explicit-any]
    """
    Extract the subagent task/agent name from the tool input.

    :param tool_input: The hook's ``tool_input`` dict.
    :param task_keys: Keys to try in order (e.g., ``("subagent_type", "task_name")``).
    :returns: Task name string, or ``None`` if not found.
    """
    if not task_keys:
        task_keys = ("subagent_type", "task_name", "agent_name")
    for key in task_keys:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def is_fork_spawn(tool_input: dict[str, object], task_keys: tuple[str, ...] = ()) -> bool:
    """
    Check if the spawn is a fork (inherits context from caller).

    :param tool_input: The hook's ``tool_input`` dict.
    :param task_keys: Keys to check for the spawn type.
    :returns: ``True`` if the spawn is a fork variant.
    """
    task_name = spawn_task_name(tool_input, task_keys)
    if task_name in FORK_SUBAGENT_TYPES:
        return True
    if task_name:
        return any(task_name.endswith(suffix) for suffix in _FORK_SUFFIXES)
    return False


def build_route_request(
    tool_input: dict[str, object],
    harness: str,
    parent_model: str | None = None,
    task_keys: tuple[str, ...] = (),
    include_prompt: bool = True,
) -> dict[str, object]:
    """
    Build the ``route-subagent`` request body.

    :param tool_input: ``tool_input`` from the hook payload.
    :param harness: Requesting harness (e.g., ``"claude-native"``).
    :param parent_model: Model the parent session runs on (when known).
    :param task_keys: Keys naming the subagent (e.g., ``("subagent_type",)``).
    :param include_prompt: ``False`` sends ``prompt: null`` for encrypted prompts (codex).
    :returns: JSON-serializable request body.
    """
    prompt = tool_input.get("prompt") if include_prompt else None
    return {
        "harness": harness,
        "task_name": spawn_task_name(tool_input, task_keys),
        "prompt": prompt if isinstance(prompt, str) and prompt else None,
        "fork": is_fork_spawn(tool_input, task_keys),
        "parent_model": parent_model,
    }


def request_decision(
    endpoint: RouterEndpoint,
    session_id: str,
    body: dict[str, object],
    timeout: float = REQUEST_TIMEOUT_S,
) -> dict[str, object] | None:
    """
    POST one routing request to the runner.

    :param endpoint: Advertised endpoint.
    :param session_id: Omnigent session id.
    :param body: Request body from :func:`build_route_request`.
    :param timeout: Socket timeout in seconds.
    :returns: Decoded decision dict, or ``None`` on any failure (caller allows spawn unchanged).
    """
    url = f"{endpoint.url}/v1/sessions/{urllib.parse.quote(session_id, safe='')}/route-subagent"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {endpoint.token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None

    return payload if isinstance(payload, dict) else None


def decision_to_allow_output(
    tool_input: dict[str, object],
    model: str,
    reason: str = "",
) -> dict[str, object]:
    """
    Map a routing decision to a PreToolUse hook "allow" output.

    :param tool_input: Original hook ``tool_input``.
    :param model: Routed model id to pass to the spawn.
    :param reason: Optional human-readable reason for the decision.
    :returns: Hook output dict suitable for returning to the harness.
    """
    output: dict[str, Any] = {  # type: ignore[explicit-any]
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "updatedInput": {**tool_input, "model": model},
    }
    if reason:
        output["permissionDecisionReason"] = reason
    return {"hookSpecificOutput": output}


def decision_to_deny_output(reason: str) -> dict[str, object]:
    """
    Map a routing decision to a PreToolUse hook "deny" output.

    :param reason: Human-readable reason for the denial.
    :returns: Hook output dict suitable for returning to the harness.
    """
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
