"""The create paths (wave-0 seam; wave-2 stream 2 fills).

Two create-time routing paths live here so they do not collide with the turn
gate in ``orchestration.py`` (plan 4b, 4f):

1. **Smart Routing harness** — a create with ``harness_override == "auto"`` and a
   ``smart_routing_message`` routes BOTH harness and model at create, via
   ``smart_routing.route_session_harness``. Main does this inline in
   orchestration; the rebuild lifts it here.
2. **Fixed-harness model routing** — a create pinned to ONE native harness with
   routing on routes only the MODEL at create, because a TUI's turns originate
   in the pane and the turn gate can never reach them (plan 2d, 5b). This is the
   ``_fixed_native_routing_harness`` / ``_resolve_fixed_native_model_routing``
   path the CLI depends on.

One trap must survive from the reference implementation (plan 5b): both paths
share ``_routing_host_for_create``, and it authorizes the host BEFORE it looks
the host up. The reverse order is the authorization bug ``CUJ_IMPLEMENTATION.md``
§4.3d describes. Wave-2 stream 2 keeps that order.

Wave 0 declares the seam; the bodies are wave-2 stream 2's.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from omnigent.model_fallbacks import TASK_V1_ARMS

if TYPE_CHECKING:
    import httpx

_logger = logging.getLogger(__name__)

# The router keys candidates by the harness id it understands: the claude arms
# under ``claude-sdk`` and the codex arms under ``codex`` (verified live via
# scripts/probe_routing_api.sh). A create has no session catalog to discover
# yet, so both create paths pass these frozen task_v1 arms as the candidate set.
_CREATE_CANDIDATE_MODELS: dict[str, list[str]] = {
    "claude-sdk": list(TASK_V1_ARMS["claude"].model_ids),
    "codex": list(TASK_V1_ARMS["codex"].model_ids),
}

# The single-harness candidate slice for a fixed-native create, keyed by the
# native harness spelling the create request pins.
_FIXED_NATIVE_CANDIDATES: dict[str, dict[str, list[str]]] = {
    "claude-native": {"claude-sdk": list(TASK_V1_ARMS["claude"].model_ids)},
    "codex-native": {"codex": list(TASK_V1_ARMS["codex"].model_ids)},
}


async def resolve_smart_routing_create(
    smart_routing_message: str,
    *,
    session_id: str | None = None,
    catalog_session_id: str | None = None,  # noqa: ARG001 — create has no catalog; kept for caller
    runner_client: httpx.AsyncClient | None = None,  # noqa: ARG001 — create has no catalog
) -> tuple[str | None, str | None, dict[str, Any] | None, str | None]:
    """Route a create with ``harness_override == "auto"`` to select BOTH harness and model.

    Delegates to :func:`omnigent.server.smart_routing.route_session_harness`,
    which selects from the ``both`` five-arm menu over all native harnesses.

    Returns ``(harness, model, verdict, error)`` — the shape of
    ``smart_routing.route_session_harness``, which it delegates to.
    """
    from omnigent.server.smart_routing import route_session_harness

    if not (smart_routing_message or "").strip():
        return None, None, None, None

    # A session being created has no catalog yet, so route against the frozen
    # task_v1 arms (both families) instead of a live-catalog discovery that
    # would find nothing and decline.
    harness, model, verdict, error = await route_session_harness(
        smart_routing_message,
        session_id=session_id,
        candidate_models=_CREATE_CANDIDATE_MODELS,
    )

    return harness, model, verdict, error


async def resolve_fixed_native_model_routing(
    harness: str,
    smart_routing_message: str,
    *,
    session_id: str | None = None,
    runner_client: httpx.AsyncClient | None = None,  # noqa: ARG001 — create has no catalog
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """Route the model for a create pinned to one native harness.

    Routes only the model for a create already pinned to ``harness``. The
    ``harness`` argument names the session's fixed harness (e.g.
    ``"claude-native"`` or ``"codex-native"``); the candidate set is that
    harness's frozen task_v1 arms ONLY, so the router cannot change the
    harness. This is the "native TUI" path where turns originate in the pane
    and the per-turn gate can never reach. Fails open: an unavailable router
    or an unknown harness yields no model and a rationale for the routing card.

    Returns ``(model, verdict, error)``; ``model`` and ``verdict`` are ``None``
    when nothing should be pinned, and ``error`` then explains why.
    """
    from omnigent.server.smart_routing import route_session_harness

    if not (smart_routing_message or "").strip():
        return None, None, None

    candidates = _FIXED_NATIVE_CANDIDATES.get(harness)
    if candidates is None:
        return None, None, f"Smart Routing is not available for the {harness!r} harness."

    # A session being created has no catalog yet, so route against this
    # harness's frozen task_v1 arms rather than a live-catalog discovery that
    # would find nothing and decline. Single-harness candidate set → the
    # router cannot cross to another harness.
    _harness, model, verdict, error = await route_session_harness(
        smart_routing_message,
        session_id=session_id,
        candidate_models=candidates,
    )

    if model is None or verdict is None:
        return None, None, error or "Routing unavailable; using the harness default model."

    return model, verdict, None
