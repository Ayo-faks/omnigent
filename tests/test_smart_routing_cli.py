"""Unit tests for CLI-side smart routing."""

from unittest.mock import MagicMock, Mock, patch

import click
import httpx
import pytest

from omnigent.smart_routing_cli import (
    AUTO_HARNESS,
    RoutingDecision,
    _gateway_state,
    check_smart_routing_available,
    create_smart_routing_session,
    known_host_id,
    smart_routing_families,
)


class TestSmartRoutingFamilies:
    """Test smart_routing_families function."""

    def test_fixed_harness_returns_single_family(self):
        """Fixed harness route checks only that harness."""
        result = smart_routing_families("claude-native")
        assert result == ("claude-native",)

    def test_fixed_codex_harness_returns_single_family(self):
        """Fixed codex harness route checks only that harness."""
        result = smart_routing_families("codex-native")
        assert result == ("codex-native",)

    def test_auto_harness_returns_both_families(self):
        """Auto route checks both families."""
        result = smart_routing_families(AUTO_HARNESS)
        assert set(result) == {"claude-native", "codex-native"}

    def test_none_harness_returns_both_families(self):
        """None harness (auto route) checks both families."""
        result = smart_routing_families(None)
        assert set(result) == {"claude-native", "codex-native"}


class TestCheckSmartRoutingAvailable:
    """Test check_smart_routing_available function."""

    def test_raises_when_server_disabled_routing(self):
        """Raises error when server reports smart_routing_enabled: false."""
        with patch("omnigent.smart_routing_cli._get_json") as mock_get:
            mock_get.return_value = {"smart_routing_enabled": False}
            with pytest.raises(click.ClickException, match="not enabled"):
                check_smart_routing_available(
                    base_url="http://localhost:6868",
                    harnesses=["claude-native"],
                )

    def test_raises_when_server_returns_no_info(self):
        """Raises error when server returns no smart_routing_enabled field."""
        with patch("omnigent.smart_routing_cli._get_json") as mock_get:
            mock_get.return_value = {}
            with pytest.raises(click.ClickException, match="not enabled"):
                check_smart_routing_available(
                    base_url="http://localhost:6868",
                    harnesses=["claude-native"],
                )

    def test_passes_with_no_host_id(self):
        """Passes preflight with no host_id (skips gateway check)."""
        with patch("omnigent.smart_routing_cli._get_json") as mock_get:
            mock_get.return_value = {"smart_routing_enabled": True}
            # Should not raise
            check_smart_routing_available(
                base_url="http://localhost:6868",
                harnesses=["claude-native"],
                host_id=None,
            )

    def test_raises_when_gateway_false_for_harness(self):
        """Raises error when gateway_inference map has false for harness."""
        with patch("omnigent.smart_routing_cli._get_json") as mock_get:
            with patch("omnigent.smart_routing_cli._gateway_inference_for_host") as mock_gw:
                mock_get.return_value = {"smart_routing_enabled": True}
                mock_gw.return_value = {"claude-native": False, "codex-native": True}
                with pytest.raises(click.ClickException, match="unavailable for claude-native"):
                    check_smart_routing_available(
                        base_url="http://localhost:6868",
                        harnesses=["claude-native"],
                        host_id="host_abc123",
                    )

    def test_passes_when_gateway_true_for_harness(self):
        """Passes preflight when gateway_inference map has true for harness."""
        with patch("omnigent.smart_routing_cli._get_json") as mock_get:
            with patch("omnigent.smart_routing_cli._gateway_inference_for_host") as mock_gw:
                mock_get.return_value = {"smart_routing_enabled": True}
                mock_gw.return_value = {"claude-native": True, "codex-native": True}
                # Should not raise
                check_smart_routing_available(
                    base_url="http://localhost:6868",
                    harnesses=["claude-native"],
                    host_id="host_abc123",
                )

    def test_passes_when_gateway_unknown_for_harness(self):
        """Passes preflight when gateway_inference map has no entry (unknown)."""
        with patch("omnigent.smart_routing_cli._get_json") as mock_get:
            with patch("omnigent.smart_routing_cli._gateway_inference_for_host") as mock_gw:
                mock_get.return_value = {"smart_routing_enabled": True}
                mock_gw.return_value = {"codex-native": True}  # claude-native absent
                # Should not raise
                check_smart_routing_available(
                    base_url="http://localhost:6868",
                    harnesses=["claude-native"],
                    host_id="host_abc123",
                )

    def test_passes_when_gateway_map_none(self):
        """Passes preflight when gateway_inference map is None (unknown)."""
        with patch("omnigent.smart_routing_cli._get_json") as mock_get:
            with patch("omnigent.smart_routing_cli._gateway_inference_for_host") as mock_gw:
                mock_get.return_value = {"smart_routing_enabled": True}
                mock_gw.return_value = None
                # Should not raise
                check_smart_routing_available(
                    base_url="http://localhost:6868",
                    harnesses=["claude-native"],
                    host_id="host_abc123",
                )


