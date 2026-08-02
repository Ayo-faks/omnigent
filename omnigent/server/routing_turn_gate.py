"""The turn gate (wave-0 seam; wave-2 stream 1 fills).

Main decides per turn whether to route, inline in
``omnigent/server/routes/_sessions/orchestration.py`` (the smart-routing block
around the ``route_turn`` call). The rebuild lifts that decision into this module
so wave-2 stream 1 (the turn gate) and wave-2 stream 2 (the create paths) do not
collide in ``orchestration.py`` (plan 4b, 4f). Orchestration keeps only a thin
call into ``should_route_turn`` / ``apply_turn_route`` here.

Wave 0 declares the seam; the body is wave-2 stream 1's. The signature is the
contract — only the lead re-declares it, at a barrier (plan 4e).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx


async def route_turn_for_session(
    harness: str | None,
    user_message: str,
    *,
    session_id: str | None = None,
    runner_client: httpx.AsyncClient | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Wave-2 stream 1 fills this.

    The turn-gate wrapper orchestration calls instead of ``route_turn`` directly.
    Returns ``(routed_model, verdict)`` — ``(None, None)`` to skip routing this
    turn (a manual pin, routing off, or no discovered catalog). Delegates to
    ``smart_routing.route_turn`` and enforces the session-start cadence: a turn
    that already carries a ``model_override`` does not re-route.
    """
    raise NotImplementedError("wave-2 stream 1")
