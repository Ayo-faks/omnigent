"""Per-family gateway-inference signal (wave-1 stream 4).

Reports, per harness family, whether THIS host resolves that family's inference
to the workspace AI Gateway. It is a config-only check: it reads resolved
provider config and never launches a process or makes a network call. The signal
rides the host frames, the host store, and the hosts route; the web consumes it
to gate the Smart Routing option (plan 3f):

- The Model row offers Smart Routing for a harness only when that harness's
  family reports gateway-backed.
- The Smart Routing harness row appears only when BOTH the claude and codex
  families report gateway-backed, because the harness routes across both.
- A host that reports nothing is unknown, and unknown never hides the option.

Smart Routing's apply layer can only rewrite a launch's model when the launch
resolves through the Databricks AI Gateway — that is where the routable model
catalog lives. These checks answer that question per harness family from config
resolution alone, so the host can report the answer alongside harness readiness
on every registration.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Final
from urllib.parse import urlparse

from omnigent.server.routing_contract import (
    CLAUDE_GATEWAY_HARNESSES,
    CODEX_GATEWAY_HARNESSES,
)

if TYPE_CHECKING:
    from omnigent.codex_native_app_server import NativeCodexLaunch

_logger = logging.getLogger(__name__)

# The AI Gateway serves Codex/OpenAI-Responses under this path suffix; both
# gateway URL shapes (dedicated subdomain and workspace-hosted) end with it.
_CODEX_GATEWAY_PATH_SUFFIX = "/codex/v1"

# Trusted parent domain suffixes for a Databricks-owned host, and the DNS label
# a dedicated gateway subdomain carries. Transcribed verbatim from the canonical
# predicate (``omnigent.pi_native_credentials._is_databricks_ai_gateway_url``,
# ``databricks_ai_gateway.is_databricks_ai_gateway_url`` in the reference tree)
# — a security constant (plan 0d), inlined here so this owned, config-only
# module needs no import from a harness file. Anchored on the leading "." so a
# look-alike like ``...cloud.databricks.com.evil.test`` is rejected.
_DATABRICKS_TRUSTED_HOST_SUFFIXES: Final[tuple[str, ...]] = (
    ".cloud.databricks.com",  # AWS workspaces + ai-gateway
    ".azuredatabricks.net",  # Azure Databricks
    ".gcp.databricks.com",  # GCP Databricks
)
_DATABRICKS_AI_GATEWAY_LABEL: Final[str] = "ai-gateway"


def _is_databricks_ai_gateway_url(base_url: str) -> bool:
    """Return ``True`` only for a genuine Databricks AI Gateway base URL.

    Two URL shapes are accepted:

    1. **Dedicated AI Gateway subdomain** — ``ai-gateway`` is a full DNS label
       in the hostname (e.g. ``<id>.ai-gateway.cloud.databricks.com``).
    2. **Workspace-hosted gateway** — the hostname is a plain Databricks
       workspace (ends with a trusted suffix) and the path starts with
       ``/ai-gateway/`` (e.g. ``<workspace>.cloud.databricks.com/ai-gateway/...``).

    Both cases require ``https`` and a hostname ending with a trusted
    Databricks-owned domain suffix to prevent token-forwarding attacks.

    :param base_url: An inference base URL, e.g. a codex provider ``base_url``.
    :returns: ``True`` iff the URL is an https Databricks AI Gateway endpoint.
    """
    parsed = urlparse(base_url)
    if parsed.scheme != "https":
        return False
    hostname = parsed.hostname
    if not hostname:
        return False
    hostname = hostname.lower()
    trusted = any(hostname.endswith(suffix) for suffix in _DATABRICKS_TRUSTED_HOST_SUFFIXES)
    if not trusted:
        return False
    # Shape 1: ``ai-gateway`` is a full DNS label in the hostname.
    if _DATABRICKS_AI_GATEWAY_LABEL in hostname.split("."):
        return True
    # Shape 2: workspace hostname + /ai-gateway/ path prefix.
    return (parsed.path or "").startswith("/ai-gateway/")


def _codex_launch_base_url(launch: NativeCodexLaunch) -> str | None:
    """Inference base URL a resolved codex launch pins, or ``None`` when it defers.

    Reimplements the reference tree's ``native_codex_launch_base_url`` from the
    v2 launch shape (which does not ship that helper): the Databricks-profile
    branch derives the base URL from the profile host exactly as
    ``build_native_codex_app`` applies it, while a generic provider carries the
    URL inside a ``model_providers.…base_url=…`` config override. A cli-config
    entry pins only a provider *name* (its table lives in the user's
    ``~/.codex/config.toml``, which this process does not read) → ``None``.

    :param launch: A resolved :class:`NativeCodexLaunch`.
    :returns: The base URL the launch routes through, or ``None``.
    """
    if launch.profile is not None:
        from omnigent.inner.codex_executor import _databricks_codex_base_url
        from omnigent.inner.databricks_executor import _databricks_gateway_host

        host = _databricks_gateway_host(launch.profile)
        if not host:
            return None
        return _databricks_codex_base_url(host.rstrip("/"))
    for override in launch.config_overrides:
        _, sep, table = override.partition("=")
        if not sep or not override.startswith("model_providers."):
            continue
        marker = "base_url="
        index = table.find(marker)
        if index < 0:
            continue
        try:
            base_url, _ = json.JSONDecoder().raw_decode(table[index + len(marker) :])
        except ValueError:
            continue
        if isinstance(base_url, str):
            return base_url
    return None


def claude_gateway_inference_backed() -> bool:
    """Whether a claude-native launch on this host resolves gateway-backed inference.

    Config-only: inspect the resolved claude provider config. A gateway-backed
    launch pins ``ANTHROPIC_BASE_URL`` and delivers its bearer token through
    Claude Code's ``apiKeyHelper``. The Bedrock path sets
    ``ANTHROPIC_BEDROCK_BASE_URL`` with no helper, and a subscription / CLI
    login resolves no config at all — neither is routable.

    :returns: ``True`` iff the resolved config is AI-Gateway-backed.
    """
    from omnigent.claude_native import resolve_native_claude_config

    config = resolve_native_claude_config(spec=None)
    if config is None:
        return False
    return bool(config.env.get("ANTHROPIC_BASE_URL")) and bool(config.api_key_helper)


def codex_gateway_inference_backed() -> bool:
    """Whether a codex-native launch on this host resolves gateway-backed inference.

    Config-only: resolve the codex launch this host would use and inspect the
    base URL it pins. Gateway-backed iff that URL is a Databricks AI Gateway
    endpoint whose path is the Codex Responses surface (``/codex/v1``).

    :returns: ``True`` iff the resolved launch routes through an AI Gateway
        Codex base URL.
    """
    from omnigent.codex_native_app_server import resolve_native_codex_launch

    base_url = _codex_launch_base_url(resolve_native_codex_launch(model=None))
    if not base_url:
        return False
    if not _is_databricks_ai_gateway_url(base_url):
        return False
    return base_url.rstrip("/").endswith(_CODEX_GATEWAY_PATH_SUFFIX)


def gateway_inference_map() -> dict[str, bool]:
    """Per-harness map of whether this host's inference for that family is gateway-backed.

    Each family is evaluated once and the result fanned out over every spelling
    that family travels under. A family whose check raises is omitted rather
    than reported as ``False``, so the server can tell "not gateway-backed"
    apart from "could not tell".

    :returns: Harness spelling → gateway-backed flag, omitting unevaluable
        families.
    """
    result: dict[str, bool] = {}
    for _family, spellings, check in (
        ("claude", CLAUDE_GATEWAY_HARNESSES, claude_gateway_inference_backed),
        ("codex", CODEX_GATEWAY_HARNESSES, codex_gateway_inference_backed),
    ):
        try:
            backed = check()
        except NotImplementedError:
            raise
        except Exception:  # noqa: BLE001  # a family check failure is unknown, never False
            _logger.warning("gateway_inference: %s family check failed", _family, exc_info=True)
            continue
        for spelling in spellings:
            result[spelling] = backed
    return result
