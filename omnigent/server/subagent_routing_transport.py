"""Runner-side loopback endpoint for in-harness subagent routing.

This module serves the runner's loopback HTTP endpoint that harness
``PreToolUse`` hooks (Claude ``Agent``/``Task``, Codex ``spawn_agent``) call
before a native subagent spawn. The hook subprocess POSTs a routing request,
and the runner forwards it to the server's policy resolver via the server
relay route (:data:`omnigent.server.routing_contract.SUBAGENT_SERVER_RELAY_PATH`).

The endpoint is advertised to hook scripts via ``subagent_router.json`` in the
session bridge directory, following the same rendezvous pattern as
``tool_relay.json``.

**Architecture:**

- Hook subprocess → POST /v1/sessions/{id}/route-subagent → loopback endpoint (this module)
- Loopback endpoint → POST /v1/sessions/{id}/hooks/route-subagent → server relay (W2·4)
- Server relay → policy resolver (W2·4,
  :func:`omnigent.server.subagent_routing_policy.resolve_subagent_route`)
- Policy resolver → :class:`omnigent.server.routing_contract.SubagentRouteDecision`
- Loopback endpoint (returns decision JSON) → hook subprocess

**Timeout budget:**

1. Harness hook timeout — 40s (Claude native hook entry, codex spawn hook)
2. Hook script HTTP request — 30s (defined in ``omnigent.inner.hook_scripts.subagent_router``)
3. Runner loopback relay wait — 20s (this module's :data:`RELAY_TIMEOUT_S`)
4. Server relay hop — 15s (the relay handler in ``routes/sessions/routes_hooks.py``)

The loopback endpoint is fail-open: any transport failure, authentication error,
timeout, or unparseable response allows the spawn unchanged (routing is an
optimization, never a gate that blocks legitimate spawns).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from omnigent.server.routing_contract import (
    SUBAGENT_SERVER_RELAY_PATH,
    SubagentRouteDecision,
)

_logger = logging.getLogger(__name__)

#: Cap on the task-name field parsed off a spawn payload, so a pathological
#: hook body can't balloon a decision row.
_TASK_NAME_CAP = 200


@dataclass(frozen=True)
class SubagentRouteRequest:
    """One native-subagent spawn awaiting a routing verdict.

    The wire shape the hook subprocess POSTs to the loopback and the server
    relay forwards to the policy. ``build_route_request`` in
    :mod:`omnigent.inner.hook_scripts.subagent_router` produces the matching
    JSON.

    :param harness: Requesting harness id, e.g. ``"claude-native"``.
    :param task_name: Subagent type / task name from the spawn payload.
    :param prompt: Raw task text. ``None`` on codex (its spawn message is
        encrypted in hook payloads).
    :param fork: ``True`` when the spawn forks the parent session.
    :param parent_model: Model the parent session runs on, when known.
    """

    harness: str
    task_name: str = ""
    prompt: str | None = None
    fork: bool = False
    parent_model: str | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> SubagentRouteRequest:
        """Parse a loopback/relay request body.

        :param payload: Decoded JSON object from the hook script.
        :returns: Parsed request.
        :raises ValueError: If ``harness`` is missing or not a string.
        """
        harness = payload.get("harness")
        if not isinstance(harness, str) or not harness.strip():
            raise ValueError("route-subagent body requires a non-empty 'harness' string")
        task_name = payload.get("task_name")
        prompt = payload.get("prompt")
        parent_model = payload.get("parent_model")
        return cls(
            harness=harness.strip(),
            task_name=task_name[:_TASK_NAME_CAP] if isinstance(task_name, str) else "",
            prompt=prompt if isinstance(prompt, str) and prompt else None,
            fork=bool(payload.get("fork")),
            parent_model=(
                parent_model if isinstance(parent_model, str) and parent_model else None
            ),
        )


def auto_harness_session(conv: Any, parent: Any = None) -> bool:
    """Report whether a session may cross harness families for a subagent spawn.

    True only for a session in Smart Routing (auto) harness mode, or a child
    of one: those are the sessions whose harness the router owns. Everyone
    else is pinned to the family they started on, so a codex session never
    gets Claude children and vice versa. In v2 the auto mode is carried by the
    conversation's ``harness_override == "auto"`` sentinel (there is no label).

    :param conv: Conversation row for the session, or ``None``.
    :param parent: Conversation row for its parent, when known.
    :returns: ``True`` when cross-family picks are allowed.
    """
    for row in (conv, parent):
        if row is not None and getattr(row, "harness_override", None) == "auto":
            return True
    return False


#: Seconds the loopback handler waits for the server relay response.
#: Hop 3 of the timeout budget in the module docstring.
RELAY_TIMEOUT_S = 20.0


class SubagentRouteHandler:
    """Handles POST requests to the loopback endpoint.

    Bound to :data:`SUBAGENT_LOOPBACK_PATH` by the runner; decodes the hook
    request, forwards to the server relay, and returns the decision.

    :param session_id: Omnigent session id this endpoint serves.
    :param server_relay_url: Base URL of the server relay (e.g., ``http://127.0.0.1:6868``).
    :param bearer_token: Token hook requests must present in the Authorization header.
    :param loop: Event loop that owns the server relay client.
    :param relay_client: Async HTTP client for calling the server relay.
    """

    def __init__(
        self,
        session_id: str,
        server_relay_url: str,
        bearer_token: str,
        loop: asyncio.AbstractEventLoop,
        relay_client: Any,
        timeout_s: float = RELAY_TIMEOUT_S,
    ):
        self.session_id = session_id
        self.server_relay_url = server_relay_url
        self.bearer_token = bearer_token
        self.loop = loop
        self.relay_client = relay_client
        self.timeout_s = timeout_s

    async def handle_route_request(self, request_body: dict[str, Any]) -> tuple[int, str]:
        """
        Handle a routing request from a hook subprocess.

        Decodes the request, calls the server relay, and returns the decision
        or an error response. On any failure, returns a 500 status so the
        hook subprocess can detect and fail-open.

        :param request_body: Parsed JSON request body from the hook.
        :returns: Tuple of (HTTP status code, JSON response body).
        """
        try:
            # Extract session id from the request (it should match the endpoint's session id).
            req_session_id = request_body.get("session_id", "")
            if req_session_id and req_session_id != self.session_id:
                _logger.warning(
                    "subagent route request for session %s does not match endpoint session %s",
                    req_session_id,
                    self.session_id,
                )
                msg = f"request session_id {req_session_id} != endpoint session {self.session_id}"
                return HTTPStatus.FORBIDDEN, json.dumps(
                    {
                        "error": "session mismatch",
                        "rationale": msg,
                    }
                )

            # Build the server relay URL using the frozen path template.
            relay_path = SUBAGENT_SERVER_RELAY_PATH.format(session_id=unquote(self.session_id))
            relay_url = f"{self.server_relay_url}{relay_path}"

            # Call the server relay.
            decision = await self._call_server_relay(relay_url, request_body)
            if decision is None:
                # Relay error; return 500 so the hook fails open.
                return HTTPStatus.INTERNAL_SERVER_ERROR, json.dumps(
                    {"error": "relay failed", "rationale": "server relay did not respond"}
                )

            # Return the decision payload.
            if isinstance(decision, SubagentRouteDecision):
                payload = decision.to_payload()
            else:
                payload = decision
            return HTTPStatus.OK, json.dumps(payload)

        except Exception as e:  # noqa: BLE001,RUF100
            _logger.exception("subagent route handler error: %s", e)
            return HTTPStatus.INTERNAL_SERVER_ERROR, json.dumps(
                {"error": "handler error", "rationale": str(e)}
            )

    async def _call_server_relay(
        self,
        relay_url: str,
        request_body: dict[str, Any],
    ) -> SubagentRouteDecision | None:
        """
        Call the server relay and decode the decision.

        :param relay_url: Server relay URL (already formatted with session_id).
        :param request_body: Hook request body to forward.
        :returns: Decoded decision, or ``None`` on failure.
        """
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.bearer_token}",
            }
            async with asyncio.timeout(self.timeout_s):
                resp = await self.relay_client.post(
                    relay_url,
                    json=request_body,
                    headers=headers,
                )
            if resp.status_code != HTTPStatus.OK:
                _logger.warning(
                    "server relay returned %d for session %s",
                    resp.status_code,
                    self.session_id,
                )
                return None

            payload = resp.json()
            # Deserialize the decision. The relay returns the frozen shape.
            return SubagentRouteDecision(
                action=payload.get("action", "allow"),
                rationale=payload.get("rationale", ""),
                model=payload.get("model"),
                harness=payload.get("harness"),
                raw_model=payload.get("raw_model"),
                decision_id=payload.get("decision_id"),
            )

        except asyncio.TimeoutError:
            _logger.warning("server relay timeout for session %s", self.session_id)
            return None
        except Exception as e:  # noqa: BLE001
            _logger.warning("server relay call failed: %s", e)
            return None


def write_loopback_advertisement(
    bridge_dir: Path,
    url: str,
    token: str,
    session_id: str | None = None,
) -> Path:
    """
    Advertise the loopback endpoint to hook scripts.

    Writes a JSON file (``subagent_router.json``) into the bridge directory
    so hook scripts can discover the endpoint. Follows the rendezvous pattern
    used by ``tool_relay.json``.

    :param bridge_dir: Session bridge directory (created if missing).
    :param url: Loopback endpoint base URL (e.g., ``http://127.0.0.1:12345``).
    :param token: Bearer token hook scripts must present.
    :param session_id: Session id (included in advertisement so hooks know which session).
    :returns: Path of the written advertisement file.
    """
    bridge_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = bridge_dir / "subagent_router.json"

    payload: dict[str, Any] = {
        "url": url,
        "token": token,
    }
    if session_id is not None:
        payload["session_id"] = session_id

    path.write_text(json.dumps(payload), encoding="utf-8")
    # Restrict permissions to owner since the token is in the file.
    path.chmod(0o600)

    return path
