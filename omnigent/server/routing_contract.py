"""Wave-0 contract for the Smart Routing rebuild — shared surfaces only.

This module is the single source of truth for the type signatures that the
parallel workstreams (see ``designs/PR_REWRITE_PLAN.md`` §4b) code against. It
declares shapes; it holds no routing logic. A stream imports the shape it needs
from here and never waits on another stream's implementation. Only the lead
re-declares a signature, and only at a wave barrier (plan 4e).

Everything here EXTENDS what ``origin/main`` already ships (plan 2g). Main
already has, in ``omnigent/server/smart_routing.py``: ``RoutingResult`` (model,
rationale, harness), the ``RoutingClient`` protocol, ``ExternalRoutingClient``,
``LLMRoutingClient``, ``route_session_harness``, ``route_turn``,
``fetch_runner_models``, and ``_redirect_incompatible_pick``; and in
``omnigent/entities/conversation.py``: ``RoutingDecisionData`` (model, applied,
rationale, agent). The rebuild adds the fields and modules below without
re-implementing any of that.

The declarations are intentionally logic-free (``...`` bodies, ``NotImplemented``
returns). Wave-1 streams replace each body in the file the partition (plan 4f)
assigns them, keeping the signature identical.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

# The literal model ids live in the guard-blessed home (omnigent/model_fallbacks.py)
# with provenance; the contract re-exports them so streams import one place.
from omnigent.model_fallbacks import (
    FAMILY_FALLBACK_ID as FAMILY_FALLBACK,
)
from omnigent.model_fallbacks import (
    SERVABLE_ALIASES,
    TASK_V1_ARMS,
)

#: The contract's public surface. FAMILY_FALLBACK / SERVABLE_ALIASES / TASK_V1_ARMS
#: are re-exported from model_fallbacks so a stream imports one place (this module).
__all__ = [
    "CLAUDE_GATEWAY_HARNESSES",
    "CODEX_GATEWAY_HARNESSES",
    "FAMILY_FALLBACK",
    "MODEL_ID_PREFIXES",
    "ROUTING_DECISION_ADDED_FIELDS",
    "SERVABLE_ALIASES",
    "SESSION_CREATE_ADDED_FIELDS",
    "SESSION_RESPONSE_ADDED_FIELDS",
    "SUBAGENT_LOOPBACK_PATH",
    "SUBAGENT_SERVER_RELAY_PATH",
    "TASK_V1_ARMS",
    "TASK_V1_MENUS",
    "ResolvedRoute",
    "RoutingBackend",
    "RoutingBackendPredicate",
    "RoutingScope",
    "SubagentAction",
    "SubagentRouteDecision",
    "resolve_route",
]

# ── 1. Routing backend selection (2f) — wave-1 stream 2 ─────────────────────
#
# Main builds ONE routing_client at cli.py startup and hangs it on
# RuntimeCaps.routing_client. The rebuild keeps that, and adds a per-REQUEST
# predicate the deployment supplies: on each route() the seam asks the predicate
# which backend answers. Flag on → the AI Gateway ExternalRoutingClient; flag
# off → the naive LLMRoutingClient judge. A flag-off workspace therefore still
# routes (plan 7h). OSS never hardcodes the flag: the managed plugin binds this
# predicate to its SAFE flag (databricks.mas.omnigent.intelligentRouting,
# default OFF); when no deployment supplies one, the seam defaults to "gateway
# client when configured, judge otherwise". The predicate rides RuntimeCaps
# next to routing_client, mirroring the existing policy_llm_connection_factory.

#: A deployment-supplied callable the seam consults on every ``route()`` to
#: choose the backend. ``True`` → use the external AI Gateway client; ``False``
#: → use the LLM judge. ``None`` (no deployment binding) → the seam's default.
RoutingBackendPredicate = Callable[[], bool]


class RoutingBackend(Protocol):
    """The two-backend seam wave-1 stream 2 builds around the predicate.

    Wraps main's ``ExternalRoutingClient`` and ``LLMRoutingClient`` and dispatches
    per request. It satisfies main's ``RoutingClient`` protocol
    (``route(message, available_models) -> RoutingResult | None``) so every
    existing caller (``route_session_harness``, ``route_turn``) is unchanged;
    only the object on ``RuntimeCaps.routing_client`` differs.
    """

    async def route(
        self,
        message: str,
        available_models: dict[str, list[str]],
    ) -> object | None:
        """Return a ``RoutingResult`` (main's dataclass) or ``None`` to skip."""
        ...


# ── 2. The frozen task_v1 arms + family fallback (3i) — wave-1 stream 1 ──────
#
# The literal ids live in omnigent/model_fallbacks.py (guard-blessed, with
# provenance) and are re-exported above: TASK_V1_ARMS (family -> StaticModelFallback),
# FAMILY_FALLBACK (family -> fallback id), SERVABLE_ALIASES (the glm spelling pin).
# The rebuild does NOT build the cost-substitution ladder, MODEL_LISTS, or the id
# allowlist (plan 3i rule 1). It keeps only: the frozen arm menus (a wire
# contract — task_v1 400s on a partial menu), the ONE fixed fallback per family,
# and the gateway spelling pin.

#: The scenario menus task_v1 offers. "both" is the Smart Routing harness menu.
#: Derived from the guard-owned arm records so there is one source of truth.
TASK_V1_MENUS: Mapping[str, tuple[str, ...]] = {
    "cc": TASK_V1_ARMS["claude"].model_ids,
    "codex": TASK_V1_ARMS["codex"].model_ids,
    "both": TASK_V1_ARMS["claude"].model_ids + TASK_V1_ARMS["codex"].model_ids,
}

#: Catalog prefixes stripped for id comparison (main already has MODEL_ID_PREFIXES).
MODEL_ID_PREFIXES: tuple[str, ...] = ("databricks-", "system.ai.")


@dataclass(frozen=True)
class ResolvedRoute:
    """A router arm translated into a servable id + its family harness.

    :param model: The servable catalog id to apply (after the fallback and the
        gateway-spelling pin).
    :param harness: Harness derived from the arm's family, or ``None``.
    :param raw_model: The router's pick verbatim, kept for the decision payload
        so the chip shows what the router actually said. Differs from ``model``
        only on a fallback or a spelling pin.
    """

    model: str
    harness: str | None
    raw_model: str


def resolve_route(
    picked_model: str,
    *,
    servable: Sequence[str],
    prefixes: Sequence[str] = MODEL_ID_PREFIXES,
) -> ResolvedRoute | None:
    """Wave-1 stream 1 fills this. The four-step chain (plan 3i):

    strip prefix → exact catalog match → family fallback → honest decline.
    Returns ``None`` on an honest decline (no servable arm and no servable
    fallback); the caller then writes ``applied=false`` and keeps the default.
    """
    raise NotImplementedError("wave-1 stream 1")


# ── 3. The decision record extension (3i keeps) — wave-1 stream 3 ────────────
#
# Main's RoutingDecisionData is (model, applied, rationale, agent). The rebuild
# ADDS these fields, all defaulted so old rows deserialize unchanged. Declared
# here as the agreed field set; stream 3 adds them to the real BaseModel in
# omnigent/entities/conversation.py.

#: Fields wave-1 stream 3 appends to ``RoutingDecisionData`` (name → type-hint).
ROUTING_DECISION_ADDED_FIELDS: Mapping[str, str] = {
    "harness": "str | None = None",
    "scope": 'Literal["session", "turn", "child_session", "native_subagent"] = "turn"',
    "decision_id": "str | None = None",
    "raw_model": "str | None = None",
    "attempted_override": "str | None = None",
}

RoutingScope = Literal["session", "turn", "child_session", "native_subagent"]


# ── 4. The subagent verdict shape (2c) — wave-2 stream 4 ─────────────────────

SubagentAction = Literal["allow", "rewrite", "redirect", "deny"]


@dataclass(frozen=True)
class SubagentRouteDecision:
    """What ``resolve_subagent_route`` returns and the loopback endpoint serves.

    Frozen response shape (wave-2 stream 4 owns the implementation in
    ``subagent_routing_policy.py``; the transport in
    ``subagent_routing_transport.py`` serializes it). ``decision_id`` is a join
    key against the persisted ``RoutingDecisionData``.
    """

    action: SubagentAction
    rationale: str
    model: str | None = None
    harness: str | None = None
    raw_model: str | None = None
    decision_id: str | None = None

    def to_payload(self) -> dict[str, object]:
        """Serialize to the frozen loopback response JSON."""
        return {
            "action": self.action,
            "model": self.model,
            "harness": self.harness,
            "raw_model": self.raw_model,
            "rationale": self.rationale,
            "decision_id": self.decision_id,
        }


#: POST loopback path the hook subprocess calls (runner-local); the server relay
#: forwards to POST /v1/sessions/{id}/hooks/route-subagent (wave-2 streams 3+4).
SUBAGENT_LOOPBACK_PATH = "/v1/sessions/{session_id}/route-subagent"
SUBAGENT_SERVER_RELAY_PATH = "/v1/sessions/{session_id}/hooks/route-subagent"


# ── 5. The gateway-inference signal (3f) — wave-1 stream 4 ───────────────────
#
# Per-family "is this host's inference for that family AI-Gateway-backed?".
# Config-only host check (no launch, no network). Rides the host frames, the
# host store, and the hosts route; the web consumes it to gate the option
# (Model row → that harness's family true; harness row → BOTH families true;
# a host that reports nothing → unknown, never hides). Full stub in
# omnigent/gateway_inference.py (wave-1 stream 4 fills it).

#: Harness spellings that belong to each gateway-gated family (transcribed from v1).
CLAUDE_GATEWAY_HARNESSES: tuple[str, ...] = ("claude-native", "native-claude")
CODEX_GATEWAY_HARNESSES: tuple[str, ...] = ("codex", "codex-native", "native-codex")


# ── 6. HTTP create + read-back additions (2e, 3f) — waves 1 & 2 ──────────────
#
# Main's SessionCreateRequest already has model_override, cost_control_mode_override,
# harness_override. The rebuild ADDS the two below (wave-1 stream 3 declares,
# the create-path stream consumes). SessionResponse gains subagent_routing_override
# and gateway_inference for read-back (wave-1 stream 4 + wave-2 stream 5).

#: Fields added to ``SessionCreateRequest`` (name → type-hint).
SESSION_CREATE_ADDED_FIELDS: Mapping[str, str] = {
    "smart_routing_message": "str | None = None",
    "subagent_routing_override": "str | None = None",
}

#: Fields added to ``SessionResponse`` for read-back.
SESSION_RESPONSE_ADDED_FIELDS: Mapping[str, str] = {
    "subagent_routing_override": "str | None = None",
    "gateway_inference": "dict[str, bool] | None = None",
}
