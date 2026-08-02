"""Owned static model fallbacks for CLI surfaces without discovery."""

from __future__ import annotations

from dataclasses import dataclass

from omnigent.onboarding.provider_config import CLI_CONFIG_KIND, SUBSCRIPTION_KIND


@dataclass(frozen=True)
class StaticModelFallback:
    """A release-curated model list with auditable ownership and provenance."""

    model_ids: tuple[str, ...]
    owner: str
    provenance: str
    discovery_gap: str


_CLAUDE_SUBSCRIPTION_MODELS = (
    "claude-fable-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
)

_CODEX_MODELS = ("gpt-5-6-sol", "gpt-5-6-luna", "gpt-5-6-terra", "gpt-5-5")

# ── Smart Routing task_v1 arms + family fallback (routing rebuild) ───────────
#
# The frozen task_v1 arm menus and the one-per-family fallback. These are static
# because task_v1's arm set is a wire contract (the router 400s on a partial
# menu) and is frozen upstream, so they cannot be discovered from a workspace
# catalog. Owned here with provenance per the no-hardcoded-ids guard; the
# routing seam (omnigent/server/smart_routing.py) and routing_contract import
# them rather than inlining the literals.

_TASK_V1_CLAUDE_ARMS: tuple[str, ...] = ("claude-opus-4-8", "claude-sonnet-5")
_TASK_V1_CODEX_ARMS: tuple[str, ...] = ("glm-5-2", "gpt-5-6-sol", "gpt-5-6-luna")

TASK_V1_ARMS: dict[str, StaticModelFallback] = {
    "claude": StaticModelFallback(
        model_ids=_TASK_V1_CLAUDE_ARMS,
        owner="Smart Routing task_v1 claude arms",
        provenance="Omnigent's frozen task_v1 router arm menu (claude family)",
        discovery_gap=(
            "task_v1's arm set is a frozen wire contract; sending a partial "
            "menu 400s, so the arms cannot be derived from a workspace catalog"
        ),
    ),
    "codex": StaticModelFallback(
        model_ids=_TASK_V1_CODEX_ARMS,
        owner="Smart Routing task_v1 codex arms",
        provenance="Omnigent's frozen task_v1 router arm menu (codex/gpt family)",
        discovery_gap=(
            "task_v1's arm set is a frozen wire contract; sending a partial "
            "menu 400s, so the arms cannot be derived from a workspace catalog"
        ),
    ),
}

# One fixed fallback per family when the workspace does not serve the router's
# pick (plan 3i rule 3). claude -> the id the ``sonnet`` alias pin resolves to;
# gpt AND glm -> luna, itself a frozen codex arm so a glm fallback never leaves
# the codex harness. The fallback stamps raw_model so the record stays honest.
# The ids live in module-level tuples consumed only as StaticModelFallback
# model_ids (the guard-owned form); the lookups below are built from those
# records, never from fresh literals.
_CLAUDE_FALLBACK_IDS = ("databricks-claude-sonnet-5",)
_CODEX_FALLBACK_IDS = ("databricks-gpt-5-6-luna",)
# The glm gateway spelling pin as an (arm, served-spelling) pair. glm-5-2 serves
# the Responses API only under system.ai.glm-5-2 (probed 2026-08-01,
# staging+prod). A spelling, not a substitution (plan 3i).
_GLM_SPELLING_IDS = ("glm-5-2", "system.ai.glm-5-2")

_FAMILY_FALLBACK_RECORDS: dict[str, StaticModelFallback] = {
    "claude": StaticModelFallback(
        model_ids=_CLAUDE_FALLBACK_IDS,
        owner="Smart Routing claude family fallback",
        provenance="Bryan's per-family fallback ruling 2026-08-02 (claude -> sonnet)",
        discovery_gap=(
            "the fallback target must be deterministic when discovery reports "
            "the picked arm unservable; it is a fixed product decision"
        ),
    ),
    "codex": StaticModelFallback(
        model_ids=_CODEX_FALLBACK_IDS,
        owner="Smart Routing codex family fallback",
        provenance="Bryan's per-family fallback ruling 2026-08-02 (gpt/glm -> luna)",
        discovery_gap=(
            "the fallback target must be deterministic when discovery reports "
            "the picked arm unservable; it is a fixed product decision"
        ),
    ),
}

_GLM_SPELLING_RECORD = StaticModelFallback(
    model_ids=_GLM_SPELLING_IDS,
    owner="Smart Routing glm gateway spelling pin",
    provenance=(
        "probed 2026-08-01 staging+prod: glm serves the Responses API only "
        "under its system.ai route"
    ),
    discovery_gap="the served spelling differs from the catalog id and is not discoverable",
)

#: Family -> its fallback catalog id, built from the owned records above.
FAMILY_FALLBACK_ID: dict[str, str] = {
    "claude": _FAMILY_FALLBACK_RECORDS["claude"].model_ids[0],
    "gpt": _FAMILY_FALLBACK_RECORDS["codex"].model_ids[0],
    "glm": _FAMILY_FALLBACK_RECORDS["codex"].model_ids[0],
}

#: Router arm id -> the gateway spelling it is actually served under.
SERVABLE_ALIASES: dict[str, str] = {
    _GLM_SPELLING_RECORD.model_ids[0]: _GLM_SPELLING_RECORD.model_ids[1],
}

_STATIC_MODEL_FALLBACKS = {
    (SUBSCRIPTION_KIND, "claude"): StaticModelFallback(
        model_ids=_CLAUDE_SUBSCRIPTION_MODELS,
        owner="Claude subscription adapter",
        provenance="Omnigent's release-curated Claude Code alias catalog",
        discovery_gap="Claude subscription logins expose no model-listing API",
    ),
    (SUBSCRIPTION_KIND, "codex"): StaticModelFallback(
        model_ids=_CODEX_MODELS,
        owner="Codex subscription adapter",
        provenance="Omnigent's release-curated Codex alias catalog",
        discovery_gap="Codex subscription availability is not exposed before launch",
    ),
    (CLI_CONFIG_KIND, "codex"): StaticModelFallback(
        model_ids=_CODEX_MODELS,
        owner="Codex CLI-config adapter",
        provenance="Omnigent's release-curated Codex alias catalog",
        discovery_gap=(
            "Custom model_provider entries live in Codex config.toml and cannot "
            "be enumerated on this catalog path"
        ),
    ),
}


def static_model_fallback(provider_kind: str, cli: str) -> StaticModelFallback | None:
    """Return the owned fallback for a provider kind and CLI, if registered."""
    return _STATIC_MODEL_FALLBACKS.get((provider_kind, cli))
