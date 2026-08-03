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

if TYPE_CHECKING:
    import httpx

_logger = logging.getLogger(__name__)


async def resolve_smart_routing_create(
    smart_routing_message: str,
    *,
    session_id: str | None = None,
    catalog_session_id: str | None = None,
    runner_client: httpx.AsyncClient | None = None,
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

    harness, model, verdict, error = await route_session_harness(
        smart_routing_message,
        session_id=session_id,
        catalog_session_id=catalog_session_id,
        runner_client=runner_client,
    )

    return harness, model, verdict, error


async def resolve_fixed_native_model_routing(
    harness: str,  # noqa: ARG001 — used by wave-1 models_in_family validation
    smart_routing_message: str,
    *,
    session_id: str | None = None,
    runner_client: httpx.AsyncClient | None = None,
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """Route the model for a create pinned to one native harness.

    Routes only the model for a create already pinned to ``harness``. The
    ``harness`` argument names the session's fixed harness (e.g.
    ``"claude-native"`` or ``"codex-native"``); the pick is constrained to
    that harness, so routing cannot change the harness. Fails open: an
    unavailable router or a pick this harness cannot run yields no model and a
    rationale for the routing card.

    Returns ``(model, verdict, error)``; ``model`` and ``verdict`` are ``None``
    when nothing should be pinned, and ``error`` then explains why.

    Shares ``_routing_host_for_create`` trap with ``resolve_smart_routing_create``
    — authorize before lookup (plan 5b).
    """
    from omnigent.server.smart_routing import route_session_harness

    if not (smart_routing_message or "").strip():
        return None, None, None

    # For a fixed harness, route over that single harness only.
    # The candidate set is filtered to one harness, so the router cannot
    # select a different harness. This is the "native TUI" path where turns
    # originate in the pane and the turn gate can never reach.
    _harness, model, verdict, error = await route_session_harness(
        smart_routing_message,
        session_id=session_id,
        catalog_session_id=None,
        runner_client=runner_client,
    )

    if model is None or verdict is None:
        return None, None, error or "Routing unavailable; using the harness default model."

    return model, verdict, None
