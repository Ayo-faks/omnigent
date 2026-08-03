"""Claude hook entry point for subagent routing.

Dispatches the ``route-subagent`` hook event to the shared routing client.
Runs as a subprocess under ``python -I`` (isolated mode) to avoid cwd shadowing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from omnigent.inner.hook_scripts import subagent_router


def main() -> int:
    """
    Claude hook entry point.

    Dispatches subcommands:
    - ``route-subagent``: handle an Agent/Task hook event.

    Environment/CLI contract:
    - ``--bridge-dir``: session bridge directory (or ``OMNIGENT_SUBAGENT_ROUTER_DIR``)
    - ``--session-id``: session id (or ``OMNIGENT_SUBAGENT_ROUTER_SESSION_ID``)
    - ``--loopback-path``: frozen loopback path contract (unused in routing, just documented)
    - ``--harness``: harness name (defaults to "claude-native")

    Returns 0 always (routing is advisory; hook errors degrade to "allow").
    """
    parser = argparse.ArgumentParser(
        description="Omnigent subagent routing hooks for Claude",
        add_help=False,  # Suppress -h so a missing subcommand doesn't show usage
    )
    parser.add_argument("subcommand", nargs="?", default="route-subagent")
    parser.add_argument("--bridge-dir", type=str, default=None)
    parser.add_argument("--router-dir", type=str, default=None)
    parser.add_argument("--session-id", type=str, default=None)
    parser.add_argument("--loopback-path", type=str, default=None)  # Frozen contract; unused here
    parser.add_argument("--harness", type=str, default="claude-native")

    args, _ = parser.parse_known_args()

    if args.subcommand == "route-subagent":
        return handle_route_subagent(
            bridge_dir=args.bridge_dir,
            router_dir=args.router_dir,
            session_id=args.session_id,
            harness=args.harness,
        )

    # Unknown subcommand; fail open.
    return 0


def handle_route_subagent(
    bridge_dir: str | None,
    router_dir: str | None,
    session_id: str | None,
    harness: str,
) -> int:
    """
    Handle an Agent/Task routing hook event.

    Reads the hook payload from stdin, routes it, and writes the output to stdout.

    :param bridge_dir: Session bridge directory (from --bridge-dir or env).
    :param router_dir: Explicit router dir (from --router-dir or env).
    :param session_id: Session id (from --session-id or env).
    :param harness: Harness name (from --harness or default).
    :returns: 0 always (fail-open).
    """
    try:
        # Read the hook payload from stdin (claude passes hook data this way).
        payload_text = sys.stdin.read()
        if not payload_text:
            # No input; fail open.
            return 0

        payload = json.loads(payload_text)
    except (json.JSONDecodeError, EOFError, ValueError):
        # Malformed input; fail open.
        return 0

    try:
        # Extract session id from environment if not provided.
        if not session_id:
            session_id = (
                os.environ.get(subagent_router.SESSION_ID_ENV_VAR, "").strip()
                or os.environ.get(subagent_router.NATIVE_SESSION_ID_ENV_VAR, "").strip()
            )

        if not session_id:
            # No session id; fail open.
            return 0

        # Discover the router endpoint.
        discovery_dir = (
            Path(router_dir)
            if router_dir
            else (Path(bridge_dir) if bridge_dir else subagent_router.discover_router_dir())
        )
        endpoint = subagent_router.read_router_endpoint(discovery_dir)

        if endpoint is None:
            # Router unreachable; fail open.
            return 0

        # Extract the tool input and spawn details from the claude hook payload.
        hook_input = payload.get("hookInput", {})
        tool_input = hook_input.get("toolInput", {})

        # Build the routing request (claude sends the prompt in tool_input).
        request_body = subagent_router.build_route_request(
            tool_input,
            harness=harness,
            parent_model=None,  # Claude sends this separately if needed.
            task_keys=("subagent_type", "agent_name", "task_name"),
            include_prompt=True,  # Claude spawns include the prompt.
        )

        # Call the runner's loopback endpoint.
        decision = subagent_router.request_decision(endpoint, session_id, request_body)

        if decision is None:
            # Loopback error; fail open.
            return 0

        # Extract the action from the decision.
        action = decision.get("action", "allow")
        rationale = decision.get("rationale", "")
        model = decision.get("model")

        action_str = str(action) if action else "allow"
        rationale_str = str(rationale) if rationale else ""
        model_str = str(model) if model else None

        if action_str == "deny":
            # Deny the spawn.
            output = subagent_router.decision_to_deny_output(rationale_str)
        elif action_str in ("allow", "rewrite", "redirect"):
            # Allow or rewrite with a model.
            if model_str:
                output = subagent_router.decision_to_allow_output(
                    tool_input, model_str, reason=rationale_str
                )
            else:
                # No model in the decision; fail open (allow unchanged).
                model_fallback = str(tool_input.get("model", "")) if tool_input.get("model") else ""
                output = subagent_router.decision_to_allow_output(
                    tool_input,
                    model_fallback,
                    reason=rationale_str or "No routing model",
                )
        else:
            # Unknown action; fail open.
            model_fallback = str(tool_input.get("model", "")) if tool_input.get("model") else ""
            output = subagent_router.decision_to_allow_output(
                tool_input, model_fallback, reason="Unknown routing action"
            )

        # Write the hook output to stdout.
        sys.stdout.write(json.dumps(output))
        sys.stdout.flush()

    except Exception:  # noqa: BLE001
        # Any other error; fail open by writing nothing.
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
