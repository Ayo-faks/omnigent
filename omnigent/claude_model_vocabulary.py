"""Claude Code's model vocabulary, and how to speak it.

Omnigent routes to servable catalog ids (``databricks-claude-sonnet-5``),
but two Claude Code surfaces reject a bare catalog id:

* the ``Agent`` / ``Task`` tool's ``model`` parameter is a closed enum
  (``sonnet``, ``opus``, ``haiku``, ``fable``); a catalog id fails schema
  validation and the spawn dies before it starts;
* the ``/model`` slash command resolves an alias — or the custom slot's
  exact id — offline with no validation, but ANY other value (catalog id
  or canonical vendor id alike) is accepted only if a live one-token probe
  to the configured endpoint succeeds, so it depends on the gateway
  answering mid-turn and otherwise fails as a network error. Typing the
  raw catalog id therefore silently leaves the pane on its old model.

Claude Code resolves each alias to a concrete id via the workspace's
``ANTHROPIC_DEFAULT_*_MODEL`` env (omnigent's launch config sets these), so
inverting that mapping is exact — and only exact. A family segment alone
is not enough: a workspace serving two generations of a family pins the
alias to the newer one, so speaking the alias for the older routed id
would run a model nobody routed to. Both surfaces fail OPEN on an id with
no accepted spelling — skip the switch rather than send a value the CLI
drops.

``--model`` at launch is a different contract: it takes any string
verbatim, so a session STARTS on an exact id without needing a pin.

Stdlib-only so hook subprocesses can import it on the spawn path.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping

#: Family aliases both surfaces accept, longest-lived family first.
CLAUDE_MODEL_ALIASES: tuple[str, ...] = ("fable", "opus", "sonnet", "haiku")

#: Alias -> the env var Claude Code reads to pin that alias to one model id.
ALIAS_MODEL_ENV_VARS: dict[str, str] = {
    "fable": "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
}

#: The extra picker slot, pinned to one exact id. ``/model`` accepts that id
#: offline, compared BYTE-EXACTLY (case included) against this value, so
#: translation returns the env's own spelling rather than the caller's. The
#: Agent tool's enum has no such slot, so only ``/model`` consults it.
CUSTOM_MODEL_OPTION_ENV_VAR = "ANTHROPIC_CUSTOM_MODEL_OPTION"

#: The launch-env keys that define a session's model vocabulary. Persisted into
#: the bridge dir at launch so a runner-side caller that never saw the terminal
#: env can still translate a routed id.
MODEL_VOCABULARY_ENV_VARS: tuple[str, ...] = (
    *ALIAS_MODEL_ENV_VARS.values(),
    CUSTOM_MODEL_OPTION_ENV_VAR,
)

#: Catalog prefixes stripped before comparing ids. Must equal
#: ``routing_contract.MODEL_ID_PREFIXES`` (asserted by
#: ``test_catalog_prefixes_match_the_routing_defaults``); duplicated here
#: because this module stays stdlib-only for hook subprocesses, which also
#: means it cannot honour a deployment's ``routing.model_prefix`` override.
_CATALOG_PREFIXES: tuple[str, ...] = ("databricks-", "system.ai.")

#: Splits a normalized id into its word segments for family detection.
_SEGMENT_RE = re.compile(r"[^a-z0-9]+")


def normalized_model_id(model: str) -> str:
    """Lower-case a model id, dropping any catalog prefix and ``[1m]`` suffix.

    :param model: Any model id or alias.
    :returns: The comparable bare id, e.g. ``"claude-sonnet-5"``.
    """
    bare = model.strip().lower().removesuffix("[1m]")
    for prefix in _CATALOG_PREFIXES:
        if bare.startswith(prefix):
            return bare[len(prefix) :]
    return bare


def alias_pins(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Read the session's alias -> model-id pinning.

    :param env: Environment mapping. ``None`` reads :data:`os.environ`.
    :returns: Alias -> pinned model id, for the aliases that are pinned.
    """
    environ = os.environ if env is None else env
    pins: dict[str, str] = {}
    for alias, env_var in ALIAS_MODEL_ENV_VARS.items():
        pinned = environ.get(env_var, "").strip()
        if pinned:
            pins[alias] = pinned
    return pins


