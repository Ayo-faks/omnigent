"""Regression test :
``pass_history`` and ``max_sessions`` declared on an inline
``type: agent`` sub-agent tool in a YAML spec are silently
dropped after ``load() + agent_spec_to_agent_def()``.

Root cause: the Omnigent ``AgentSpec`` type has no fields for
``pass_history``, ``pass_histories``, or ``max_sessions``, so
the YAML parser discards them when it builds the ``AgentSpec``
intermediate for the sub-agent. ``_sub_spec_to_agent_tool``
then reconstructs an ``AgentTool`` with the defaults
(``pass_history=False``, ``max_sessions=None``) even though
the YAML clearly set ``pass_history: true`` and
``max_sessions: 2``.

Journey (the user path that surfaces the failure):
1. Author a YAML with an inline sub-agent tool that sets
   ``pass_history: true`` and ``max_sessions: 2``.
2. Load the YAML with ``omnigent.spec.load()``.
3. Translate it with ``agent_spec_to_agent_def()``.
4. Read ``agent.tools["reviewer"].pass_history`` and
   ``agent.tools["reviewer"].max_sessions``.
5. Observe ``False`` / ``None`` instead of the declared
   ``True`` / ``2``.

This test FAILS on the unfixed codebase (both assertions fire)
and must PASS after the fix lands — the canonical
fail-then-pass regression guard for the fix step.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from omnigent.inner.tools import AgentTool
from omnigent.spec import load
from omnigent.spec.omnigent import agent_spec_to_agent_def


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture()
def history_repro_yaml(tmp_path: Path) -> Path:
    """
    Write the exact YAML from the bug report to a temp file and return
    its path.

    Declares an inline sub-agent tool ``reviewer`` with:
    - ``pass_history: true``
    - ``max_sessions: 2``
    - ``pass_histories: [parent]`` (extra field to cover all three)

    :returns: Path to the written YAML file.
    """
    content = textwrap.dedent("""\
        name: history_repro
        prompt: Validate agent-tool history settings.

        executor:
          harness: claude-sdk
          model: databricks-claude-sonnet-4-6

        tools:
          reviewer:
            type: agent
            prompt: Review the current changes.
            executor:
              harness: claude-sdk
              model: databricks-claude-sonnet-4-6
            os_env: inherit
            pass_history: true
            max_sessions: 2
            pass_histories:
              - parent
    """)
    yaml_path = tmp_path / "history_repro.yaml"
    yaml_path.write_text(content)
    return yaml_path


# ── Reproduction test (YAML load path) ─────────────────────────────────────


def test_yaml_sub_agent_pass_history_preserved(history_repro_yaml: Path) -> None:
    """
    ``pass_history: true`` declared on an inline sub-agent tool in a
    YAML spec must survive ``load() + agent_spec_to_agent_def()`` and
    appear as ``True`` on the resulting ``AgentTool``.

    **Bug**: The ``AgentSpec`` intermediate representation
    has no ``pass_history`` field, so the YAML parser discards the
    value. ``_sub_spec_to_agent_tool`` then constructs an ``AgentTool``
    with the default ``pass_history=False``, silently ignoring the
    author's intent to share conversation history with the sub-agent.

    **What breaks if this fails after a fix**: a regression in the fix
    re-introduces the silent drop — users who set ``pass_history: true``
    on their sub-agent tools again see the parent history absent from the
    sub-session context.
    """
    spec = load(history_repro_yaml)
    agent = agent_spec_to_agent_def(spec)
    reviewer = agent.tools.get("reviewer")

    assert isinstance(reviewer, AgentTool), (
        "Expected 'reviewer' to be an AgentTool in the translated AgentDef; "
        f"got {type(reviewer).__name__!r}. "
        "The sub-agent tool translation may have broken entirely."
    )
    assert reviewer.pass_history is True, (
        f"pass_history should be True (as declared in YAML) but got "
        f"{reviewer.pass_history!r}. "
        "AgentSpec / _sub_spec_to_agent_tool is dropping 'pass_history' silently."
    )


def test_yaml_sub_agent_max_sessions_preserved(history_repro_yaml: Path) -> None:
    """
    ``max_sessions: 2`` declared on an inline sub-agent tool in a YAML
    spec must survive ``load() + agent_spec_to_agent_def()`` and appear
    as ``2`` on the resulting ``AgentTool``.

    **Bug**: Same root cause as ``pass_history`` — the
    ``AgentSpec`` type has no ``max_sessions`` field, so the parser
    discards it. ``_sub_spec_to_agent_tool`` defaults to ``None``
    (unlimited), silently removing the concurrency cap the author
    declared.

    **What breaks if this fails after a fix**: a regression re-drops
    the concurrency cap, and the runtime allows unlimited concurrent
    sub-sessions even when the author intended to throttle them.
    """
    spec = load(history_repro_yaml)
    agent = agent_spec_to_agent_def(spec)
    reviewer = agent.tools.get("reviewer")

    assert isinstance(reviewer, AgentTool), (
        "Expected 'reviewer' to be an AgentTool in the translated AgentDef; "
        f"got {type(reviewer).__name__!r}."
    )
    assert reviewer.max_sessions == 2, (
        f"max_sessions should be 2 (as declared in YAML) but got "
        f"{reviewer.max_sessions!r}. "
        "AgentSpec / _sub_spec_to_agent_tool is dropping 'max_sessions' silently."
    )


def test_yaml_sub_agent_pass_histories_preserved(history_repro_yaml: Path) -> None:
    """
    ``pass_histories: [parent]`` declared on an inline sub-agent tool
    must survive ``load() + agent_spec_to_agent_def()`` and appear as
    ``["parent"]`` on the resulting ``AgentTool``.

    **Bug**: Same root cause — ``AgentSpec`` has no
    ``pass_histories`` field, so the named histories list is silently
    dropped. Sub-sessions then start without the named histories the
    author intended to inject.

    **What breaks if this fails after a fix**: a regression drops the
    named histories list; sub-sessions no longer receive the histories
    injected by the parent.
    """
    spec = load(history_repro_yaml)
    agent = agent_spec_to_agent_def(spec)
    reviewer = agent.tools.get("reviewer")

    assert isinstance(reviewer, AgentTool), (
        "Expected 'reviewer' to be an AgentTool in the translated AgentDef; "
        f"got {type(reviewer).__name__!r}."
    )
    assert reviewer.pass_histories == ["parent"], (
        f"pass_histories should be ['parent'] (as declared in YAML) but got "
        f"{reviewer.pass_histories!r}. "
        "AgentSpec / _sub_spec_to_agent_tool is dropping 'pass_histories' silently."
    )


# ── Combined / end-to-end regression test ──────────────────────────────────


def test_yaml_sub_agent_all_history_fields_preserved(tmp_path: Path) -> None:
    """
    End-to-end regression: all three history/session-limiting fields
    (``pass_history``, ``pass_histories``, ``max_sessions``) declared on
    an inline sub-agent tool survive the full
    ``load() + agent_spec_to_agent_def()`` pipeline.

    This is the exact script from the bug report, adapted to
    run as an automated pytest assertion. It must:
    - Return ``pass_history=True`` (not ``False``)
    - Return ``max_sessions=2`` (not ``None``)
    - Return ``pass_histories=["parent"]`` (not ``None``)

    **Bug**: All three values are silently reset to their defaults
    because ``AgentSpec`` lacks the fields and
    ``_sub_spec_to_agent_tool`` therefore never sees them.

    **What breaks if this fails after a fix**: the fix has regressed
    and any of the three fields is again dropped silently, causing
    incorrect sub-agent behavior for users who configure these fields.
    """
    yaml_content = textwrap.dedent("""\
        name: history_repro
        prompt: Validate agent-tool history settings.

        executor:
          harness: claude-sdk
          model: databricks-claude-sonnet-4-6

        tools:
          reviewer:
            type: agent
            prompt: Review the current changes.
            executor:
              harness: claude-sdk
              model: databricks-claude-sonnet-4-6
            os_env: inherit
            pass_history: true
            max_sessions: 2
            pass_histories:
              - parent
    """)
    yaml_path = tmp_path / "history_repro.yaml"
    yaml_path.write_text(yaml_content)

    spec = load(yaml_path)
    agent = agent_spec_to_agent_def(spec)
    reviewer = agent.tools.get("reviewer")

    assert isinstance(reviewer, AgentTool), (
        "'reviewer' tool should be an AgentTool after translation."
    )

    # All three fields must survive the load + translate pipeline.
    failures: list[str] = []
    if reviewer.pass_history is not True:
        failures.append(
            f"pass_history: expected True, got {reviewer.pass_history!r}"
        )
    if reviewer.max_sessions != 2:
        failures.append(
            f"max_sessions: expected 2, got {reviewer.max_sessions!r}"
        )
    if reviewer.pass_histories != ["parent"]:
        failures.append(
            f"pass_histories: expected ['parent'], got {reviewer.pass_histories!r}"
        )

    assert not failures, (
        "Sub-agent history/session fields silently dropped during "
        "load() + agent_spec_to_agent_def():\n"
        + "\n".join(f"  • {f}" for f in failures)
        + "\n\nFix: AgentSpec needs fields for these values (or the parser "
        "must carry them through a side-channel to _sub_spec_to_agent_tool)."
    )
