"""Directed unit tests for the per-request routing backend seam (plan 2f/7h).

The ``RoutingBackend`` wraps main's two clients and, on every ``route()`` call,
consults ``RuntimeCaps.routing_backend_predicate`` to choose which one answers:
``True`` → the external AI Gateway client, ``False`` → the LLM judge, ``None``
(no deployment binding) → the OSS default. These tests use fakes for the two
clients and assert which one's ``route()`` ran — fast, no network.
"""

from __future__ import annotations

import pytest

from omnigent.server.routing_backend import RoutingBackend
from omnigent.server.smart_routing import RoutingResult

_MESSAGE = "refactor this module"
_MODELS: dict[str, list[str]] = {"self": ["model-a", "model-b"]}


class _FakeClient:
    """A ``RoutingClient`` fake that records its calls and returns a marker."""

    def __init__(self, name: str, *, last_error: str | None = None) -> None:
        self.name = name
        self.calls: list[tuple[str, dict[str, list[str]]]] = []
        self.last_error = last_error

    async def route(
        self,
        message: str,
        available_models: dict[str, list[str]],
    ) -> RoutingResult | None:
        self.calls.append((message, available_models))
        return RoutingResult(model=self.name, rationale=f"picked by {self.name}")


async def _route(backend: RoutingBackend) -> RoutingResult | None:
    return await backend.route(_MESSAGE, _MODELS)


# ── The five required cases ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_predicate_true_routes_to_external() -> None:
    external = _FakeClient("external")
    judge = _FakeClient("judge")
    backend = RoutingBackend(external=external, judge=judge, predicate=lambda: True)

    result = await _route(backend)

    assert result is not None and result.model == "external"
    assert external.calls and not judge.calls


@pytest.mark.asyncio
async def test_predicate_false_routes_to_judge() -> None:
    external = _FakeClient("external")
    judge = _FakeClient("judge")
    backend = RoutingBackend(external=external, judge=judge, predicate=lambda: False)

    result = await _route(backend)

    assert result is not None and result.model == "judge"
    assert judge.calls and not external.calls


@pytest.mark.asyncio
async def test_predicate_none_with_external_present_uses_external() -> None:
    external = _FakeClient("external")
    judge = _FakeClient("judge")
    backend = RoutingBackend(external=external, judge=judge, predicate=None)

    result = await _route(backend)

    assert result is not None and result.model == "external"
    assert external.calls and not judge.calls


@pytest.mark.asyncio
async def test_predicate_none_with_only_judge_uses_judge() -> None:
    judge = _FakeClient("judge")
    backend = RoutingBackend(external=None, judge=judge, predicate=None)

    result = await _route(backend)

    assert result is not None and result.model == "judge"
    assert judge.calls


@pytest.mark.asyncio
async def test_predicate_true_without_external_falls_back_to_judge() -> None:
    judge = _FakeClient("judge")
    backend = RoutingBackend(external=None, judge=judge, predicate=lambda: True)

    result = await _route(backend)

    assert result is not None and result.model == "judge"
    assert judge.calls


# ── Invariants the seam must also hold ───────────────────────────────────────


@pytest.mark.asyncio
async def test_predicate_false_without_judge_falls_back_to_external() -> None:
    # A flag-off workspace with only the gateway configured still routes:
    # never returns "unavailable" just because the requested backend is absent.
    external = _FakeClient("external")
    backend = RoutingBackend(external=external, judge=None, predicate=lambda: False)

    result = await _route(backend)

    assert result is not None and result.model == "external"
    assert external.calls


@pytest.mark.asyncio
async def test_no_backends_configured_returns_none() -> None:
    backend = RoutingBackend(external=None, judge=None, predicate=lambda: True)

    assert await _route(backend) is None


@pytest.mark.asyncio
async def test_predicate_raising_uses_default_backend() -> None:
    def _boom() -> bool:
        raise RuntimeError("flag service down")

    external = _FakeClient("external")
    judge = _FakeClient("judge")
    backend = RoutingBackend(external=external, judge=judge, predicate=_boom)

    result = await _route(backend)

    # A predicate hiccup degrades to the default (external present → external),
    # it never crashes routing.
    assert result is not None and result.model == "external"
    assert external.calls and not judge.calls


@pytest.mark.asyncio
async def test_predicate_evaluated_per_request() -> None:
    # The flag is read on every route(), not cached at construction (7h).
    external = _FakeClient("external")
    judge = _FakeClient("judge")
    flag = {"on": True}
    backend = RoutingBackend(external=external, judge=judge, predicate=lambda: flag["on"])

    await _route(backend)
    flag["on"] = False
    await _route(backend)

    assert len(external.calls) == 1
    assert len(judge.calls) == 1


@pytest.mark.asyncio
async def test_last_error_reflects_last_used_backend() -> None:
    external = _FakeClient("external", last_error="router 401")
    judge = _FakeClient("judge")  # no meaningful last_error
    backend = RoutingBackend(external=external, judge=judge, predicate=lambda: True)

    assert backend.last_error is None  # before any route()
    await _route(backend)
    assert backend.last_error == "router 401"


@pytest.mark.asyncio
async def test_last_error_none_when_judge_has_no_attribute() -> None:
    # The judge (LLMRoutingClient) has no ``last_error``; reading through it
    # must degrade to None rather than raise.
    class _NoErrClient:
        async def route(
            self, message: str, available_models: dict[str, list[str]]
        ) -> RoutingResult | None:
            return None

    backend = RoutingBackend(external=None, judge=_NoErrClient(), predicate=lambda: False)
    await _route(backend)
    assert backend.last_error is None