def model_vocabulary_env(options: Iterable[Mapping[str, object]]) -> dict[str, str]:
    """Rebuild a session's model vocabulary from its picker rows.

    The native picker's rows ARE the launch env's pinning read back out: a
    row keyed by a family alias is that alias's pin, and any other row
    occupies the single custom slot. This lets a process that never saw the
    terminal's env (the server) ask :func:`claude_model_command_arg` the same
    question the executor will.

    Rows that only restate their own key (a direct Claude login's curated
    ``opus`` / ``sonnet`` rows) pin nothing — Claude resolves those itself —
    so they are skipped rather than read as a pin onto an alias.

    :param options: Picker rows, e.g.
        ``[{"id": "opus", "model": "databricks-claude-opus-5"}]``.
    :returns: A vocabulary env mapping, e.g.
        ``{"ANTHROPIC_DEFAULT_OPUS_MODEL": "databricks-claude-opus-5"}``.
        Empty when the rows pin no concrete model ids.
    """
    env: dict[str, str] = {}
    for option in options:
        if not isinstance(option, Mapping):
            continue
        row_id = option.get("id")
        model = option.get("model")
        if not isinstance(model, str) or not model.strip():
            continue
        # A row whose model restates its own key (or is itself an alias) pins
        # nothing concrete; skip it rather than record a bogus pin.
        if model.strip().lower() in CLAUDE_MODEL_ALIASES or model == row_id:
            continue
        key = ALIAS_MODEL_ENV_VARS.get(row_id if isinstance(row_id, str) else "")
        if key is None:
            key = CUSTOM_MODEL_OPTION_ENV_VAR
        env.setdefault(key, model.strip())
    return env


def claude_model_alias(
    model: str,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Translate a servable model id into Claude's alias vocabulary.

    An exact hit on the pinning is authoritative. The id's own family
    segment names the alias only when NOTHING is pinned at all (a direct
    Anthropic login, where the alias resolves to the vendor's model of that
    family). Once this session pins aliases, a family segment is not enough:
    an unpinned alias resolves to a canonical vendor id the gateway rejects,
    and a MISMATCHED pin is worse — the alias resolves to the pinned id, so
    the pane would run a model nobody routed to while the record claims the
    routed one (workspace serving both ``claude-opus-4-8`` and
    ``claude-opus-5``, ``opus`` pinned to the latter, ``claude-opus-4-8``
    routed).

    :param model: Model id from a routing decision, or an alias already.
    :param env: Environment mapping holding the alias pinning. ``None`` reads
        :data:`os.environ` — a hook subprocess inherits the CLI's.
    :returns: An accepted alias, or ``None`` when the id maps to nothing
        Claude would accept; callers must then leave the model alone.
    """
    if not isinstance(model, str) or not model.strip():
        return None
    candidate = model.strip().lower()
    if candidate in CLAUDE_MODEL_ALIASES:
        return candidate
    pins = alias_pins(env)
    normalized = normalized_model_id(model)
    for alias, pinned in pins.items():
        if normalized_model_id(pinned) == normalized:
            return alias
    if pins:
        # Every pinned alias was compared exactly above, so reaching here
        # means the routed id is not what any alias resolves to. Speaking a
        # family segment now would move the pane to the pinned generation.
        return None
    # Nothing pinned: a direct Anthropic login, where the family segment IS
    # the alias and resolves to the vendor's own model of that family.
    segments = set(_SEGMENT_RE.split(normalized))
    for alias in CLAUDE_MODEL_ALIASES:
        if alias in segments:
            return alias
    return None


def claude_model_command_arg(
    model: str,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Translate a model id into a ``/model`` argument.

    Same alias vocabulary as :func:`claude_model_alias`, plus the extra
    picker slot: ``/model`` takes that exact id, so a routed model pinned
    there is applied precisely instead of stepping down to its family alias.
    The slot is compared byte-exactly, so the returned arg is the env's own
    spelling even when the routed id differs in prefix or case.

    :param model: Model id from a routing decision, or an alias already.
    :param env: Environment mapping holding the session's pinning. ``None``
        reads :data:`os.environ`.
    :returns: The ``/model`` argument, or ``None`` when the id maps to nothing
        the command accepts — the caller must skip the switch, because an
        unaccepted value silently keeps the current model.
    """
    if not isinstance(model, str) or not model.strip():
        return None
    environ = os.environ if env is None else env
    custom = environ.get(CUSTOM_MODEL_OPTION_ENV_VAR, "").strip()
    if custom and normalized_model_id(custom) == normalized_model_id(model):
        return custom
    return claude_model_alias(model, env)
