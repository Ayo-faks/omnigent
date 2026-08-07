"""Unit tests for the gateway servlet's catalog translation and state file."""

from __future__ import annotations

import os

import pytest

from omnigent.gateway.auth import databrickscfg_host_for_profile
from omnigent.gateway.catalog import (
    build_models_response,
    catalog_etag,
    codex_slug,
    dumps_catalog,
    picker_options,
    routable_models,
)
from omnigent.gateway.state import (
    ServletState,
    clear_servlet_state,
    read_servlet_state,
    read_session_registry,
    write_servlet_state,
    write_session_registry,
)


@pytest.mark.parametrize(
    ("service_id", "expected"),
    [
        ("system.ai.gpt-5-6-sol", "gpt-5.6-sol"),
        ("system.ai.gpt-5-5", "gpt-5.5"),
        ("system.ai.gpt-5-5-pro", "gpt-5.5-pro"),
        ("system.ai.gpt-5", "gpt-5"),
        ("system.ai.gpt-5-mini", "gpt-5-mini"),
        ("system.ai.gpt-5-4-nano", "gpt-5.4-nano"),
        ("system.ai.gpt-5-3-codex", "gpt-5.3-codex"),
        # Non-mainline ids stay verbatim (no native Codex metadata exists).
        ("system.ai.gpt-oss-120b", "system.ai.gpt-oss-120b"),
        ("system.ai.glm-5-2", "glm-5-2"),
    ],
)
def test_codex_slug(service_id: str, expected: str) -> None:
    assert codex_slug(service_id) == expected


def _native_catalog() -> dict:
    levels = [{"effort": e} for e in ("low", "medium", "high", "xhigh")]
    return {
        "models": [
            {
                "slug": "gpt-5.6-sol",
                "display_name": "GPT-5.6 Sol",
                "description": "Latest frontier agentic coding model.",
                "supported_reasoning_levels": levels,
                "default_reasoning_level": "medium",
                "priority": 0,
                "visibility": "list",
                "base_instructions": "instr",
            },
            {
                "slug": "gpt-5.5",
                "display_name": "GPT-5.5",
                "description": "Frontier model.",
                "supported_reasoning_levels": levels,
                "default_reasoning_level": "medium",
                "priority": 1,
                "visibility": "list",
                "base_instructions": "instr",
            },
        ]
    }


def test_build_models_response_orders_and_enriches() -> None:
    service_ids = [
        "system.ai.glm-5-2",
        "system.ai.gpt-5-5",
        "system.ai.gpt-5-6-luna",
        "system.ai.gpt-5-6-sol",
    ]
    response = build_models_response(service_ids, _native_catalog())
    assert response is not None
    slugs = [m["slug"] for m in response["models"]]
    # Native-priority order first (sol, 5.5), then unknown mainline
    # newest-first (luna), then non-mainline verbatim ids.
    assert slugs == ["gpt-5.6-sol", "gpt-5.5", "gpt-5.6-luna", "glm-5-2"]
    assert [m["priority"] for m in response["models"]] == [0, 1, 2, 3]
    sol = response["models"][0]
    assert sol["display_name"] == "GPT-5.6 Sol"
    assert sol["description"] == "Databricks AI Gateway (system.ai.gpt-5-6-sol)"
    luna = response["models"][2]
    # Synthesized entry: template clone with a clamped effort ladder.
    assert luna["display_name"] == "gpt-5.6-luna"
    assert "system.ai.gpt-5-6-luna" in luna["description"]
    assert [lvl["effort"] for lvl in luna["supported_reasoning_levels"]] == [
        "low",
        "medium",
        "high",
    ]
    assert luna["default_reasoning_level"] == "medium"

    options = picker_options(response)
    assert options[0] == {
        "id": "gpt-5.6-sol",
        "displayName": "GPT-5.6 Sol",
        "isDefault": True,
    }
    assert all("isDefault" not in option for option in options[1:])
    assert routable_models(response) == slugs


def test_build_models_response_requires_native_and_inventory() -> None:
    assert build_models_response(["system.ai.gpt-5-5"], None) is None
    assert build_models_response(["system.ai.gpt-5-5"], {"models": []}) is None
    assert build_models_response([], _native_catalog()) is None


def test_catalog_etag_deterministic() -> None:
    response = build_models_response(["system.ai.gpt-5-5"], _native_catalog())
    assert response is not None
    first = catalog_etag(dumps_catalog(response))
    second = catalog_etag(dumps_catalog(response))
    assert first == second
    assert first.startswith('"') and first.endswith('"')


def test_servlet_state_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert read_servlet_state() is None
    pid = os.getpid()
    write_servlet_state(ServletState(url="http://127.0.0.1:5", admin_token="t", pid=pid))
    state = read_servlet_state()
    assert state == ServletState(url="http://127.0.0.1:5", admin_token="t", pid=pid)
    # A different owner must not clear a newer daemon's file.
    clear_servlet_state(owner_pid=999)
    assert read_servlet_state() is not None
    clear_servlet_state(owner_pid=pid)
    assert read_servlet_state() is None


