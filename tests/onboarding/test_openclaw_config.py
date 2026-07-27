from __future__ import annotations

from pathlib import Path

from omnigent.onboarding.acp_auth import AcpAgentEntry, acp_agents_settings
from omnigent.onboarding.openclaw_config import (
    discover_openclaw_agents,
    merge_imported_acp_entries,
    openclaw_agents_to_acp_entries,
)


def test_discovers_acpx_config(tmp_path: Path) -> None:
    acpx = tmp_path / "config.json"
    acpx.write_text(
        """
        {
          "agents": {
            "Gemini CLI": {"command": "gemini", "args": ["--experimental-acp"]},
            "Broken": {"args": ["missing-command"]}
          }
        }
        """,
        encoding="utf-8",
    )

    discovery = discover_openclaw_agents(acpx_path=acpx, openclaw_path=tmp_path / "missing.json")

    assert discovery.errors == ()
    assert [(agent.name, agent.command_line, agent.source) for agent in discovery.agents] == [
        ("Gemini CLI", "gemini --experimental-acp", "acpx")
    ]


def test_discovers_openclaw_wrapped_json5_config(tmp_path: Path) -> None:
    openclaw = tmp_path / "openclaw.json"
    openclaw.write_text(
        """
        {
          // OpenClaw wraps the acpx registry under plugins.entries.acpx.config.
          plugins: {
            entries: {
              acpx: {
                config: {
                  agents: {
                    'Claude Code': {
                      command: 'npx',
                      args: ['-y', '@zed-industries/claude-code-acp'],
                    },
                  },
                },
              },
            },
          },
        }
        """,
        encoding="utf-8",
    )

    discovery = discover_openclaw_agents(
        acpx_path=tmp_path / "missing.json", openclaw_path=openclaw
    )

    assert discovery.errors == ()
    assert [(agent.name, agent.command_line, agent.source) for agent in discovery.agents] == [
        ("Claude Code", "npx -y @zed-industries/claude-code-acp", "openclaw")
    ]


def test_discovers_both_config_shapes(tmp_path: Path) -> None:
    acpx = tmp_path / "acpx.json"
    openclaw = tmp_path / "openclaw.json"
    acpx.write_text(
        '{"agents": {"Qwen Code": {"command": "qwen", "args": ["--acp"]}}}',
        encoding="utf-8",
    )
    openclaw.write_text(
        "{plugins: {entries: {acpx: {config: {agents: "
        '{Goose: {command: "goose", args: ["acp"]}}}}}}}',
        encoding="utf-8",
    )

    discovery = discover_openclaw_agents(acpx_path=acpx, openclaw_path=openclaw)

    assert discovery.errors == ()
    assert [(agent.name, agent.command_line) for agent in discovery.agents] == [
        ("Qwen Code", "qwen --acp"),
        ("Goose", "goose acp"),
    ]


def test_malformed_config_is_soft_error(tmp_path: Path) -> None:
    acpx = tmp_path / "config.json"
    acpx.write_text('{"agents": ', encoding="utf-8")

    discovery = discover_openclaw_agents(acpx_path=acpx, openclaw_path=tmp_path / "missing.json")

    assert discovery.agents == ()
    assert len(discovery.errors) == 1
    assert discovery.errors[0].path == acpx
    assert "invalid JSON" in discovery.errors[0].message


def test_empty_configs_return_no_agents(tmp_path: Path) -> None:
    acpx = tmp_path / "config.json"
    openclaw = tmp_path / "openclaw.json"
    acpx.write_text('{"agents": {}}', encoding="utf-8")
    openclaw.write_text("{plugins: {entries: {acpx: {config: {}}}}}", encoding="utf-8")

    discovery = discover_openclaw_agents(acpx_path=acpx, openclaw_path=openclaw)

    assert discovery.agents == ()
    assert discovery.errors == ()


def test_translator_emits_acp_agents_settings(tmp_path: Path) -> None:
    acpx = tmp_path / "config.json"
    acpx.write_text(
        '{"agents": {"Gemini CLI": {"command": "gemini", "args": ["--experimental-acp"]}}}',
        encoding="utf-8",
    )

    entries = openclaw_agents_to_acp_entries(
        discover_openclaw_agents(acpx_path=acpx, openclaw_path=tmp_path / "missing.json").agents
    )

    assert entries == [
        AcpAgentEntry(slug="gemini-cli", name="Gemini CLI", command="gemini --experimental-acp")
    ]
    assert acp_agents_settings(entries) == {
        "acp": {"agents": [{"name": "Gemini CLI", "command": "gemini --experimental-acp"}]}
    }


def test_import_merge_is_idempotent_by_slug() -> None:
    imported = [
        AcpAgentEntry(slug="gemini-cli", name="Gemini CLI", command="gemini --experimental-acp")
    ]

    merged, added = merge_imported_acp_entries(imported, existing=[])
    rerun_merged, rerun_added = merge_imported_acp_entries(imported, existing=merged)

    assert added == imported
    assert rerun_added == []
    assert rerun_merged == merged
