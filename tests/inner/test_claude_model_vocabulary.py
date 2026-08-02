"""Directed tests for Claude Code's model vocabulary translation.

Covers the four routing traps the vocabulary layer exists to close: a
catalog id maps to a family alias, an unknown id fails open (``None``), a
drifted / unpinned family is untranslatable, and a custom-slot id is spoken
byte-exactly. See ``omnigent/claude_model_vocabulary.py``.
"""

from __future__ import annotations

import pytest

from omnigent.claude_model_vocabulary import (
    claude_model_alias,
    claude_model_command_arg,
    model_vocabulary_env,
    normalized_model_id,
)

# A ucode-style launch pinning: each family alias mapped to a gateway id,
# plus the extra picker slot holding the newer sonnet generation.
_PINNED_ENV = {
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "databricks-claude-opus-4-8",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "databricks-claude-sonnet-4-6",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "databricks-claude-haiku-4-5",
    "ANTHROPIC_CUSTOM_MODEL_OPTION": "databricks-claude-sonnet-5",
}


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        # Catalog id -> the alias whose pin resolves to exactly that id.
        ("databricks-claude-opus-4-8", "opus"),
        ("databricks-claude-sonnet-4-6", "sonnet"),
        ("databricks-claude-haiku-4-5", "haiku"),
        # Pinned to the custom slot, and the Agent tool enum has no slot for
        # it: "sonnet" resolves to the pinned 4-6, so stepping down would run
        # a model nobody asked for. The alias surface declines.
        ("databricks-claude-sonnet-5", None),
        # Prefix + context-suffix are normalized away before comparison.
        ("claude-opus-4-8[1m]", "opus"),
        # An alias already: passed through.
        ("sonnet", "sonnet"),
        # Not a Claude id at all.
        ("databricks-gpt-5-5", None),
    ],
)
def test_alias_translation_under_a_pinned_launch(model: str, expected: str | None) -> None:
    assert claude_model_alias(model, _PINNED_ENV) == expected


def test_alias_from_the_id_when_nothing_is_pinned() -> None:
    """A direct Anthropic login accepts the family alias as-is."""
    assert claude_model_alias("databricks-claude-sonnet-5", {}) == "sonnet"
    assert claude_model_alias("mystery-model", {}) is None


def test_unpinned_family_is_untranslatable() -> None:
    """An unpinned alias resolves to a vendor id the gateway rejects."""
    env = {"ANTHROPIC_DEFAULT_OPUS_MODEL": "databricks-claude-opus-4-8"}
    assert claude_model_alias("databricks-claude-sonnet-5", env) is None
    assert claude_model_alias("databricks-claude-opus-4-8", env) == "opus"


def test_alias_requires_the_pin_to_match_the_routed_id() -> None:
    """A drifted family pin must not be spoken as its alias."""
    env = {"ANTHROPIC_DEFAULT_OPUS_MODEL": "databricks-claude-opus-5"}
    assert claude_model_alias("databricks-claude-opus-4-8", env) is None
    assert claude_model_alias("databricks-claude-opus-5", env) == "opus"


# A launch whose family pins drifted a generation ahead of the routed id, with
# and without the extra picker slot holding that exact id.
_DRIFTED_ENV = {
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "databricks-claude-opus-5",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "databricks-claude-sonnet-5",
}
_DRIFTED_WITH_SLOT = {
    **_DRIFTED_ENV,
    "ANTHROPIC_CUSTOM_MODEL_OPTION": "databricks-claude-opus-4-8",
}


@pytest.mark.parametrize(
    ("model", "env", "expected"),
    [
        # Drifted pins and no custom slot: nothing spells the routed id, so
        # the caller must skip the switch (fail open).
        ("databricks-claude-opus-4-8", _DRIFTED_ENV, None),
        # The slot holds it exactly, so that is the ``/model`` arg.
        ("databricks-claude-opus-4-8", _DRIFTED_WITH_SLOT, "databricks-claude-opus-4-8"),
        # ``/model`` compares the custom slot byte-exactly, so the arg is the
        # env's own spelling even when the routed id differs in prefix or case.
        ("system.ai.claude-opus-4-8", _DRIFTED_WITH_SLOT, "databricks-claude-opus-4-8"),
        # A pinned launch prefers the custom slot's exact id over stepping down
        # to the family alias.
        ("databricks-claude-sonnet-5", _PINNED_ENV, "databricks-claude-sonnet-5"),
        # A family pin that already matches resolves to the bare alias.
        ("databricks-claude-sonnet-4-6", _PINNED_ENV, "sonnet"),
        # Not a Claude id at all.
        ("databricks-gpt-5-5", _PINNED_ENV, None),
        # Empty / whitespace-only input is never a spellable model.
        ("", _PINNED_ENV, None),
        ("   ", _PINNED_ENV, None),
    ],
)
def test_command_arg_spells_the_routed_model_or_nothing(
    model: str, env: dict[str, str], expected: str | None
) -> None:
    assert claude_model_command_arg(model, env) == expected


def test_model_vocabulary_env_rebuilds_the_pinning_from_picker_rows() -> None:
    """Picker rows are the launch env's pinning read back out."""
    env = model_vocabulary_env(
        [
            {"id": "opus", "model": "databricks-claude-opus-5"},
            {"id": "sonnet", "model": "databricks-claude-sonnet-5"},
            {"id": "sonnet_5", "model": "databricks-claude-opus-4-8"},
            {"id": "haiku"},
            "not-a-row",
        ]
    )
    assert env == {
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "databricks-claude-opus-5",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "databricks-claude-sonnet-5",
        "ANTHROPIC_CUSTOM_MODEL_OPTION": "databricks-claude-opus-4-8",
    }
    # The rebuilt env speaks the same vocabulary the executor would.
    assert claude_model_command_arg("databricks-claude-opus-4-8", env) == (
        "databricks-claude-opus-4-8"
    )
    assert model_vocabulary_env([]) == {}


def test_model_vocabulary_env_ignores_rows_that_pin_nothing() -> None:
    """A direct Claude login's curated rows restate their own key."""
    assert (
        model_vocabulary_env(
            [
                {"id": "opus", "model": "opus", "displayName": "Opus"},
                {"id": "sonnet_5", "model": "sonnet_5", "displayName": "Sonnet 5"},
            ]
        )
        == {}
    )


def test_normalized_model_id_strips_prefix_and_context_suffix() -> None:
    assert normalized_model_id("databricks-claude-sonnet-5") == "claude-sonnet-5"
    assert normalized_model_id("system.ai.claude-sonnet-5") == "claude-sonnet-5"
    assert normalized_model_id("Claude-Opus-4-8[1M]") == "claude-opus-4-8"


def test_catalog_prefixes_match_the_routing_contract() -> None:
    """This module duplicates the prefix list to stay stdlib-only; keep it equal.

    Anchored on the frozen wave-0 contract (``routing_contract.MODEL_ID_PREFIXES``)
    rather than ``smart_routing`` so this test does not depend on another
    concurrent stream having landed its re-export.
    """
    from omnigent.claude_model_vocabulary import _CATALOG_PREFIXES
    from omnigent.server.routing_contract import MODEL_ID_PREFIXES

    assert _CATALOG_PREFIXES == MODEL_ID_PREFIXES
