"""Per-family gateway-inference signal (wave-0 stub; wave-1 stream 4 fills).

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

Wave-1 stream 4 owns this file (plan 4f) and fills the two checks below. The
harness-spelling groups are the contract (imported from
``omnigent.server.routing_contract``); the family-detection logic is stream 4's.
"""

from __future__ import annotations

import logging

from omnigent.server.routing_contract import (
    CLAUDE_GATEWAY_HARNESSES,
    CODEX_GATEWAY_HARNESSES,
)

_logger = logging.getLogger(__name__)


def claude_gateway_inference_backed() -> bool:
    """Wave-1 stream 4 fills this: is claude-family inference gateway-backed?

    Config-only: inspect the resolved claude provider config (base URL /
    apiKeyHelper) and return whether it points at the workspace AI Gateway.
    """
    raise NotImplementedError("wave-1 stream 4")


def codex_gateway_inference_backed() -> bool:
    """Wave-1 stream 4 fills this: is codex-family inference gateway-backed?

    Config-only: inspect the resolved codex provider entry's base_url family.
    """
    raise NotImplementedError("wave-1 stream 4")


def gateway_inference_map() -> dict[str, bool]:
    """Per-harness map of whether this host's inference for that family is gateway-backed.

    The shape wave-1 stream 4 reports on the host frames. Every accepted
    spelling of a family carries that family's single result. A family whose
    check raises is omitted (unknown), never reported ``False``.
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
