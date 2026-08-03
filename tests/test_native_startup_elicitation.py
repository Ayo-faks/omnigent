"""Tests for the generic native stuck-elicitation client transport.

Exercise :mod:`omnigent.native_startup_elicitation` over a real
:class:`httpx.AsyncClient` backed by a :class:`httpx.MockTransport`, so the
hook-POST / long-poll / resolve wire behaviour is covered without a live server.

Scenarios:
- request → 200 with an ElicitationResult body → the parsed verdict is returned.
- request → 200 with an EMPTY body (server timeout / disconnect) → ``None``.
- request → 4xx (misconfigured hook) → ``None`` (no retry storm).
- resolve → posts ``external_elicitation_resolved`` with the elicitation id.
"""

from __future__ import annotations

import httpx
import pytest

from omnigent.native_startup_elicitation import (
    request_native_elicitation,
    resolve_native_elicitation,
)
from omnigent.native_startup_supervisor import native_terminal_input_params


def _params():  # type: ignore[no-untyped-def]
    return native_terminal_input_params(pane="Choose a theme", terminal_id="term_1")


@pytest.mark.asyncio
async def test_request_returns_parsed_verdict() -> None:
    """A 200 with an ElicitationResult body is parsed and returned."""
    posted: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(request)
        return httpx.Response(
            200, json={"action": "accept", "content": {"keys": ["1", "Enter"]}}
        )

    client = httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )
    async with client:
        result = await request_native_elicitation(
            client, "conv_1", elicitation_id="elicit_stuck_x", params=_params()
        )
    assert result is not None
    assert result.action == "accept"
    assert result.content == {"keys": ["1", "Enter"]}
    # Hit the harness-neutral hook path.
    assert posted[0].url.path == "/v1/sessions/conv_1/hooks/native-elicitation-request"


@pytest.mark.asyncio
async def test_request_empty_body_is_none() -> None:
    """A 200 with no body (server timeout/disconnect) yields ``None``."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    client = httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )
    async with client:
        result = await request_native_elicitation(
            client, "conv_1", elicitation_id="elicit_stuck_x", params=_params()
        )
    assert result is None


@pytest.mark.asyncio
async def test_request_4xx_is_none_without_retry() -> None:
    """A 4xx is final (client/config error) — returns ``None``, no retry storm."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad hook config")

    client = httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )
    async with client:
        result = await request_native_elicitation(
            client, "conv_1", elicitation_id="elicit_stuck_x", params=_params()
        )
    assert result is None
    assert calls["n"] == 1  # not retried


@pytest.mark.asyncio
async def test_resolve_posts_external_elicitation_resolved() -> None:
    """Resolve posts the withdraw event carrying the elicitation id."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )
    async with client:
        await resolve_native_elicitation(client, "conv_1", "elicit_stuck_x")
    assert seen == [
        {
            "type": "external_elicitation_resolved",
            "data": {"elicitation_id": "elicit_stuck_x"},
        }
    ]
