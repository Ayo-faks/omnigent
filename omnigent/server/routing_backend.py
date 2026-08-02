"""Per-request routing backend selection (plan 2f/7h).

One routing seam, two backends. ``origin/main`` builds ONE routing client at
startup and hangs it on :attr:`RuntimeCaps.routing_client`. This wraps BOTH of
main's clients — the AI Gateway :class:`~omnigent.server.smart_routing.
ExternalRoutingClient` and the naive :class:`~omnigent.server.smart_routing.
LLMRoutingClient` judge — and, on **every** :meth:`RoutingBackend.route` call,
consults a deployment-supplied predicate to choose which one answers.

The predicate is evaluated per request, not once at construction (7h): flag on
→ the AI Gateway client; flag off → the judge. A flag-off workspace therefore
still routes — the flag selects routing *quality*, it never removes the feature.

OSS never names the flag. It only exposes the predicate seam
(:attr:`RuntimeCaps.routing_backend_predicate`); the managed plugin binds it to
its SAFE flag. When no deployment supplies a predicate (``None``), the seam
defaults to the OSS behaviour — the gateway client when one is configured, the
judge otherwise (plan 2f).

The object satisfies main's ``RoutingClient`` protocol, so every existing caller
(``route_session_harness``, ``route_turn``) is unchanged: only the object on
:attr:`RuntimeCaps.routing_client` differs.
"""

from __future__ import annotations

import logging

from omnigent.server.routing_contract import RoutingBackendPredicate
from omnigent.server.smart_routing import RoutingClient, RoutingResult

__all__ = ["RoutingBackend"]

_logger = logging.getLogger(__name__)


class RoutingBackend:
    """Two-backend routing seam, chosen per request by a predicate.

    Satisfies main's ``RoutingClient`` protocol
    (``route(message, available_models) -> RoutingResult | None``).

    :param external: The AI Gateway ``routes:select`` client, or ``None`` when
        the deployment configures no external router.
    :param judge: The naive LLM-judge client, or ``None`` when the deployment
        configures no server ``llm:`` block.
    :param predicate: A per-request callable — ``True`` selects the external
        client, ``False`` selects the judge — invoked on every :meth:`route`.
        ``None`` (no deployment binding) selects the seam's default: the
        external client when configured, else the judge.
    """

    def __init__(
        self,
        *,
        external: RoutingClient | None,
        judge: RoutingClient | None,
        predicate: RoutingBackendPredicate | None = None,
    ) -> None:
        self._external = external
        self._judge = judge
        self._predicate = predicate
        # The delegate the most recent route() dispatched to, so a caller
        # reading ``last_error`` (route_session_harness does) sees the backend
        # that actually answered. ``None`` before the first route() call.
        self._last_delegate: RoutingClient | None = None

    def _select(self) -> RoutingClient | None:
        """Choose the backend for this request from the per-request predicate.

        - ``True`` → the external client, falling back to the judge when no
          external client is configured (a missing gateway never crashes).
        - ``False`` → the judge, falling back to the external client so a
          flag-off workspace still routes.
        - ``None`` (no predicate) → the OSS default: the external client when
          configured, else the judge.

        A predicate that raises is treated as ``None`` (use the default) rather
        than propagated — a flag-service hiccup must not break routing.
        """
        if self._predicate is None:
            want_external: bool | None = None
        else:
            try:
                want_external = bool(self._predicate())
            except Exception:  # noqa: BLE001 — a predicate hiccup must not break routing
                _logger.warning(
                    "RoutingBackend: predicate raised; using the default backend",
                    exc_info=True,
                )
                want_external = None

        if want_external is False:
            return self._judge or self._external
        # ``True`` and ``None`` both prefer the external client, then the judge.
        return self._external or self._judge

    @property
    def last_error(self) -> str | None:
        """The last-used backend's failure reason, or ``None``.

        ``route_session_harness`` reads this off ``RuntimeCaps.routing_client``
        to surface a specific cause (e.g. a gateway 401) when ``route()``
        returns ``None``. Only the external client records one; the judge has
        no such attribute, so this reads through defensively.
        """
        return getattr(self._last_delegate, "last_error", None)

    async def route(
        self,
        message: str,
        available_models: dict[str, list[str]],
    ) -> RoutingResult | None:
        """Dispatch to the predicate-selected backend.

        Returns the backend's :class:`RoutingResult`, or ``None`` to skip
        routing — including when no backend is configured at all.
        """
        backend = self._select()
        self._last_delegate = backend
        if backend is None:
            return None
        return await backend.route(message, available_models)
