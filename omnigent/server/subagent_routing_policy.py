"""Server-side subagent routing policy: family constraints + routing override gate.

This module implements the policy for native-subagent routing decisions. It:

1. Routes the spawn based on the Task prompt (delegates to routing_client the
   same way route_turn/route_session_harness do).
2. Applies family constraints (a spawn's routed arm must stay servable + within
   allowed cross-family policy).
3. Respects the per-session subagent_routing_override toggle (per-call gate).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from omnigent.server.routing_contract import (
    SubagentRouteDecision,
)

_logger = logging.getLogger(__name__)


async def resolve_subagent_route(
    session_id: str,
    req: Any,  # SubagentRouteRequest (from W2·3 transport)
    *,
    subagent_routing_override: str | None = None,
    cost_control_mode: str | None = None,
    parent_cost_control_mode: str | None = None,
    auto_harness: bool = False,
    caps: Any = None,
    available_models: dict[str, list[str]] | None = None,
    _catalog: Mapping[str, list[str]] | None = None,
) -> SubagentRouteDecision:
    """Decide what happens to one native-subagent spawn.

    This is the server-side policy called via the relay endpoint
    (POST /v1/sessions/{session_id}/hooks/route-subagent). It returns a
    decision that the hook script enforces.

    Family constraints (INTELLIGENT_ROUTING_PLAN §12):
    - Rule-0: a spawn's routed arm must be servable on the spawn's harness
      OR on a family-paired harness when the session is in auto mode.
    - Non-auto sessions: Claude ⇏ Codex, Codex ⇏ Claude. Only auto
      (Smart Routing) may cross families.
    - Same-harness subagents: routed model's family matches request harness's
      family → rewrite (same harness). Otherwise if auto → redirect. Else deny.

    Subagent routing override (decision 5):
    - "off" → allow unchanged (advisory, per-call gate).
    - "on" / absent → route (inherit from session cost_control_mode).

    :param session_id: Parent session/conversation identifier.
    :param req: Spawn request (harness, task_name, prompt, fork, parent_model).
    :param subagent_routing_override: Session's subagent_routing_override.
    :param cost_control_mode: Session's cost_control_mode_override.
    :param parent_cost_control_mode: Parent session's cost_control_mode.
    :param auto_harness: True when session may cross harness families.
    :param caps: RuntimeCaps-shaped object. None reads process-global caps.
    :param available_models: Candidate harness → models map. None derives it.
    :param _catalog: Live per-session model catalog (reserved for W2·3 transport).
    :returns: SubagentRouteDecision (action, rationale, model, harness, etc.).
    """
    if caps is None:
        from omnigent.runtime import get_caps

        caps = get_caps()

    # Per-call subagent routing override gate (INTELLIGENT_ROUTING_PLAN §12 decision 5).
    # "off" → allow unchanged; "on" → route (force on); absent/None → inherit.
    if subagent_routing_override == "off":
        _logger.info(
            "route-subagent: subagent routing disabled for session=%s harness=%s",
            session_id,
            getattr(req, "harness", "unknown"),
        )
        return SubagentRouteDecision(
            action="allow",
            rationale="Subagent routing disabled; spawn allowed unchanged",
        )

    # Check if routing is enabled at all (session-level gate).
    # Override "on" forces routing on; otherwise inherit from cost_control_mode.
    routing_on = (
        subagent_routing_override == "on"
        or cost_control_mode == "on"
        or parent_cost_control_mode == "on"
    )
    if not routing_on:
        return SubagentRouteDecision(
            action="allow",
            rationale="Smart Routing not enabled; spawn allowed unchanged",
        )

    # ── Fork exemption ──
    if getattr(req, "fork", False):
        return SubagentRouteDecision(
            action="allow",
            rationale="Fork keeps the parent's model; forks are not routed",
            model=getattr(req, "parent_model", None),
        )

    # ── Routing task + client check ──
    task = _routing_task(req)
    if task is None:
        return SubagentRouteDecision(
            action="allow",
            rationale=(
                "No routable signal (encrypted prompt, no task name); "
                "subagent inherits the session model"
            ),
            model=getattr(req, "parent_model", None),
        )

    client = getattr(caps, "routing_client", None)
    if client is None:
        return SubagentRouteDecision(
            action="allow",
            rationale="Routing unavailable (no routing client); spawn allowed unchanged",
        )

    # ── Build candidate models + call router ──
    cross_harness = auto_harness
    candidates = available_models  # Available models pre-computed by relay/caller
    if not candidates:
        # If not pre-computed, this policy can't build them without importing
        # the W2·3 transport helpers. This is a contract expectation: the relay
        # handler computes candidates and passes them.
        return SubagentRouteDecision(
            action="allow",
            rationale="No candidate models provided; spawn allowed unchanged",
            model=getattr(req, "parent_model", None),
        )

    try:
        result = await client.route(task, candidates)
    except Exception:  # noqa: BLE001 — router outages are advisory
        _logger.warning(
            "route-subagent: router call failed for session=%s",
            session_id,
            exc_info=True,
        )
        result = None

    if result is None or not getattr(result, "model", None):
        detail = _opt_str(getattr(client, "last_error", None)) or "router returned no verdict"
        return SubagentRouteDecision(
            action="allow",
            rationale=f"Routing unavailable ({detail}); spawn allowed unchanged",
            model=req.parent_model,
        )

    # ── Apply family constraints ──
    return _decision_from_result(req, result, candidates, cross_harness)


def _routing_task(req: Any) -> str | None:
    """Return the task text the router should score.

    :param req: Spawn request (duck-typed).
    :returns: Task text, or None when the spawn carries no signal to score.
    """
    _PROMPT_CAP = 4000

    prompt = getattr(req, "prompt", None)
    if prompt:
        return str(prompt)[:_PROMPT_CAP]
    # Codex encrypts the spawn message, so task_name is the only signal.
    task_name = getattr(req, "task_name", "")
    if task_name:
        return str(task_name)[:_PROMPT_CAP]
    return None


def _opt_str(value: Any) -> str | None:
    """Coerce value to string or None."""
    return value if isinstance(value, str) and value else None




def _counterpart_harness(harness: str) -> str | None:
    """Return the cross-family counterpart harness, or None."""
    _COUNTERPART = {
        "claude-sdk": "codex",
        "claude-native": "codex-native",
        "codex": "claude-sdk",
        "codex-native": "claude-native",
    }
    return _COUNTERPART.get(harness)


def _target_harness(
    req_harness: str,
    picked_harness: str | None,
    picked_family: str | None,
) -> str:
    """Name the harness a picked model should run on.

    If the picked model's family matches the request harness's family,
    keep the request harness. Otherwise try the counterpart. Fall back to
    the picked harness or counterpart if known.
    """
    from omnigent.server.smart_routing import _HARNESS_FAMILY

    if picked_family is not None and picked_family == _HARNESS_FAMILY.get(req_harness):
        return req_harness
    counterpart = _counterpart_harness(req_harness)
    if counterpart is not None and _HARNESS_FAMILY.get(counterpart) == picked_family:
        return counterpart
    return picked_harness or counterpart or req_harness


def _decision_from_result(
    req: Any,  # SubagentRouteRequest
    result: Any,
    candidates: Mapping[str, list[str]],
    cross_harness: bool,
) -> SubagentRouteDecision:
    """Translate a router result into a decision, applying family constraints.

    The router picked a model. Check it's in the offered set, then apply
    family constraints:
    - If the model's family matches the request harness's family, rewrite
      (same harness).
    - If cross-family is allowed (auto harness), redirect to the other family.
    - Otherwise deny.
    """
    from omnigent.server.smart_routing import _HARNESS_FAMILY, _bare_id

    model = getattr(result, "model", None)
    rationale = getattr(result, "rationale", "") or ""

    # Only report a raw pick that actually differs from resolved model
    # (a prefix-only spelling difference is the same arm).
    raw = _opt_str(getattr(result, "raw_model", None))
    raw_model = (
        raw if raw and model and _bare_id(raw) != _bare_id(model) else None
    )

    # Ensure the picked model is in the offered set.
    offered = {m for models in candidates.values() for m in models}
    if offered and model not in offered:
        return SubagentRouteDecision(
            action="deny",
            rationale=f"Router picked {model}, which this harness cannot run",
            raw_model=raw_model or model,
        )

    # Determine the family of the picked model.
    picked_harness = _opt_str(getattr(result, "harness", None))
    family = _HARNESS_FAMILY.get(picked_harness) if picked_harness else None
    if family is None:
        # Infer family from which harness in candidates has this model.
        for harness_id, models in candidates.items():
            if model in models:
                family = _HARNESS_FAMILY.get(harness_id)
                break

    # Apply family constraints: determine target harness and action.
    req_harness = getattr(req, "harness", "unknown")
    target = _target_harness(req_harness, picked_harness, family)

    if target == req_harness:
        # Same harness: rewrite (or allow if it's the parent model).
        req_parent = getattr(req, "parent_model", None)
        if req_parent is not None and model == req_parent:
            return SubagentRouteDecision(
                action="allow",
                rationale=rationale or "Router kept the parent model",
                model=model,
                raw_model=raw_model,
            )
        return SubagentRouteDecision(
            action="rewrite",
            rationale=rationale or f"Router selected {model}",
            model=model,
            raw_model=raw_model,
        )

    # Cross-harness: only allowed if auto_harness is True. Otherwise deny.
    if not cross_harness:
        msg = f"Router selected {target}/{model}, but cross-family routing not allowed"
        return SubagentRouteDecision(
            action="deny",
            rationale=msg,
            raw_model=raw_model,
        )

    return SubagentRouteDecision(
        action="redirect",
        rationale=rationale or f"Router selected {target}/{model}",
        model=model,
        harness=target,
        raw_model=raw_model,
    )