class TestCreateSmartRoutingSession:
    """Test create_smart_routing_session function."""

    def test_creates_session_with_routing_contract(self):
        """Creates session with correct routing contract fields."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "conv_abc123",
            "harness": "claude-native",
            "model_override": "databricks-claude-opus-4-8",
        }

        with patch("omnigent.smart_routing_cli.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            decision = create_smart_routing_session(
                base_url="http://localhost:6868",
                prompt="test prompt",
                harness="claude-native",
                host_id="host_abc123",
                workspace="/home/user",
            )

            # Verify request body
            call_args = mock_client.post.call_args
            assert call_args[0][0] == "/v1/sessions"
            body = call_args[1]["json"]
            assert body["cost_control_mode_override"] == "on"
            assert body["smart_routing_message"] == "test prompt"
            assert body["host_id"] == "host_abc123"
            assert body["workspace"] == "/home/user"

            # Verify decision
            assert decision.session_id == "conv_abc123"
            assert decision.harness == "claude-native"
            assert decision.model == "databricks-claude-opus-4-8"
            assert decision.notice is None

    def test_returns_unavailable_on_create_error(self):
        """Returns unavailable decision on create failure."""
        with patch("omnigent.smart_routing_cli.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.post.side_effect = httpx.ConnectError("connection failed")
            mock_client_cls.return_value = mock_client

            decision = create_smart_routing_session(
                base_url="http://localhost:6868",
                prompt="test prompt",
                harness="claude-native",
            )

            assert decision.session_id is None
            assert decision.harness is None
            assert decision.model is None
            assert "unavailable" in decision.notice.lower()

    def test_sends_no_harness_override_for_fixed_harness(self):
        """Does not send harness_override for fixed harness."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "conv_abc123",
            "harness": "claude-native",
            "model_override": "databricks-claude-opus-4-8",
        }

        with patch("omnigent.smart_routing_cli.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            create_smart_routing_session(
                base_url="http://localhost:6868",
                prompt="test prompt",
                harness="claude-native",
            )

            call_args = mock_client.post.call_args
            body = call_args[1]["json"]
            assert "harness_override" not in body

    def test_sends_auto_harness_override_when_none(self):
        """Sends harness_override: 'auto' when harness is None."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "conv_abc123",
            "harness": "codex-native",
            "model_override": "databricks-gpt-5-6-sol",
        }

        with patch("omnigent.smart_routing_cli.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            create_smart_routing_session(
                base_url="http://localhost:6868",
                prompt="test prompt",
                harness=None,
            )

            call_args = mock_client.post.call_args
            body = call_args[1]["json"]
            assert body["harness_override"] == AUTO_HARNESS


class TestKnownHostId:
    """Test known_host_id function."""

    def test_returns_none_when_host_not_in_list(self):
        """Returns None when host_id is not in the hosts list."""
        with patch("omnigent.smart_routing_cli._get_json") as mock_get:
            mock_get.return_value = {
                "hosts": [
                    {"host_id": "host_xyz789"},
                ]
            }

            result = known_host_id(
                base_url="http://localhost:6868",
                host_id="host_abc123",
            )
            assert result is None

    def test_returns_host_id_when_found(self):
        """Returns host_id when it is in the hosts list."""
        with patch("omnigent.smart_routing_cli._get_json") as mock_get:
            mock_get.return_value = {
                "hosts": [
                    {"host_id": "host_abc123", "gateway_inference": {"claude-native": True}},
                ]
            }

            result = known_host_id(
                base_url="http://localhost:6868",
                host_id="host_abc123",
            )
            assert result == "host_abc123"

    def test_returns_none_when_host_id_is_none(self):
        """Returns None when host_id input is None."""
        result = known_host_id(
            base_url="http://localhost:6868",
            host_id=None,
        )
        assert result is None


class TestGatewayState:
    """Test _gateway_state function."""

    def test_returns_value_for_canonical_harness(self):
        """Returns gateway state value for canonical harness name."""
        gateway = {"claude-native": True, "codex-native": False}
        result = _gateway_state(gateway, "claude-native")
        assert result is True

    def test_returns_value_for_alternate_spelling(self):
        """Returns gateway state value when alternate spelling is used."""
        gateway = {"native-claude": True}
        # Assuming canonicalize_harness maps native-claude to claude-native
        result = _gateway_state(gateway, "native-claude")
        # Should find it under native-claude if canonicalize_harness doesn't normalize it
        assert result is True

    def test_returns_none_when_not_found(self):
        """Returns None when harness is not in gateway map."""
        gateway = {"codex-native": True}
        result = _gateway_state(gateway, "claude-native")
        assert result is None


class TestRoutingDecision:
    """Test RoutingDecision dataclass."""

    def test_creates_decision_with_all_fields(self):
        """Creates decision with all fields populated."""
        decision = RoutingDecision(
            session_id="conv_abc123",
            harness="claude-native",
            model="databricks-claude-opus-4-8",
            notice=None,
        )
        assert decision.session_id == "conv_abc123"
        assert decision.harness == "claude-native"
        assert decision.model == "databricks-claude-opus-4-8"
        assert decision.notice is None

    def test_creates_decision_with_notice(self):
        """Creates decision with notice when routing unavailable."""
        decision = RoutingDecision(
            session_id=None,
            harness=None,
            model=None,
            notice="omnigent: Smart Routing was unavailable (reason)",
        )
        assert decision.session_id is None
        assert decision.notice is not None
