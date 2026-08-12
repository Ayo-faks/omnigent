"""Regression tests for typed OS-environment harness serialization."""

from __future__ import annotations

import importlib

import pytest

from omnigent.inner.datamodel import (
    CredentialProxyEntry,
    CredentialProxySpec,
    CredentialSourceSpec,
    GitHubCodeSearchSpec,
    OSEnvSandboxSpec,
    OSEnvSpec,
)
from omnigent.runtime.workflow import _serialize_os_env


@pytest.mark.parametrize(
    "module_name",
    [
        "acp_harness",
        "claude_sdk_harness",
        "codex_harness",
        "copilot_harness",
        "cursor_harness",
        "goose_harness",
        "hermes_harness",
        "kimi_harness",
        "pi_harness",
        "qwen_harness",
    ],
)
def test_harness_os_env_round_trip_preserves_nested_specs(
    module_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = OSEnvSpec(
        cwd="/workspace",
        sandbox=OSEnvSandboxSpec(
            type="linux_bwrap",
            egress_rules=["GET api.github.com/search/code**"],
            credential_proxy=CredentialProxySpec(
                entries=[
                    CredentialProxyEntry(
                        host="api.github.com",
                        scheme="bearer",
                        source=CredentialSourceSpec(kind="command", command="gh auth token"),
                    )
                ]
            ),
            github_code_search=GitHubCodeSearchSpec(
                host="api.github.com",
                control_header="x-omnigent-github-org",
                organizations=("databricks-eng",),
            ),
        ),
        start_in_scratch=True,
    )
    serialized = _serialize_os_env(original)
    assert serialized is not None
    module = importlib.import_module(f"omnigent.inner.{module_name}")
    monkeypatch.setenv(module._ENV_OS_ENV, serialized)

    resolved = module._resolve_os_env()

    assert resolved == original
    assert isinstance(resolved.sandbox.credential_proxy, CredentialProxySpec)
    assert isinstance(resolved.sandbox.github_code_search, GitHubCodeSearchSpec)
