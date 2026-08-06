"""Unit tests for the gateway servlet's catalog translation and state file."""

from __future__ import annotations

import pytest

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
    write_servlet_state,
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
        ("system.ai.glm-5-2", "system.ai.glm-5-2"),
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
    assert slugs == ["gpt-5.6-sol", "gpt-5.5", "gpt-5.6-luna", "system.ai.glm-5-2"]
    assert [m["priority"] for m in response["models"]] == [0, 1, 2, 3]
    sol = response["models"][0]
    assert sol["display_name"] == "GPT-5.6 Sol"
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
    write_servlet_state(ServletState(url="http://127.0.0.1:5", admin_token="t", pid=123))
    state = read_servlet_state()
    assert state == ServletState(url="http://127.0.0.1:5", admin_token="t", pid=123)
    # A different owner must not clear a newer daemon's file.
    clear_servlet_state(owner_pid=999)
    assert read_servlet_state() is not None
    clear_servlet_state(owner_pid=123)
    assert read_servlet_state() is None
