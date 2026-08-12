"""Focused parser tests for the query-aware GitHub code-search gate."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml

from omnigent.errors import OmnigentError
from omnigent.spec.parser import parse


def _config(
    *,
    sandbox_type: str = "linux_bwrap",
    gate: object | None = None,
    egress_rules: list[str] | None = None,
    credential_proxy: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "spec_version": 1,
        "name": "github-search",
        "os_env": {
            "type": "caller_process",
            "cwd": ".",
            "sandbox": {
                "type": sandbox_type,
                "egress_rules": egress_rules
                if egress_rules is not None
                else ["GET api.github.com/search/code**"],
                "credential_proxy": credential_proxy
                if credential_proxy is not None
                else [
                    {
                        "type": "https_bearer",
                        "target": "API.GitHub.com",
                        "source": {"command": "gh auth token"},
                    }
                ],
                "github_code_search": gate
                if gate is not None
                else {
                    "host": "API.GitHub.com",
                    "control_header": "X-Omnigent-GitHub-Org",
                    "organizations": ["Databricks-Eng", "databricks-security"],
                },
            },
        },
    }


def _parse_config(tmp_path: Path, config: dict[str, object]):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config))
    return parse(tmp_path)


def test_github_code_search_normalizes_to_frozen_spec(tmp_path: Path) -> None:
    spec = _parse_config(tmp_path, _config())
    gate = spec.os_env.sandbox.github_code_search

    assert gate is not None
    assert gate.host == "api.github.com"
    assert gate.control_header == "x-omnigent-github-org"
    assert gate.organizations == ("databricks-eng", "databricks-security")
    binding = spec.os_env.sandbox.credential_proxy.entries[0]
    assert binding.host == gate.host
    assert binding.scheme == "bearer"
    assert binding.source.kind == "command"
    assert binding.source.command == "gh auth token"
    assert binding.inject_env == []
    with pytest.raises(FrozenInstanceError):
        gate.host = "example.com"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("gate", "match"),
    [
        (
            {
                "host": "*.github.com",
                "control_header": "X-Org",
                "organizations": ["databricks-eng"],
            },
            "exact DNS-safe hostname",
        ),
        (
            {
                "host": "api.github.com",
                "control_header": "Bad Header",
                "organizations": ["databricks-eng"],
            },
            "valid HTTP header name",
        ),
        (
            {
                "host": "api.github.com",
                "control_header": "X-Org",
                "organizations": [],
            },
            "non-empty list",
        ),
        (
            {
                "host": "api.github.com",
                "control_header": "X-Org",
                "organizations": ["Databricks-Eng", "databricks-eng"],
            },
            "duplicate",
        ),
        (
            {
                "host": "api.github.com",
                "control_header": "X-Org",
                "organizations": ["databricks/eng"],
            },
            "organizations entries",
        ),
    ],
)
def test_github_code_search_rejects_malformed_gate(
    tmp_path: Path,
    gate: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(OmnigentError, match=match):
        _parse_config(tmp_path, _config(gate=gate))


@pytest.mark.parametrize(
    "egress_rules",
    [
        ["GET api.github.com/search/code"],
        ["* api.github.com/search/code**"],
        ["GET *.github.com/search/code**"],
        ["GET api.github.com/repos/**"],
    ],
)
def test_github_code_search_requires_separate_exact_get_rule(
    tmp_path: Path,
    egress_rules: list[str],
) -> None:
    with pytest.raises(OmnigentError, match="exact-host GET egress rule"):
        _parse_config(tmp_path, _config(egress_rules=egress_rules))


@pytest.mark.parametrize(
    "credential_proxy",
    [
        [{"type": "https_bearer", "target": "github.com", "source": {"env": "TOKEN"}}],
        [{"type": "https_basic", "target": "api.github.com", "source": {"env": "TOKEN"}}],
    ],
)
def test_github_code_search_requires_matching_bearer_binding(
    tmp_path: Path,
    credential_proxy: list[dict[str, object]],
) -> None:
    with pytest.raises(OmnigentError, match="https_bearer entry"):
        _parse_config(tmp_path, _config(credential_proxy=credential_proxy))


def test_github_code_search_requires_hardened_sandbox(tmp_path: Path) -> None:
    with pytest.raises(OmnigentError, match=r"requires sandbox.type"):
        _parse_config(tmp_path, _config(sandbox_type="none"))


def test_github_code_search_accepts_https_bearer_on_macos(tmp_path: Path) -> None:
    spec = _parse_config(tmp_path, _config(sandbox_type="darwin_seatbelt"))
    gate = spec.os_env.sandbox.github_code_search

    assert gate is not None
    assert gate.host == "api.github.com"
    assert spec.os_env.sandbox.credential_proxy.entries[0].inject_env == []


def test_github_code_search_rejected_on_inherited_terminal(tmp_path: Path) -> None:
    config = _config()
    config["terminals"] = {"shell": {"command": "bash", "os_env": "inherit"}}

    with pytest.raises(OmnigentError, match="github_code_search is not supported"):
        _parse_config(tmp_path, config)