def test_servlet_state_stale_owner_reads_absent(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    write_servlet_state(ServletState(url="http://127.0.0.1:6768", admin_token="t", pid=4))
    monkeypatch.setattr("omnigent.gateway.state._pid_alive", lambda pid: False)
    # Launchers see no servlet (fall open without a connect timeout)…
    assert read_servlet_state() is None
    # …but the next daemon start can still read the port to reclaim it,
    # and the dead owner's pid still matches for retraction.
    stale = read_servlet_state(allow_stale=True)
    assert stale is not None and stale.url.endswith(":6768")
    clear_servlet_state(owner_pid=4)
    assert read_servlet_state(allow_stale=True) is None


def test_session_registry_roundtrip_and_cap(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert read_session_registry() == {}
    write_session_registry(
        {
            "tok1": {"profile": "oss", "workspace_host": "https://a.example"},
            "bad": {"profile": ""},  # dropped on read
        }
    )
    assert read_session_registry() == {
        "tok1": {"profile": "oss", "workspace_host": "https://a.example"}
    }
    # Cap keeps the newest entries (insertion order).
    write_session_registry(
        {f"tok{i}": {"profile": "p", "workspace_host": "https://a.example"} for i in range(600)}
    )
    loaded = read_session_registry()
    assert len(loaded) == 512
    assert "tok599" in loaded and "tok0" not in loaded


def test_registry_restores_into_new_servlet(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    from omnigent.gateway.servlet import GatewayServlet

    first = GatewayServlet()
    session = first.register_session("oss", "https://ws.example")
    # A fresh servlet (post-restart) restores the same token → session map.
    second = GatewayServlet()
    restored = second._sessions[session.token]
    assert restored.profile == "oss"
    assert restored.upstream_base == "https://ws.example/ai-gateway/codex/v1"


def test_databrickscfg_host_for_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert databrickscfg_host_for_profile("oss") is None
    (tmp_path / ".databrickscfg").write_text(
        "[oss]\nhost = https://ws.example/\nauth_type = databricks-cli\n"
    )
    assert databrickscfg_host_for_profile("oss") == "https://ws.example"
    assert databrickscfg_host_for_profile("missing") is None


def test_fetch_includes_translated_arms(monkeypatch) -> None:
    """Chat-only ids in the translated-arm set are served; other chat-only
    ids stay excluded."""
    import asyncio

    from omnigent.gateway.catalog import fetch_codex_service_ids

    payload = {
        "model_services": [
            {
                "name": "model-services/system.ai.gpt-5-6-sol",
                "supported_api_types": ["mlflow/v1/chat/completions", "openai/v1/responses"],
            },
            {
                "name": "model-services/system.ai.glm-5-2",
                "supported_api_types": ["mlflow/v1/chat/completions"],
            },
            {
                "name": "model-services/system.ai.llama-4-maverick",
                "supported_api_types": ["mlflow/v1/chat/completions"],
            },
        ]
    }

    class _Resp:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return payload

    class _Client:
        async def get(self, url: str, params: dict, headers: dict) -> _Resp:
            return _Resp()

    ids = asyncio.run(fetch_codex_service_ids(_Client(), "https://ws.example", "tok"))
    assert ids == ["system.ai.glm-5-2", "system.ai.gpt-5-6-sol"]


def test_glm_arm_row_is_verbatim_and_never_default() -> None:
    response = build_models_response(
        ["system.ai.gpt-5-6-sol", "system.ai.glm-5-2"], _native_catalog()
    )
    assert response is not None
    slugs = [m["slug"] for m in response["models"]]
    assert slugs == ["gpt-5.6-sol", "glm-5-2"]
    glm = response["models"][1]
    assert glm["display_name"] == "glm-5-2"
    assert [lvl["effort"] for lvl in glm["supported_reasoning_levels"]] == [
        "low",
        "medium",
        "high",
    ]
    options = picker_options(response)
    assert options[0]["isDefault"] is True and options[0]["id"] == "gpt-5.6-sol"
    assert options[1] == {"id": "glm-5-2", "displayName": "glm-5-2"}


def test_normalize_relay_model_body_translates_bare_arms() -> None:
    import json as _json

    from omnigent.gateway.catalog import normalize_relay_model_body

    out = normalize_relay_model_body(b'{"model": "glm-5-2", "stream": true}')
    assert _json.loads(out)["model"] == "system.ai.glm-5-2"
    for untouched in (b'{"model": "gpt-5.6-sol"}', b"not json", b"{}"):
        assert normalize_relay_model_body(untouched) == untouched
