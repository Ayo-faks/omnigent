"""Client transport for the generic native stuck-prompt elicitation.

The pure detector (:mod:`omnigent.native_startup_supervisor`) decides WHEN a
native TUI is stuck and builds the elicitation params; this module is the I/O
half that surfaces the card to the web UI and clears it, over the harness-neutral
``native-elicitation-request`` hook + the ``external_elicitation_resolved``
session event.

It mirrors the agy interaction bridge's transport
(:func:`omnigent.antigravity_native_reader._post_agy_elicitation_request` /
``_post_external_elicitation_resolved``) but is deliberately harness-agnostic so
any tmux-driven forwarder can reuse one path rather than each growing its own
copy:

* :func:`request_native_elicitation` — POSTs the card to the hook and
  long-poll-awaits the human verdict, re-POSTing across severed long-polls so a
  proxy idle-cut re-parks the SAME card. Returns ``None`` on timeout/decline so
  the caller stops cleanly.
* :func:`resolve_native_elicitation` — POSTs ``external_elicitation_resolved`` so
  a card whose underlying prompt was answered (the idle marker reappeared, or the
  user answered in the terminal directly) is withdrawn from the web UI and any
  in-flight :func:`request_native_elicitation` long-poll returns.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from omnigent.native_terminal import url_component
from omnigent.server.schemas import ElicitationRequestParams, ElicitationResult

_logger = logging.getLogger(__name__)

# Day-long park matching the server-side ``_NATIVE_ELICITATION_HOOK_TIMEOUT_S``
# so the client budget outlives the server wait (the server's own timeout wins
# over a client cut). Also the total re-POST budget across severed long-polls.
_REQUEST_TIMEOUT_SECONDS = 86405.0
# Fail an unreachable-server connect fast into the backoff loop instead of
# inheriting the day-long read budget.
_CONNECT_TIMEOUT_SECONDS = 30.0
# First retry lands inside the server's re-park grace; later retries back off.
_RETRY_INITIAL_BACKOFF_SECONDS = 1.0
_RETRY_MAX_BACKOFF_SECONDS = 30.0
_RESOLVE_TIMEOUT_SECONDS = 10.0

_HOOK_PATH = "native-elicitation-request"


async def _retry_sleep(seconds: float) -> None:
    """Sleep between hook re-POSTs (seam kept tiny so tests can patch it)."""
    await asyncio.sleep(seconds)


async def _post_request(
    client: httpx.AsyncClient,
    session_id: str,
    *,
    elicitation_id: str,
    params: ElicitationRequestParams,
) -> httpx.Response | None:
    """POST one stuck-prompt elicitation, re-POSTing across severed long-polls.

    A single failed/5xx POST must not abandon the card: the id is deterministic,
    so a re-POST of the same body re-parks the SAME elicitation server-side and
    can collect a verdict that landed between attempts. Transport errors and 5xx
    are retried within :data:`_REQUEST_TIMEOUT_SECONDS`; 2xx and 4xx are final.

    :param client: HTTP client for Omnigent hook posts.
    :param session_id: Omnigent conversation id.
    :param elicitation_id: Deterministic id from ``stuck_elicitation_id``.
    :param params: The web-renderable elicitation params.
    :returns: The final hook response, or ``None`` when the retry budget ran out.
    """
    url = f"/v1/sessions/{url_component(session_id)}/hooks/{_HOOK_PATH}"
    body: dict[str, object] = {
        "elicitation_id": elicitation_id,
        "params": params.model_dump(),
    }
    timeout = httpx.Timeout(_REQUEST_TIMEOUT_SECONDS, connect=_CONNECT_TIMEOUT_SECONDS)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _REQUEST_TIMEOUT_SECONDS
    backoff_s = _RETRY_INITIAL_BACKOFF_SECONDS
    while True:
        response: httpx.Response | None = None
        try:
            response = await client.post(url, json=body, timeout=timeout)
        except httpx.HTTPError:
            _logger.warning(
                "native stuck elicitation hook POST failed; retrying: elicitation_id=%s",
                elicitation_id,
                exc_info=True,
            )
        if response is not None and response.status_code < 500:
            return response
        if response is not None:
            _logger.warning(
                "native stuck elicitation hook returned %s; retrying: elicitation_id=%s",
                response.status_code,
                elicitation_id,
            )
        if loop.time() + backoff_s >= deadline:
            _logger.warning(
                "native stuck elicitation hook retry budget exhausted: elicitation_id=%s",
                elicitation_id,
            )
            return None
        await _retry_sleep(backoff_s)
        backoff_s = min(backoff_s * 2, _RETRY_MAX_BACKOFF_SECONDS)


async def request_native_elicitation(
    client: httpx.AsyncClient,
    session_id: str,
    *,
    elicitation_id: str,
    params: ElicitationRequestParams,
) -> ElicitationResult | None:
    """Surface a stuck-prompt card and long-poll-await the human verdict.

    * a 2xx with a body → parse it as :class:`ElicitationResult`;
    * a 2xx with an EMPTY body → ``None`` (server timed out / upstream
      disconnect — the prompt is handled by withdraw or the user answering in the
      terminal directly);
    * a 4xx, an exhausted-retry ``None``, or a non-JSON / non-object body →
      ``None`` (logged; no verdict).

    :param client: HTTP client for Omnigent hook posts.
    :param session_id: Omnigent conversation id.
    :param elicitation_id: Deterministic id from ``stuck_elicitation_id``.
    :param params: The web-renderable elicitation params.
    :returns: The parsed :class:`ElicitationResult`, or ``None``.
    """
    response = await _post_request(
        client, session_id, elicitation_id=elicitation_id, params=params
    )
    if response is None:
        return None
    if response.status_code >= 400:
        _logger.warning(
            "native stuck elicitation hook rejected request (a 4xx is a "
            "client/config error, not transient): status=%s elicitation_id=%s body=%s",
            response.status_code,
            elicitation_id,
            response.text[:512],
        )
        return None
    if not response.content:
        return None
    try:
        return ElicitationResult.model_validate_json(response.content)
    except ValueError:
        _logger.warning(
            "native stuck elicitation hook returned a malformed body: elicitation_id=%s",
            elicitation_id,
            exc_info=True,
        )
        return None


async def resolve_native_elicitation(
    client: httpx.AsyncClient,
    session_id: str,
    elicitation_id: str,
) -> None:
    """Withdraw a stuck-prompt card once its prompt is no longer outstanding.

    POSTs ``external_elicitation_resolved`` so the parked web card clears and any
    in-flight :func:`request_native_elicitation` long-poll returns ``None``.
    Best-effort: a failed POST is logged, not raised — the caller is a long-lived
    poll loop that must stay alive.

    :param client: HTTP client for Omnigent event posts.
    :param session_id: Omnigent conversation id.
    :param elicitation_id: The id of the card to withdraw.
    :returns: None.
    """
    try:
        response = await client.post(
            f"/v1/sessions/{url_component(session_id)}/events",
            json={
                "type": "external_elicitation_resolved",
                "data": {"elicitation_id": elicitation_id},
            },
            timeout=_RESOLVE_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            _logger.warning(
                "native external_elicitation_resolved rejected: status=%s body=%s",
                response.status_code,
                response.text[:512],
            )
    except httpx.HTTPError:
        _logger.exception("native external_elicitation_resolved POST failed")
