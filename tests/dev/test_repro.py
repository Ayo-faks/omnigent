"""Tests for the repro-agent driver's CI real-turn setup (``dev/repro.py``)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import dev.repro as repro
from dev.repro import build_ci_provider_config, configure_ci_real_turns
from omnigent.onboarding.provider_config import default_provider_for_harness, load_config


def _ci_env() -> dict[str, str]:
    return {
        "GATEWAY_BASE_URL": "https://workspace.example.com/serving-endpoints",
        "LLM_API_KEY": "secret-repro-token",
        "OMNIGENT_CI_ANTHROPIC_MODEL": "databricks-claude-sonnet",
        "OMNIGENT_CI_OPENAI_MODEL": "databricks-gpt",
    }


def test_build_ci_provider_config_routes_all_repro_harness_families() -> None:
    config = build_ci_provider_config(_ci_env())

    provider = config["providers"]["repro-ci-gateway"]  # type: ignore[index]
    assert provider["default"] == ["anthropic", "openai"]
    assert provider["anthropic"] == {
        "base_url": "https://workspace.example.com/serving-endpoints/anthropic",
        "api_key_ref": "env:LLM_API_KEY",
        "models": {"default": "databricks-claude-sonnet"},
    }
    assert provider["openai"] == {
        "base_url": "https://workspace.example.com/ai-gateway/codex/v1",
        "api_key_ref": "env:LLM_API_KEY",
        "wire_api": "responses",
        "models": {"default": "databricks-gpt"},
    }


@pytest.mark.parametrize(
    "missing",
    [
        "GATEWAY_BASE_URL",
        "LLM_API_KEY",
        "OMNIGENT_CI_ANTHROPIC_MODEL",
        "OMNIGENT_CI_OPENAI_MODEL",
    ],
)
def test_build_ci_provider_config_fails_closed_when_credential_is_missing(
    missing: str,
) -> None:
    env = _ci_env()
    env[missing] = ""

    with pytest.raises(ValueError, match=missing):
        build_ci_provider_config(env)


@pytest.mark.parametrize("gateway", ["workspace.example.com", "file:///tmp/gateway", ""])
def test_build_ci_provider_config_rejects_invalid_gateway_url(gateway: str) -> None:
    env = _ci_env()
    env["GATEWAY_BASE_URL"] = gateway

    with pytest.raises(ValueError, match="GATEWAY_BASE_URL"):
        build_ci_provider_config(env)


def test_configure_ci_real_turns_keeps_token_out_of_config(tmp_path: Path) -> None:
    env = _ci_env()
    env["OMNIGENT_RUNNER_ENV_PASSTHROUGH"] = "DATABRICKS_LINEAR_API_KEY"

    configure_ci_real_turns(env, tmp_path)

    config_text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "secret-repro-token" not in config_text
    assert json.loads(config_text) == build_ci_provider_config(env)
    assert env["OMNIGENT_CONFIG_HOME"] == str(tmp_path)
    assert env["REPRO_AGENT_REAL_TURNS"] == "1"
    assert env["OMNIGENT_RUNNER_ENV_PASSTHROUGH"].split(",") == [
        "DATABRICKS_LINEAR_API_KEY",
        "REPRO_AGENT_REAL_TURNS",
    ]


def test_configure_ci_real_turns_does_not_duplicate_passthrough_marker(
    tmp_path: Path,
) -> None:
    env = _ci_env()
    env["OMNIGENT_RUNNER_ENV_PASSTHROUGH"] = "REPRO_AGENT_REAL_TURNS"

    configure_ci_real_turns(env, tmp_path)

    assert env["OMNIGENT_RUNNER_ENV_PASSTHROUGH"] == "REPRO_AGENT_REAL_TURNS"


def test_ci_real_turns_rejects_remote_server_before_creating_worktree(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        repro,
        "_parse_args",
        lambda: argparse.Namespace(
            bug_url="OMNI-4278",
            server="https://remote.example.com",
            public=False,
            ci_real_turns=True,
        ),
    )

    with pytest.raises(SystemExit, match="1"):
        repro.main()

    assert "cannot be combined with --server" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("harness", "family"),
    [
        ("claude-native", "anthropic"),
        ("codex-native", "openai"),
        ("openai-agents", "openai"),
    ],
)
def test_ci_config_resolves_for_each_real_turn_harness(
    harness: str,
    family: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _ci_env()
    configure_ci_real_turns(env, tmp_path)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    provider = default_provider_for_harness(load_config(), harness)

    assert provider is not None
    resolved = provider.family(family)
    assert resolved is not None
    assert resolved.api_key == "secret-repro-token"
