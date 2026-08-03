"""Unit tests for create-time routing resolvers (wave-2 stream 2)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from omnigent.server.routing_contract import ResolvedRoute
from omnigent.server.routing_create import (
    resolve_fixed_native_model_routing,
    resolve_smart_routing_create,
)
from omnigent.server.routing_decision_store import build_decision, decision_from_route


class TestResolveSmartRoutingCreate:
    """Tests for Smart Routing harness create resolver."""

    async def test_empty_message_returns_none(self) -> None:
        """Empty routing message should return None for all fields."""
        result = await resolve_smart_routing_create("")
        assert result == (None, None, None, None)

    async def test_whitespace_message_returns_none(self) -> None:
        """Whitespace-only message should return None."""
        result = await resolve_smart_routing_create("   \n  \t  ")
        assert result == (None, None, None, None)

    async def test_delegates_to_route_session_harness(self) -> None:
        """Delegates to route_session_harness, then maps harness + resolves model.

        The router returns a bare arm id under its own harness spelling
        (``codex`` / ``claude-sdk``). The resolver maps the harness to native
        and translates the pick to the servable spelling via resolve_route.
        """
        with patch("omnigent.server.smart_routing.route_session_harness") as mock_route:
            mock_route.return_value = (
                "claude-sdk",  # the router's own harness spelling
                "claude-opus-4-8",  # the router's bare pick
                {"model": "claude-opus-4-8", "rationale": "test"},
                None,
            )

            harness, model, verdict, error = await resolve_smart_routing_create(
                "hello world",
                session_id="sess-123",
                catalog_session_id="cat-456",
                runner_client=MagicMock(),
            )

            assert harness == "claude-native"  # mapped to native spelling
            assert model == "databricks-claude-opus-4-8"  # resolved to servable
            assert verdict is not None and verdict["raw_model"] == "claude-opus-4-8"
            assert error is None
            mock_route.assert_called_once()

    async def test_router_error_returns_error_string(self) -> None:
        """Router error should be propagated as error field."""
        with patch("omnigent.server.smart_routing.route_session_harness") as mock_route:
            mock_route.return_value = (
                None,
                None,
                None,
                "Router unavailable",
            )

            result = await resolve_smart_routing_create("test message")
            assert result[3] == "Router unavailable"

    async def test_router_harness_mapped_to_native_spelling(self) -> None:
        """The router's claude-sdk / codex pick becomes the native TUI spelling.

        route_session_harness returns the harness spelling the router keys on
        (claude-sdk / codex); a Smart Routing session launches the native
        wrapper, so the resolver must surface claude-native / codex-native.
        """
        for router_harness, expected_native, model in (
            ("claude-sdk", "claude-native", "databricks-claude-opus-4-8"),
            ("codex", "codex-native", "databricks-gpt-5-6-sol"),
        ):
            with patch("omnigent.server.smart_routing.route_session_harness") as mock_route:
                mock_route.return_value = (
                    router_harness,
                    model,
                    {"model": model, "rationale": "test"},
                    None,
                )
                harness, got_model, _verdict, error = await resolve_smart_routing_create(
                    "route me"
                )
                assert harness == expected_native, (router_harness, harness)
                assert got_model == model
                assert error is None


class TestResolveFixedNativeModelRouting:
    """Tests for fixed-harness model routing resolver."""

    async def test_empty_message_returns_none(self) -> None:
        """Empty routing message should return None for all fields."""
        result = await resolve_fixed_native_model_routing("claude-native", "")
        assert result == (None, None, None)

    async def test_routes_only_model_not_harness(self) -> None:
        """Should route only the model for a fixed harness."""
        with patch("omnigent.server.smart_routing.route_session_harness") as mock_route:
            mock_route.return_value = (
                "claude-sdk",  # Router might suggest a different harness
                "databricks-claude-opus-4-8",
                {"model": "databricks-claude-opus-4-8", "rationale": "test"},
                None,
            )

            result = await resolve_fixed_native_model_routing(
                "claude-native",
                "hello world",
                session_id="sess-123",
            )

            # Should return the model even though router suggested a different harness
            assert result[0] == "databricks-claude-opus-4-8"
            assert result[1] is not None  # verdict
            assert result[2] is None  # no error

    async def test_claude_sdk_routes_to_servable_claude_arm(self) -> None:
        """The in-process claude-sdk harness (polly / debby) routes at create.

        It routes over the claude family arms and resolves the bare router pick
        to its servable databricks- spelling, exactly like the native path.
        """
        with patch("omnigent.server.smart_routing.route_session_harness") as mock_route:
            mock_route.return_value = (
                "claude-sdk",
                "claude-sonnet-5",  # bare router pick
                {"model": "claude-sonnet-5", "rationale": "trivial"},
                None,
            )

            model, verdict, error = await resolve_fixed_native_model_routing(
                "claude-sdk",
                "what testing framework does this project use?",
                session_id="sess-sdk",
            )

            assert model == "databricks-claude-sonnet-5"  # servable spelling
            assert verdict is not None and verdict["raw_model"] == "claude-sonnet-5"
            assert error is None

    async def test_unknown_harness_declines(self) -> None:
        """A harness with no candidate arms declines with an error, never crashes."""
        model, verdict, error = await resolve_fixed_native_model_routing(
            "pi-native",
            "route me",
        )
        assert model is None
        assert verdict is None
        assert error is not None and "pi-native" in error

    async def test_router_unavailable_fails_open(self) -> None:
        """Router unavailable should fail open with error string."""
        with patch("omnigent.server.smart_routing.route_session_harness") as mock_route:
            mock_route.return_value = (
                None,
                None,
                None,
                "Router not configured",
            )

            result = await resolve_fixed_native_model_routing(
                "claude-native",
                "test message",
            )

            assert result[0] is None
            assert result[1] is None
            # Should pass through the error from route_session_harness
            assert "Router not configured" in (result[2] or "")


class TestRoutingDecisionPersistence:
    """Tests for building and persisting routing decisions."""

    def test_build_decision_creates_valid_data(self) -> None:
        """Should build a valid RoutingDecisionData."""
        decision = build_decision(
            model="databricks-claude-opus-4-8",
            rationale="test rationale",
            harness="claude-native",
            scope="session",
            applied=True,
        )

        assert decision.model == "databricks-claude-opus-4-8"
        assert decision.rationale == "test rationale"
        assert decision.harness == "claude-native"
        assert decision.scope == "session"
        assert decision.applied is True
        assert decision.decision_id is not None

    def test_decision_from_route_applied(self) -> None:
        """Should create an applied decision when route resolves."""

        class MockResult:
            model = "databricks-claude-opus-4-8"
            rationale = "test"
            harness = "claude-native"

        resolved = ResolvedRoute(
            model="databricks-claude-opus-4-8",
            harness="claude-native",
            raw_model=None,
        )

        decision = decision_from_route(
            MockResult(),
            resolved,
            scope="session",
        )

        assert decision.model == "databricks-claude-opus-4-8"
        assert decision.applied is True
        assert decision.harness == "claude-native"

    def test_decision_from_route_declined(self) -> None:
        """Should create an unapplied decision when route declines."""

        class MockResult:
            model = "databricks-claude-opus-4-8"
            rationale = "test"
            harness = "claude-native"

        decision = decision_from_route(
            MockResult(),
            None,  # Declined
            scope="session",
        )

        assert decision.model == "databricks-claude-opus-4-8"
        assert decision.applied is False
        assert decision.harness is None
