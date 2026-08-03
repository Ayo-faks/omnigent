"""Unit tests for subagent routing policy."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from omnigent.server.routing_contract import SubagentRouteDecision
from omnigent.server.subagent_routing_policy import resolve_subagent_route
from omnigent.server.subagent_routing_transport import SubagentRouteRequest


@pytest.fixture
def mock_caps():
    """Mock RuntimeCaps with a routing client."""
    caps = MagicMock()
    client = AsyncMock()
    caps.routing_client = client
    return caps


@pytest.mark.asyncio
async def test_subagent_routing_override_off(mock_caps):
    """When override is 'off', spawn is allowed unchanged (advisory gate)."""
    req = SubagentRouteRequest(
        harness="claude-native",
        task_name="test-task",
        prompt="test prompt",
    )

    decision = await resolve_subagent_route(
        session_id="test-session",
        req=req,
        subagent_routing_override="off",
        cost_control_mode="on",
        auto_harness=False,
        caps=mock_caps,
    )

    assert decision.action == "allow"
    assert "disabled" in decision.rationale.lower()
    # Router should NOT be called
    assert not mock_caps.routing_client.route.called


@pytest.mark.asyncio
async def test_subagent_routing_override_on_inherits_cost_control(mock_caps):
    """When override is 'on', routing is enabled (inherits cost_control_mode)."""
    mock_caps.routing_client.route = AsyncMock(
        return_value=MagicMock(
            model="databricks-claude-sonnet-5",
            rationale="test pick",
        )
    )

    req = SubagentRouteRequest(
        harness="claude-native",
        task_name="test-task",
        prompt="test prompt",
    )

    decision = await resolve_subagent_route(
        session_id="test-session",
        req=req,
        subagent_routing_override="on",
        cost_control_mode="off",  # Off at session level
        auto_harness=False,
        caps=mock_caps,
        available_models={"claude-native": ["databricks-claude-sonnet-5"]},
    )

    # Should route because override is "on"
    assert mock_caps.routing_client.route.called
    assert decision.action == "rewrite"


@pytest.mark.asyncio
async def test_subagent_routing_disabled_by_cost_control(mock_caps):
    """When cost_control_mode is off and override is not 'on', no routing."""
    req = SubagentRouteRequest(
        harness="claude-native",
        task_name="test-task",
        prompt="test prompt",
    )

    decision = await resolve_subagent_route(
        session_id="test-session",
        req=req,
        subagent_routing_override=None,  # Will inherit
        cost_control_mode="off",
        auto_harness=False,
        caps=mock_caps,
    )

    assert decision.action == "allow"
    assert not mock_caps.routing_client.route.called


@pytest.mark.asyncio
async def test_fork_exemption(mock_caps):
    """Forks are not routed; they keep the parent model."""
    req = SubagentRouteRequest(
        harness="claude-native",
        task_name="test-task",
        prompt="test prompt",
        fork=True,
        parent_model="databricks-claude-opus-4-8",
    )

    decision = await resolve_subagent_route(
        session_id="test-session",
        req=req,
        cost_control_mode="on",
        auto_harness=False,
        caps=mock_caps,
    )

    assert decision.action == "allow"
    assert decision.model == "databricks-claude-opus-4-8"
    assert "fork" in decision.rationale.lower()
    assert not mock_caps.routing_client.route.called


@pytest.mark.asyncio
async def test_no_routable_signal_codex_unnamed(mock_caps):
    """Codex spawns with no task_name and no prompt signal → inherit model."""
    req = SubagentRouteRequest(
        harness="codex-native",
        task_name="",  # No task name
        prompt=None,  # No prompt (codex encrypts it)
        parent_model="databricks-gpt-5-6-luna",
    )

    decision = await resolve_subagent_route(
        session_id="test-session",
        req=req,
        cost_control_mode="on",
        auto_harness=False,
        caps=mock_caps,
    )

    assert decision.action == "allow"
    assert decision.model == "databricks-gpt-5-6-luna"
    assert "no routable signal" in decision.rationale.lower()
    assert not mock_caps.routing_client.route.called


@pytest.mark.asyncio
async def test_no_routing_client(mock_caps):
    """No routing client → allow unchanged (advisory)."""
    mock_caps.routing_client = None

    req = SubagentRouteRequest(
        harness="claude-native",
        task_name="test-task",
        prompt="test prompt",
    )

    decision = await resolve_subagent_route(
        session_id="test-session",
        req=req,
        cost_control_mode="on",
        auto_harness=False,
        caps=mock_caps,
    )

    assert decision.action == "allow"
    assert "no routing client" in decision.rationale.lower()


@pytest.mark.asyncio
async def test_router_call_fails(mock_caps):
    """Router outage → allow unchanged (advisory, fail-open)."""
    mock_caps.routing_client.route = AsyncMock(side_effect=RuntimeError("Connection failed"))

    req = SubagentRouteRequest(
        harness="claude-native",
        task_name="test-task",
        prompt="test prompt",
    )

    decision = await resolve_subagent_route(
        session_id="test-session",
        req=req,
        cost_control_mode="on",
        auto_harness=False,
        caps=mock_caps,
        available_models={"claude-native": ["databricks-claude-sonnet-5"]},
    )

    assert decision.action == "allow"
    assert "unavailable" in decision.rationale.lower()


@pytest.mark.asyncio
async def test_router_returns_no_model(mock_caps):
    """Router verdict missing model → allow unchanged."""
    mock_caps.routing_client.route = AsyncMock(
        return_value=MagicMock(model=None, rationale="no pick")
    )

    req = SubagentRouteRequest(
        harness="claude-native",
        task_name="test-task",
        prompt="test prompt",
    )

    decision = await resolve_subagent_route(
        session_id="test-session",
        req=req,
        cost_control_mode="on",
        auto_harness=False,
        caps=mock_caps,
        available_models={"claude-native": ["databricks-claude-sonnet-5"]},
    )

    assert decision.action == "allow"
    assert "unavailable" in decision.rationale.lower()


@pytest.mark.asyncio
async def test_same_family_rewrite(mock_caps):
    """Claude parent → Claude routed model → rewrite (same harness)."""
    mock_caps.routing_client.route = AsyncMock(
        return_value=MagicMock(
            model="databricks-claude-opus-4-8",
            harness="claude-native",
            rationale="escalate to opus",
        )
    )

    req = SubagentRouteRequest(
        harness="claude-native",
        task_name="complex-task",
        prompt="complex prompt" * 100,
    )

    decision = await resolve_subagent_route(
        session_id="test-session",
        req=req,
        cost_control_mode="on",
        auto_harness=False,
        caps=mock_caps,
        available_models={
            "claude-native": [
                "databricks-claude-sonnet-5",
                "databricks-claude-opus-4-8",
            ],
        },
    )

    assert decision.action == "rewrite"
    assert decision.model == "databricks-claude-opus-4-8"
    assert decision.harness is None  # Same harness not named


@pytest.mark.asyncio
async def test_same_family_allow_parent_model(mock_caps):
    """Claude parent → router picks parent model → allow (no change)."""
    parent_model = "databricks-claude-sonnet-5"
    mock_caps.routing_client.route = AsyncMock(
        return_value=MagicMock(
            model=parent_model,
            harness="claude-native",
            rationale="keep parent",
        )
    )

    req = SubagentRouteRequest(
        harness="claude-native",
        task_name="trivial-task",
        prompt="hi",
        parent_model=parent_model,
    )

    decision = await resolve_subagent_route(
        session_id="test-session",
        req=req,
        cost_control_mode="on",
        auto_harness=False,
        caps=mock_caps,
        available_models={
            "claude-native": ["databricks-claude-sonnet-5"],
        },
    )

    assert decision.action == "allow"
    assert decision.model == parent_model


@pytest.mark.asyncio
async def test_cross_family_redirect_auto_allowed(mock_caps):
    """Auto harness: Claude parent → Codex pick → redirect (cross-family)."""
    mock_caps.routing_client.route = AsyncMock(
        return_value=MagicMock(
            model="databricks-gpt-5-6-luna",
            harness="codex-native",
            rationale="delegate to codex",
        )
    )

    req = SubagentRouteRequest(
        harness="claude-native",
        task_name="test-task",
        prompt="test prompt",
    )

    decision = await resolve_subagent_route(
        session_id="test-session",
        req=req,
        cost_control_mode="on",
        auto_harness=True,  # Auto mode allows cross-family
        caps=mock_caps,
        available_models={
            "claude-native": ["databricks-claude-sonnet-5"],
            "codex-native": ["databricks-gpt-5-6-luna"],
        },
    )

    assert decision.action == "redirect"
    assert decision.model == "databricks-gpt-5-6-luna"
    assert decision.harness == "codex-native"


@pytest.mark.asyncio
async def test_cross_family_deny_non_auto(mock_caps):
    """Non-auto Claude session: cross-family pick → deny."""
    mock_caps.routing_client.route = AsyncMock(
        return_value=MagicMock(
            model="databricks-gpt-5-6-luna",
            harness="codex-native",
            rationale="delegate to codex",
        )
    )

    req = SubagentRouteRequest(
        harness="claude-native",
        task_name="test-task",
        prompt="test prompt",
    )

    decision = await resolve_subagent_route(
        session_id="test-session",
        req=req,
        cost_control_mode="on",
        auto_harness=False,  # Not auto → no cross-family
        caps=mock_caps,
        available_models={
            "claude-native": ["databricks-claude-sonnet-5"],
            "codex-native": ["databricks-gpt-5-6-luna"],
        },
    )

    assert decision.action == "deny"
    assert "cross-family" in decision.rationale.lower()


@pytest.mark.asyncio
async def test_unoffered_model_deny(mock_caps):
    """Router picks a model not in the offered set → deny."""
    mock_caps.routing_client.route = AsyncMock(
        return_value=MagicMock(
            model="databricks-claude-opus-5",  # Not in offered set
            harness="claude-native",
            rationale="pick opus5",
        )
    )

    req = SubagentRouteRequest(
        harness="claude-native",
        task_name="test-task",
        prompt="test prompt",
    )

    decision = await resolve_subagent_route(
        session_id="test-session",
        req=req,
        cost_control_mode="on",
        auto_harness=False,
        caps=mock_caps,
        available_models={
            "claude-native": ["databricks-claude-sonnet-5"],
        },
    )

    assert decision.action == "deny"
    assert "cannot run" in decision.rationale.lower()


def test_decision_to_payload():
    """SubagentRouteDecision.to_payload() serializes correctly."""
    decision = SubagentRouteDecision(
        action="rewrite",
        rationale="test",
        model="databricks-claude-sonnet-5",
        harness=None,
        raw_model=None,
        decision_id="test-id",
    )

    payload = decision.to_payload()

    assert payload["action"] == "rewrite"
    assert payload["rationale"] == "test"
    assert payload["model"] == "databricks-claude-sonnet-5"
    assert payload["harness"] is None
    assert payload["decision_id"] == "test-id"
