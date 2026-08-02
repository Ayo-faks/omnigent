"""Directed tests for the native-Codex model apply layer (Smart Routing).

Covers the two pieces the rebuild adds on top of main's existing
``thread/settings/update`` push:

1. ``write_codex_config_model`` — mirrors an Omnigent-initiated model switch
   into the session's ``config.toml`` so the forwarder's model mirror
   (``_refresh_model_from_config`` → ``_sync_model_change``) and the cost-gate
   hook stay consistent. Without it, the next ``turn/started`` re-reads the
   stale launch model and silently reverts the switch.
2. ``_served_codex_model`` — applies the glm gateway spelling
   (``glm-5-2`` → ``system.ai.glm-5-2``) at apply time. The routing seam still
   records the bare arm; only the served id sent to codex changes.

Fast and offline: no live codex, no app-server, no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnigent.codex_native_bridge import (
    codex_home_for_bridge_dir,
    read_codex_config_model,
    write_codex_config_model,
)
from omnigent.inner.codex_native_executor import _model_effort_overrides, _served_codex_model
from omnigent.inner.executor import ExecutorConfig
from omnigent.server.routing_contract import SERVABLE_ALIASES

# The glm arm and the spelling the gateway serves it under, taken from the
# frozen contract pin so this test tracks the real wiring rather than a copy.
_GLM_ARM, _GLM_SERVED = next(iter(SERVABLE_ALIASES.items()))


@pytest.fixture
def bridge_dir(tmp_path: Path) -> Path:
    """A bridge dir whose per-session ``CODEX_HOME`` exists but has no config yet."""
    home = codex_home_for_bridge_dir(tmp_path)
    home.mkdir(parents=True, exist_ok=True)
    return tmp_path


def _config_path(bridge_dir: Path) -> Path:
    """Return the session's ``config.toml`` path."""
    return codex_home_for_bridge_dir(bridge_dir) / "config.toml"


# ── 1. write_codex_config_model round-trips a model into config.toml ─────────


def test_write_codex_config_model_creates_file_when_absent(bridge_dir: Path) -> None:
    """A first switch writes a fresh ``config.toml`` with the top-level model."""
    assert write_codex_config_model(bridge_dir, "gpt-5.6-luna") is True

    assert read_codex_config_model(bridge_dir) == "gpt-5.6-luna"


def test_write_codex_config_model_upserts_existing_model_key(bridge_dir: Path) -> None:
    """A switch overwrites the existing top-level ``model`` line in place."""
    _config_path(bridge_dir).write_text('model_provider = "databricks"\nmodel = "gpt-5.5"\n')

    assert write_codex_config_model(bridge_dir, "gpt-5.6-luna") is True

    # The model was replaced, and the sibling top-level key survived.
    assert read_codex_config_model(bridge_dir) == "gpt-5.6-luna"
    body = _config_path(bridge_dir).read_text()
    assert 'model_provider = "databricks"' in body
    assert body.count("\nmodel = ") + int(body.startswith("model = ")) == 1


def test_write_codex_config_model_inserts_when_no_model_key(bridge_dir: Path) -> None:
    """A config with other keys but no ``model`` gains one without loss."""
    _config_path(bridge_dir).write_text('model_reasoning_effort = "medium"\n')

    assert write_codex_config_model(bridge_dir, "gpt-5.6-luna") is True

    assert read_codex_config_model(bridge_dir) == "gpt-5.6-luna"
    assert 'model_reasoning_effort = "medium"' in _config_path(bridge_dir).read_text()


def test_write_codex_config_model_only_touches_top_level_table(bridge_dir: Path) -> None:
    """A ``model`` inside a ``[section]`` is not mistaken for the top-level key.

    The upsert stops at the first table header, so a nested ``model`` key is
    left alone and a new top-level one is prepended.
    """
    _config_path(bridge_dir).write_text('[some_provider]\nmodel = "nested-should-not-change"\n')

    assert write_codex_config_model(bridge_dir, "gpt-5.6-luna") is True

    assert read_codex_config_model(bridge_dir) == "gpt-5.6-luna"
    assert 'model = "nested-should-not-change"' in _config_path(bridge_dir).read_text()


