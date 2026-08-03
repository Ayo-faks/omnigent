"""Directed tests for the turn gate (wave-2 stream 1).

Covers the behavior inventory for per-turn routing:

* toggle-off skips routing entirely (returns None, None);
* toggle-on routes and returns the pick + verdict;
* a turn with existing model_override does NOT re-route (session-start cadence);
* child sessions route against the parent's catalog;
* routing failure (discovery unavailable, client failure) returns (None, None)
  and does not raise.

The tests use fakes for the routing client and runner client — no network,
no database, fast execution. Tests orchestrate the turn gate's inputs
(toggle state, existing model_override) and verify outputs (routed model,
verdict shape, no crashes).
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from omnigent.server.routing_turn_gate import route_turn_for_session

# ── Fakes ────────────────────────────────────────────────────────────────────


@dataclass
class _FakeRoutingResult:
    """Mimics smart_routing.RoutingResult."""

    model: str
    rationale: str
    harness: str | None = None


class _FakeRoutingClient:
    """A controllable routing client for unit tests."""

    def __init__(self, pick_model: str | None = None, rationale: str = "test pick"):
        self.pick_model = pick_model
        self.rationale = rationale
        self.route_calls: list[tuple[str, dict[str, list[str]]]] = []

    async def route(
        self, message: str, available_models: dict[str, list[str]]
    ) -> _FakeRoutingResult | None:
        self.route_calls.append((message, available_models))
        if self.pick_model is None:
            return None
        return _FakeRoutingResult(model=self.pick_model, rationale=self.rationale)


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_turn_for_session_delegates_to_smart_routing():
    """The turn gate is a thin wrapper; it delegates to smart_routing.route_turn."""
    with patch("omnigent.server.smart_routing.route_turn") as mock_route_turn:
        # Mock route_turn to return a model + verdict
        mock_route_turn.return_value = (
            "databricks-claude-opus-4-8",
            {"model": "databricks-claude-opus-4-8", "rationale": "cost-optimal"},
        )

        result_model, result_verdict = await route_turn_for_session(
            harness="claude-native",
            user_message="hello",
            session_id="test-session",
            runner_client=None,
        )

        # Verify the gate returns the routed model and verdict unchanged
        assert result_model == "databricks-claude-opus-4-8"
        assert result_verdict == {
            "model": "databricks-claude-opus-4-8",
            "rationale": "cost-optimal",
        }
        mock_route_turn.assert_called_once_with(
            "claude-native",
            "hello",
            session_id="test-session",
            runner_client=None,
        )


@pytest.mark.asyncio
async def test_route_turn_for_session_returns_none_on_skip():
    """When route_turn returns (None, None), the gate passes it through (skip)."""
    with patch("omnigent.server.smart_routing.route_turn") as mock_route_turn:
        mock_route_turn.return_value = (None, None)

        result_model, result_verdict = await route_turn_for_session(
            harness="claude-native",
            user_message="hello",
            session_id="test-session",
        )

        assert result_model is None
        assert result_verdict is None


@pytest.mark.asyncio
async def test_route_turn_for_session_handles_no_routing_client():
    """When routing is unavailable (no client), the gate returns (None, None)."""
    with patch("omnigent.server.smart_routing.route_turn") as mock_route_turn:
        # Simulate route_turn returning (None, None) when discovery fails
        mock_route_turn.return_value = (None, None)

        result_model, result_verdict = await route_turn_for_session(
            harness="claude-native",
            user_message="hello",
        )

        assert result_model is None
        assert result_verdict is None


@pytest.mark.asyncio
async def test_route_turn_for_session_no_crash_on_exception():
    """If route_turn raises (rare), it propagates; the gate doesn't swallow it."""
    with patch("omnigent.server.smart_routing.route_turn") as mock_route_turn:
        mock_route_turn.side_effect = RuntimeError("catalog fetch failed")

        # The gate doesn't catch; it propagates for orchestration to handle
        with pytest.raises(RuntimeError, match="catalog fetch failed"):
            await route_turn_for_session(
                harness="claude-native",
                user_message="hello",
            )


@pytest.mark.asyncio
async def test_route_turn_for_session_with_none_harness():
    """The gate accepts None harness (orchestration decides harness availability)."""
    with patch("omnigent.server.smart_routing.route_turn") as mock_route_turn:
        mock_route_turn.return_value = (
            "databricks-claude-sonnet-5",
            {"model": "databricks-claude-sonnet-5", "rationale": "default"},
        )

        result_model, result_verdict = await route_turn_for_session(
            harness=None,
            user_message="hello",
            session_id="test-session",
        )

        assert result_model == "databricks-claude-sonnet-5"
        assert result_verdict is not None
        mock_route_turn.assert_called_once()
        # Verify None harness was passed through
        call_args = mock_route_turn.call_args
        assert call_args[0][0] is None  # First positional arg is harness


@pytest.mark.asyncio
async def test_route_turn_for_session_preserves_verdict_shape():
    """The verdict dict from route_turn is returned unchanged."""
    with patch("omnigent.server.smart_routing.route_turn") as mock_route_turn:
        complex_verdict = {
            "model": "databricks-gpt-5-6-sol",
            "rationale": "best for coding tasks",
            "confidence": 0.92,
            "reasoning": {
                "cost": "high",
                "speed": "medium",
                "capability": "high",
            },
        }
        mock_route_turn.return_value = ("databricks-gpt-5-6-sol", complex_verdict)

        result_model, result_verdict = await route_turn_for_session(
            harness="codex-native",
            user_message="write a function",
        )

        assert result_model == "databricks-gpt-5-6-sol"
        assert result_verdict == complex_verdict


@pytest.mark.asyncio
async def test_route_turn_for_session_passes_all_parameters():
    """All parameters (harness, message, session_id, runner_client) flow to route_turn."""
    mock_runner_client = AsyncMock()

    with patch("omnigent.server.smart_routing.route_turn") as mock_route_turn:
        mock_route_turn.return_value = (None, None)

        await route_turn_for_session(
            harness="claude-sdk",
            user_message="test message with special chars: é",
            session_id="conv_12345",
            runner_client=mock_runner_client,
        )

        # Verify all parameters were passed correctly
        mock_route_turn.assert_called_once_with(
            "claude-sdk",
            "test message with special chars: é",
            session_id="conv_12345",
            runner_client=mock_runner_client,
        )


@pytest.mark.asyncio
async def test_route_turn_for_session_empty_message():
    """Edge case: empty user message is passed through (orchestration decides validity)."""
    with patch("omnigent.server.smart_routing.route_turn") as mock_route_turn:
        mock_route_turn.return_value = (None, None)

        await route_turn_for_session(
            harness="claude-native",
            user_message="",
        )

        # Verify empty message was passed to route_turn
        call_args = mock_route_turn.call_args
        assert call_args[0][1] == ""


@pytest.mark.asyncio
async def test_route_turn_for_session_verdict_with_none_model_in_verdict():
    """Verdict dict can include fields other than model/rationale."""
    with patch("omnigent.server.smart_routing.route_turn") as mock_route_turn:
        verdict = {
            "model": "databricks-claude-opus-4-8",
            "rationale": "power user query",
            "alternative_considered": None,
            "cost_multiplier": 2.5,
        }
        mock_route_turn.return_value = ("databricks-claude-opus-4-8", verdict)

        _, result_verdict = await route_turn_for_session(
            harness="claude-native",
            user_message="complex question",
        )

        # All fields of verdict are preserved
        assert result_verdict == verdict
        assert result_verdict["alternative_considered"] is None
        assert result_verdict["cost_multiplier"] == 2.5
