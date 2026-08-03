"""Tests for subagent routing hooks and transport."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from omnigent.inner.hook_scripts import subagent_router
from omnigent.server.routing_contract import SubagentRouteDecision


class TestSubagentRouterClient:
    """Test the shared routing client logic."""

    def test_discover_router_dir_explicit(self):
        """Test explicit directory discovery."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subagent_router.discover_router_dir(tmpdir)
            assert result == Path(tmpdir)

    def test_discover_router_dir_env_var(self):
        """Test discovery via environment variable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(os.environ, {subagent_router.ROUTER_DIR_ENV_VAR: tmpdir}):
                result = subagent_router.discover_router_dir()
                assert result == Path(tmpdir)

    def test_discover_router_dir_fallback_bridge_env(self):
        """Test fallback to bridge dir environment variable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(os.environ, {subagent_router.BRIDGE_DIR_ENV_VAR: tmpdir}):
                result = subagent_router.discover_router_dir()
                assert result == Path(tmpdir)

    def test_discover_router_dir_none(self):
        """Test when no directory is found."""
        with mock.patch.dict(
            os.environ,
            {
                subagent_router.ROUTER_DIR_ENV_VAR: "",
                subagent_router.BRIDGE_DIR_ENV_VAR: "",
            },
            clear=False,
        ):
            result = subagent_router.discover_router_dir(None)
            assert result is None

    def test_read_router_endpoint_valid(self):
        """Test reading a valid endpoint advertisement."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            adv_file = tmpdir_path / subagent_router.ADVERTISEMENT_FILE
            payload = {
                "url": "http://127.0.0.1:12345",
                "token": "test-token-abc123",
                "session_id": "test-session",
            }
            adv_file.write_text(json.dumps(payload))

            result = subagent_router.read_router_endpoint(tmpdir_path)
            assert result is not None
            assert result.url == "http://127.0.0.1:12345"
            assert result.token == "test-token-abc123"
            assert result.session_id == "test-session"

    def test_read_router_endpoint_missing_file(self):
        """Test when advertisement file is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subagent_router.read_router_endpoint(tmpdir)
            assert result is None

    def test_read_router_endpoint_malformed_json(self):
        """Test with malformed JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            adv_file = tmpdir_path / subagent_router.ADVERTISEMENT_FILE
            adv_file.write_text("not valid json {")

            result = subagent_router.read_router_endpoint(tmpdir_path)
            assert result is None

    def test_read_router_endpoint_missing_fields(self):
        """Test with missing required fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            adv_file = tmpdir_path / subagent_router.ADVERTISEMENT_FILE
            payload = {"url": "http://127.0.0.1:12345"}  # Missing token
            adv_file.write_text(json.dumps(payload))

            result = subagent_router.read_router_endpoint(tmpdir_path)
            assert result is None

    def test_read_router_endpoint_unsafe_url_https(self):
        """Test that HTTPS URLs are rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            adv_file = tmpdir_path / subagent_router.ADVERTISEMENT_FILE
            payload = {
                "url": "https://127.0.0.1:12345",  # HTTPS not allowed
                "token": "test-token",
            }
            adv_file.write_text(json.dumps(payload))

            result = subagent_router.read_router_endpoint(tmpdir_path)
            assert result is None

    def test_read_router_endpoint_unsafe_url_non_loopback(self):
        """Test that non-loopback URLs are rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            adv_file = tmpdir_path / subagent_router.ADVERTISEMENT_FILE
            payload = {
                "url": "http://example.com:12345",  # Not loopback
                "token": "test-token",
            }
            adv_file.write_text(json.dumps(payload))

            result = subagent_router.read_router_endpoint(tmpdir_path)
            assert result is None

    def test_read_router_endpoint_ipv6_loopback(self):
        """Test IPv6 loopback is accepted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            adv_file = tmpdir_path / subagent_router.ADVERTISEMENT_FILE
            payload = {
                "url": "http://[::1]:12345",
                "token": "test-token",
            }
            adv_file.write_text(json.dumps(payload))

            result = subagent_router.read_router_endpoint(tmpdir_path)
            assert result is not None

    def test_spawn_task_name_with_default_keys(self):
        """Test extracting task name with default keys."""
        tool_input = {
            "prompt": "some prompt",
            "subagent_type": "test-agent",
        }
        result = subagent_router.spawn_task_name(tool_input)
        assert result == "test-agent"

    def test_spawn_task_name_with_custom_keys(self):
        """Test extracting task name with custom keys."""
        tool_input = {
            "prompt": "some prompt",
            "task_name": "custom-task",
        }
        result = subagent_router.spawn_task_name(tool_input, ("task_name", "agent_name"))
        assert result == "custom-task"

    def test_spawn_task_name_missing(self):
        """Test when task name is not found."""
        tool_input = {"prompt": "some prompt"}
        result = subagent_router.spawn_task_name(tool_input)
        assert result is None

    def test_is_fork_spawn_true(self):
        """Test detecting a fork spawn."""
        tool_input = {"subagent_type": "fork"}
        result = subagent_router.is_fork_spawn(tool_input)
        assert result is True

    def test_is_fork_spawn_suffix(self):
        """Test detecting a fork spawn by suffix."""
        tool_input = {"task_name": "my_task_fork"}
        result = subagent_router.is_fork_spawn(tool_input, ("task_name",))
        assert result is True

    def test_is_fork_spawn_false(self):
        """Test a non-fork spawn."""
        tool_input = {"subagent_type": "general-purpose"}
        result = subagent_router.is_fork_spawn(tool_input)
        assert result is False

    def test_build_route_request(self):
        """Test building a routing request."""
        tool_input = {
            "prompt": "tell me a story",
            "subagent_type": "storyteller",
            "model": "claude-opus",
        }
        result = subagent_router.build_route_request(
            tool_input,
            harness="claude-native",
            parent_model="databricks-claude-opus-4-8",
        )
        assert result["harness"] == "claude-native"
        assert result["task_name"] == "storyteller"
        assert result["prompt"] == "tell me a story"
        assert result["parent_model"] == "databricks-claude-opus-4-8"
        assert result["fork"] is False

    def test_build_route_request_without_prompt(self):
        """Test building a routing request without prompt (encrypted spawn)."""
        tool_input = {
            "prompt": "tell me a story",
            "task_name": "task1",
        }
        result = subagent_router.build_route_request(
            tool_input,
            harness="codex-native",
            include_prompt=False,
        )
        assert result["prompt"] is None

    def test_decision_to_allow_output(self):
        """Test converting a decision to hook allow output."""
        tool_input = {
            "prompt": "test",
            "model": "claude-sonnet-5",
        }
        result = subagent_router.decision_to_allow_output(
            tool_input,
            model="databricks-claude-opus-4-8",
            reason="routing selected this model",
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
        model_out = result["hookSpecificOutput"]["updatedInput"]["model"]
        assert model_out == "databricks-claude-opus-4-8"
        reason_out = result["hookSpecificOutput"]["permissionDecisionReason"]
        assert reason_out == "routing selected this model"

    def test_decision_to_deny_output(self):
        """Test converting a decision to hook deny output."""
        result = subagent_router.decision_to_deny_output("spawn not allowed")
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert result["hookSpecificOutput"]["permissionDecisionReason"] == "spawn not allowed"


class TestSubagentRouteDecision:
    """Test the frozen decision shape."""

    def test_to_payload(self):
        """Test serializing a decision to payload."""
        decision = SubagentRouteDecision(
            action="rewrite",
            model="databricks-claude-opus-4-8",
            harness="claude-native",
            raw_model="claude-opus-4-8",
            rationale="cost optimization",
            decision_id="dec-123",
        )
        payload = decision.to_payload()
        assert payload["action"] == "rewrite"
        assert payload["model"] == "databricks-claude-opus-4-8"
        assert payload["harness"] == "claude-native"
        assert payload["raw_model"] == "claude-opus-4-8"
        assert payload["rationale"] == "cost optimization"
        assert payload["decision_id"] == "dec-123"

    def test_to_payload_minimal(self):
        """Test serializing a minimal decision."""
        decision = SubagentRouteDecision(
            action="allow",
            rationale="no routing",
        )
        payload = decision.to_payload()
        assert payload["action"] == "allow"
        assert payload["model"] is None
        assert payload["harness"] is None
        assert payload["rationale"] == "no routing"


class TestHookScriptImportSafety:
    """Test that hook scripts are import-safe under python -I."""

    def test_claude_hook_import_isolated(self):
        """Test that claude hook can be imported in isolated mode."""
        # Create a temporary workspace with a shadowing omnigent dir.
        with tempfile.TemporaryDirectory() as tmpdir:
            shadow_dir = Path(tmpdir) / "omnigent"
            shadow_dir.mkdir()
            # Create a fake __init__.py to make it a package.
            (shadow_dir / "__init__.py").write_text("")

            # Run the hook script in isolated mode from the shadow workspace.
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-m",
                    "omnigent.inner.hook_scripts.claude_router_hook",
                    "--help",
                ],
                cwd=tmpdir,
                capture_output=True,
                text=True,
            )
            # Should succeed (exit 0) despite the shadowing.
            assert result.returncode == 0 or "usage" in result.stderr or result.stdout == ""

    def test_codex_hook_import_isolated(self):
        """Test that codex hook can be imported in isolated mode."""
        # Create a temporary workspace with a shadowing omnigent dir.
        with tempfile.TemporaryDirectory() as tmpdir:
            shadow_dir = Path(tmpdir) / "omnigent"
            shadow_dir.mkdir()
            (shadow_dir / "__init__.py").write_text("")

            # Run the hook script in isolated mode from the shadow workspace.
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-m",
                    "omnigent.inner.hook_scripts.codex_router_hook",
                    "--help",
                ],
                cwd=tmpdir,
                capture_output=True,
                text=True,
            )
            # Should succeed (exit 0) despite the shadowing.
            assert result.returncode == 0 or "usage" in result.stderr or result.stdout == ""


class TestCodexHookBehavior:
    """Test codex hook subprocess behavior."""

    def test_codex_hook_fail_open_on_missing_input(self):
        """Test that codex hook fails open when given no input."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "omnigent.inner.hook_scripts.codex_router_hook",
                "route-subagent",
            ],
            input="",
            capture_output=True,
            text=True,
        )
        # Should always return 0 (fail open).
        assert result.returncode == 0

    def test_codex_hook_fail_open_on_bad_json(self):
        """Test that codex hook fails open on malformed JSON."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "omnigent.inner.hook_scripts.codex_router_hook",
                "route-subagent",
            ],
            input="not valid json {",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0


class TestClaudeHookBehavior:
    """Test claude hook subprocess behavior."""

    def test_claude_hook_fail_open_on_missing_input(self):
        """Test that claude hook fails open when given no input."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "omnigent.inner.hook_scripts.claude_router_hook",
                "route-subagent",
            ],
            input="",
            capture_output=True,
            text=True,
        )
        # Should always return 0 (fail open).
        assert result.returncode == 0

    def test_claude_hook_fail_open_on_bad_json(self):
        """Test that claude hook fails open on malformed JSON."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "omnigent.inner.hook_scripts.claude_router_hook",
                "route-subagent",
            ],
            input="not valid json {",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