def test_write_codex_config_model_best_effort_on_unwritable(tmp_path: Path) -> None:
    """An unwritable path returns ``False`` rather than raising.

    The live thread already runs the new model, so a mirror-write failure must
    not sink the turn.
    """
    # codex-home is a *file*, so codex_home/config.toml can't be created.
    clash = tmp_path
    (codex_home_for_bridge_dir(clash)).write_text("not a directory")

    assert write_codex_config_model(clash, "gpt-5.6-luna") is False


# ── 2. the glm served spelling is applied for a glm pick ─────────────────────


def test_served_codex_model_applies_glm_gateway_spelling() -> None:
    """The bare glm arm resolves to the gateway's served route spelling."""
    assert _served_codex_model(_GLM_ARM) == _GLM_SERVED


def test_served_codex_model_strips_catalog_prefix_before_alias() -> None:
    """A prefixed catalog glm id still maps to the served spelling."""
    assert _served_codex_model(f"databricks-{_GLM_ARM}") == _GLM_SERVED


def test_served_codex_model_is_idempotent() -> None:
    """Applying the alias to the already-served id is a no-op (safe to re-run)."""
    assert _served_codex_model(_GLM_SERVED) == _GLM_SERVED


def test_served_codex_model_passes_non_aliased_models_through() -> None:
    """A non-glm model is returned unchanged — no spurious substitution."""
    assert _served_codex_model("gpt-5.6-luna") == "gpt-5.6-luna"


def test_model_effort_overrides_applies_glm_spelling() -> None:
    """A glm pick reaches ``thread/settings/update`` under the served spelling.

    This is the wire-level proof: the model codex is told to run is the served
    route, so the codex turn does not 400 on the chat-completions-only endpoint.
    """
    config = ExecutorConfig(model=_GLM_ARM)

    overrides = _model_effort_overrides(config)

    assert overrides["model"] == _GLM_SERVED


def test_model_effort_overrides_leaves_non_glm_model_untouched() -> None:
    """A non-glm pick is sent verbatim (no served-alias rewrite)."""
    config = ExecutorConfig(model="gpt-5.6-luna")

    overrides = _model_effort_overrides(config)

    assert overrides["model"] == "gpt-5.6-luna"


def test_model_effort_overrides_empty_when_no_model() -> None:
    """No pinned model → empty overrides, so the thread keeps its launch model."""
    assert _model_effort_overrides(ExecutorConfig(model=None)) == {}
    assert _model_effort_overrides(None) == {}


# ── 3. a mirrored switch is NOT reverted by a subsequent config re-read ──────


def test_mirrored_switch_survives_config_reread(bridge_dir: Path) -> None:
    """The revert trap: a switch written to config.toml reads back identically.

    The forwarder re-reads ``config.toml`` at every ``turn/started``
    (``_refresh_model_from_config`` → ``read_codex_config_model``) and mirrors
    the result to ``conv.model_override``. Before this writer existed,
    ``thread/settings/update`` changed only the live thread, so that re-read
    saw the STALE launch model and mirrored it back — silently reverting the
    switch. Writing the accepted model into config.toml makes the re-read
    return the switched model, so no revert event is produced.
    """
    # Launch pin: the session started on luna.
    _config_path(bridge_dir).write_text('model = "gpt-5.6-luna"\n')
    assert read_codex_config_model(bridge_dir) == "gpt-5.6-luna"

    # Omnigent switches the running thread to the served glm route and mirrors
    # it (exactly what the executor does after a successful settings update).
    switched = _served_codex_model(_GLM_ARM)
    assert write_codex_config_model(bridge_dir, switched) is True

    # The forwarder's next turn/started re-read now returns the switched model,
    # so _sync_model_change would post the switched value, never the stale one.
    assert read_codex_config_model(bridge_dir) == switched
    assert read_codex_config_model(bridge_dir) != "gpt-5.6-luna"


def test_glm_switch_round_trips_as_served_spelling(bridge_dir: Path) -> None:
    """A glm switch persists under the served spelling end to end.

    The executor mirrors ``_model_effort_overrides``' model (already the served
    spelling) into config.toml, so a re-read returns ``system.ai.glm-5-2`` —
    the id codex actually serves — not the bare arm.
    """
    overrides = _model_effort_overrides(ExecutorConfig(model=_GLM_ARM))
    mirrored = overrides["model"]
    assert isinstance(mirrored, str)

    assert write_codex_config_model(bridge_dir, mirrored) is True

    assert read_codex_config_model(bridge_dir) == _GLM_SERVED
