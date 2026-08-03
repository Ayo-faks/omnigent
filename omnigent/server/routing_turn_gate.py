"""The turn gate (wave-0 seam; wave-2 stream 1 fills).

Main decides per turn whether to route, inline in
``omnigent/server/routes/_sessions/orchestration.py`` (the smart-routing block
around the ``route_turn`` call). The rebuild lifts that decision into this module
so wave-2 stream 1 (the turn gate) and wave-2 stream 2 (the create paths) do not
collide in ``orchestration.py`` (plan 4b, 4f). Orchestration keeps only a thin
call into ``route_turn_for_session`` here; card emission and persistence stay
in orchestration.

Wave 0 declares the seam; the body is wave-2 stream 1's. The signature is the
contract — only the lead re-declares it, at a barrier (plan 4e).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

_logger = logging.getLogger(__name__)


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

    Orchestration is responsible for: checking the toggle, checking that no
    model_override exists (session-start cadence), and persisting the result.
    This gate only runs the router and returns its pick + verdict.
    """
    from omnigent.server.smart_routing import route_turn

    # Delegate to smart_routing.route_turn; it handles availability discovery
    # and returns (None, None) on any unavailability/failure.
    routed_model, verdict = await route_turn(
        harness,
        user_message,
        session_id=session_id,
        runner_client=runner_client,
    )

    return routed_model, verdict
