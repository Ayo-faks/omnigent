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

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx


async def resolve_smart_routing_create(
    smart_routing_message: str,
    *,
    session_id: str | None = None,
    catalog_session_id: str | None = None,
    runner_client: httpx.AsyncClient | None = None,
) -> tuple[str | None, str | None, dict[str, Any] | None, str | None]:
    """Wave-2 stream 2 fills this (Smart Routing harness create path).

    Returns ``(harness, model, verdict, error)`` — the shape of
    ``smart_routing.route_session_harness``, which it delegates to.
    """
    raise NotImplementedError("wave-2 stream 2")


async def resolve_fixed_native_model_routing(
    harness: str,
    smart_routing_message: str,
    *,
    session_id: str | None = None,
    runner_client: httpx.AsyncClient | None = None,
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """Wave-2 stream 2 fills this (fixed-harness model routing at create).

    Routes only the model for a create pinned to ``harness``. Returns
    ``(model, verdict, error)``. Shares ``_routing_host_for_create`` with the
    path above — authorize before lookup (plan 5b).
    """
    raise NotImplementedError("wave-2 stream 2")
