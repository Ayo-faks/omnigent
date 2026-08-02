"""Directed tests for the route-resolution seam (``resolve_route``, plan 3i).

The four-step chain — strip prefix → exact catalog match → one fixed family
fallback → honest decline — has no live trigger on the reference workspace
(all five frozen arms resolve exactly there), so these synthetic-catalog unit
tests are the proof for the fallback / decline / spelling-pin / separator-safe
paths (plan 6c). Fallback and spelling targets are asserted against the
contract constants so the tests cannot drift from the owned fallback records.
"""

from __future__ import annotations

from omnigent.model_fallbacks import FAMILY_FALLBACK_ID, SERVABLE_ALIASES
from omnigent.server.smart_routing import ResolvedRoute, resolve_route

# A synthetic servable catalog: what a workspace's runner reports it can serve.
# Deliberately excludes opus and every codex arm except luna, so the fallback
# and decline branches fire without a live workspace.
_CLAUDE_SONNET = "databricks-claude-sonnet-5"
_CODEX_LUNA = "databricks-gpt-5-6-luna"
_GLM_ARM = "glm-5-2"
_GLM_SERVED = "system.ai.glm-5-2"


# ── Step 2: exact catalog match ──────────────────────────────────────────────


def test_exact_match_returns_pick_verbatim() -> None:
    """A served arm resolves to itself; raw_model equals the pick (no drift)."""
    route = resolve_route("databricks-claude-sonnet-5", servable=[_CLAUDE_SONNET, _CODEX_LUNA])
    assert route == ResolvedRoute(
        model=_CLAUDE_SONNET,
        harness="claude-native",
        raw_model="databricks-claude-sonnet-5",
    )


def test_exact_match_recovers_prefixed_catalog_id_from_bare_pick() -> None:
    """The router's bare pick maps back to the workspace's prefixed catalog id."""
    route = resolve_route("claude-sonnet-5", servable=[_CLAUDE_SONNET, _CODEX_LUNA])
    assert route is not None
    assert route.model == _CLAUDE_SONNET  # exact catalog spelling applied
    assert route.raw_model == "claude-sonnet-5"  # the router's pick, verbatim
    assert route.harness == "claude-native"


def test_gpt_arm_exact_match_lands_on_codex_native() -> None:
    """A served gpt arm keeps its codex-native harness."""
    route = resolve_route(_CODEX_LUNA, servable=[_CLAUDE_SONNET, _CODEX_LUNA])
    assert route is not None
    assert route.model == _CODEX_LUNA
    assert route.harness == "codex-native"
    assert route.raw_model == _CODEX_LUNA


# ── glm gateway spelling pin (a spelling, not a substitution) ─────────────────


def test_glm_spelling_pin_applied_on_the_way_out() -> None:
    """A served glm arm is applied under its system.ai route; raw stays the arm."""
    route = resolve_route(_GLM_ARM, servable=[_GLM_ARM, _CODEX_LUNA])
    assert route is not None
    # The pin from SERVABLE_ALIASES is applied on the way out...
    assert route.model == SERVABLE_ALIASES[_GLM_ARM] == _GLM_SERVED
    # ...but raw_model records the ARM, not the served spelling — it is a
    # spelling, not a different pick (their bare ids are identical).
    assert route.raw_model == _GLM_ARM
    assert route.harness == "codex-native"


# ── Step 3: family fallback ───────────────────────────────────────────────────


def test_claude_family_fallback_unservable_opus_to_sonnet() -> None:
    """An unservable opus pick falls back to the sonnet id; raw stays opus."""
    route = resolve_route("claude-opus-4-8", servable=[_CLAUDE_SONNET, _CODEX_LUNA])
    assert route is not None
    assert route.model == FAMILY_FALLBACK_ID["claude"] == _CLAUDE_SONNET
    assert route.raw_model == "claude-opus-4-8"  # chip shows what the router said
    assert route.harness == "claude-native"


def test_gpt_family_fallback_unservable_sol_to_luna() -> None:
    """An unservable gpt arm (sol) falls back to luna; raw stays sol."""
    route = resolve_route("gpt-5-6-sol", servable=[_CLAUDE_SONNET, _CODEX_LUNA])
    assert route is not None
    assert route.model == FAMILY_FALLBACK_ID["gpt"] == _CODEX_LUNA
    assert route.raw_model == "gpt-5-6-sol"
    assert route.harness == "codex-native"


def test_glm_family_fallback_to_luna_stays_on_codex() -> None:
    """An unservable glm pick falls back to luna and never leaves codex-native."""
    route = resolve_route(_GLM_ARM, servable=[_CODEX_LUNA])
    assert route is not None
    assert route.model == FAMILY_FALLBACK_ID["glm"] == _CODEX_LUNA
    assert route.raw_model == _GLM_ARM
    assert route.harness == "codex-native"


# ── Step 4: honest decline ────────────────────────────────────────────────────


def test_decline_when_nothing_servable() -> None:
    """No servable arm and no servable fallback → None (caller keeps default)."""
    assert resolve_route("claude-opus-4-8", servable=[]) is None


def test_decline_when_family_fallback_not_served() -> None:
    """Claude pick with only codex served: sonnet fallback missing → decline."""
    assert resolve_route("claude-opus-4-8", servable=[_CODEX_LUNA]) is None


def test_decline_on_empty_pick() -> None:
    """A blank router pick declines rather than guessing."""
    assert resolve_route("   ", servable=[_CLAUDE_SONNET]) is None


def test_decline_on_unknown_family() -> None:
    """A pick whose family has no fallback entry declines when not served exactly."""
    assert resolve_route("mystery-model-9", servable=[_CLAUDE_SONNET, _CODEX_LUNA]) is None


# ── Separator-safe prefix stripping (oracle 0d trap) ──────────────────────────


def test_prefix_without_trailing_separator_is_separator_safe() -> None:
    """A prefix configured without its trailing separator must not corrupt ids.

    With ``prefixes=("system.ai",)`` (no trailing dot), stripping leaves a
    leading ``.`` that the separator-safe rule drops. The router's bare pick
    then exact-matches the workspace's ``system.ai.``-prefixed catalog id. Were
    the leading separator NOT dropped, the bare ids would differ and this would
    fall through to a fallback/decline instead of an exact match.
    """
    route = resolve_route(
        "claude-sonnet-5",
        servable=["system.ai.claude-sonnet-5"],
        prefixes=("system.ai",),
    )
    assert route is not None
    assert route.model == "system.ai.claude-sonnet-5"  # exact catalog id applied
    assert route.raw_model == "claude-sonnet-5"
    assert route.harness == "claude-native"
