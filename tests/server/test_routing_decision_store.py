"""Directed tests for the main-agent routing-decision writer/reader (plan 2d).

Covers the behavior inventory for decision persistence:

* a fallback stamps ``raw_model`` distinct from ``model`` (honest chip);
* a prefix/spelling-only difference is the same arm and drops ``raw_model``;
* session-scope vs turn-scope persist with the right ``scope``;
* an honest decline (``applied=false``) persists with no pin;
* reading back returns the latest decision;
* the routed ``model_override`` round-trips (write + read back).

A self-contained fake store keeps the suite fast and DB-free — the real store
needs a database, and these behaviors are pure store-contract logic (``append``
+ ``list_items(type=, order=)`` + ``update_conversation`` + ``get_conversation``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from omnigent.entities.conversation import (
    ConversationItem,
    NewConversationItem,
)
from omnigent.entities.pagination import PagedList
from omnigent.server.routing_contract import ResolvedRoute
from omnigent.server.routing_decision_store import (
    apply_routed_model_override,
    build_decision,
    decision_from_route,
    latest_decision,
    persist_decision,
    routed_model_override,
)

# Servable arms + a couple of spellings used across the cases.
CLAUDE_OPUS = "databricks-claude-opus-4-8"
CLAUDE_SONNET = "databricks-claude-sonnet-5"  # the claude family fallback
GLM_ARM = "glm-5-2"
GLM_SERVABLE = "system.ai.glm-5-2"  # the gateway spelling pin for glm


# ── Fakes ────────────────────────────────────────────────────────────────────


@dataclass
class _FakeConversation:
    id: str
    model_override: str | None = None
    harness_override: str | None = None
    cost_control_mode_override: str | None = None


@dataclass
class _FakeStore:
    """A faithful, in-memory subset of ``ConversationStore``.

    Implements only what the module touches: ``append`` (assigns ids +
    preserves order), ``list_items`` (``type`` filter + ``asc``/``desc`` +
    ``limit``), ``update_conversation`` (``model_override``), and
    ``get_conversation``.
    """

    convs: dict[str, _FakeConversation] = field(default_factory=dict)
    items: dict[str, list[ConversationItem]] = field(default_factory=dict)
    _seq: int = 0

    def create(self, conversation_id: str) -> _FakeConversation:
        conv = _FakeConversation(id=conversation_id)
        self.convs[conversation_id] = conv
        return conv

    def append(
        self, conversation_id: str, items: list[NewConversationItem]
    ) -> list[ConversationItem]:
        persisted: list[ConversationItem] = []
        bucket = self.items.setdefault(conversation_id, [])
        for new in items:
            self._seq += 1
            item = ConversationItem(
                id=f"item_{self._seq}",
                type=new.type,
                status="completed",
                response_id=new.response_id,
                created_at=self._seq,
                data=new.data,
            )
            bucket.append(item)
            persisted.append(item)
        return persisted

    def list_items(
        self,
        conversation_id: str,
        limit: int = 100,
        after: str | None = None,
        before: str | None = None,
        order: str = "asc",
        type: str | None = None,
    ) -> PagedList[ConversationItem]:
        rows = list(self.items.get(conversation_id, []))
        if type is not None:
            rows = [row for row in rows if row.type == type]
        if order == "desc":
            rows = list(reversed(rows))
        rows = rows[:limit]
        return PagedList(
            data=rows,
            first_id=rows[0].id if rows else None,
            last_id=rows[-1].id if rows else None,
            has_more=False,
        )

    def update_conversation(
        self,
        conversation_id: str,
        model_override: str | None = None,
        **_: Any,
    ) -> _FakeConversation | None:
        conv = self.convs.get(conversation_id)
        if conv is None:
            return None
        if model_override is not None:
            conv.model_override = model_override
        return conv

    def get_conversation(self, conversation_id: str) -> _FakeConversation | None:
        return self.convs.get(conversation_id)


@dataclass
class _FakeRouteResult:
    """A stand-in for main's ``RoutingResult`` (structural ``RouteResult``)."""

    model: str
    rationale: str
    harness: str | None = None


# ── build_decision: raw_model honesty ────────────────────────────────────────


def test_fallback_stamps_raw_model_distinct_from_model() -> None:
    """A family fallback applied a different arm than the router picked, so the
    chip must show both: raw_model (the pick) != model (what ran)."""
    record = build_decision(
        model=CLAUDE_SONNET,
        rationale="fell back to the family default",
        harness="claude-native",
        raw_model=CLAUDE_OPUS,
        applied=True,
    )
    assert record.model == CLAUDE_SONNET
    assert record.raw_model == CLAUDE_OPUS
    assert record.raw_model != record.model
    assert record.applied is True


def test_spelling_only_difference_drops_raw_model() -> None:
    """A gateway spelling pin (glm-5-2 -> system.ai.glm-5-2) is the SAME arm, so
    it is not a substitution and earns no raw_model."""
    record = build_decision(
        model=GLM_SERVABLE,
        rationale="glm served under its system.ai route",
        harness="codex-native",
        raw_model=GLM_ARM,
        applied=True,
    )
    assert record.model == GLM_SERVABLE
    assert record.raw_model is None


def test_build_decision_generates_decision_id_when_absent() -> None:
    """Every persisted row carries a stable join key even when the caller omits
    one."""
    record = build_decision(model=CLAUDE_OPUS, rationale="deep reasoning")
    assert record.decision_id
    explicit = build_decision(model=CLAUDE_OPUS, rationale="x", decision_id="rd_fixed")
    assert explicit.decision_id == "rd_fixed"


# ── scope: session vs turn ────────────────────────────────────────────────────


async def test_session_scope_decision_persists_with_session_scope() -> None:
    store = _FakeStore()
    store.create("conv_s")
    record = build_decision(model=CLAUDE_OPUS, rationale="session pick", scope="session")
    await persist_decision("conv_s", store, record)

    read = latest_decision("conv_s", store)
    assert read is not None
    assert read.scope == "session"


async def test_turn_scope_decision_persists_with_turn_scope() -> None:
    store = _FakeStore()
    store.create("conv_t")
    # "turn" is the default scope.
    record = build_decision(model=CLAUDE_OPUS, rationale="turn pick")
    await persist_decision("conv_t", store, record)

    read = latest_decision("conv_t", store)
    assert read is not None
    assert read.scope == "turn"


# ── honest decline: applied=false, no pin ─────────────────────────────────────


async def test_honest_decline_persists_unapplied_with_no_pin() -> None:
    """resolve_route returned None: the workspace serves neither the pick nor the
    family fallback. The record keeps the router's would-have pick, records
    applied=false, and writes no harness/raw pin."""
    result = _FakeRouteResult(model="databricks-kimi-k2-6", rationale="would have picked kimi")
    record = decision_from_route(result, None, scope="turn")

    assert record.applied is False
    assert record.model == "databricks-kimi-k2-6"  # the would-have pick, kept
    assert record.harness is None
    assert record.raw_model is None

    store = _FakeStore()
    store.create("conv_d")
    await persist_decision("conv_d", store, record)
    read = latest_decision("conv_d", store)
    assert read is not None
    assert read.applied is False
    assert read.model == "databricks-kimi-k2-6"


async def test_decline_falls_back_to_default_model_when_pick_empty() -> None:
    """A decline with an empty pick keeps the record valid (non-empty model
    invariant) by naming the session default — never an invented id."""
    result = _FakeRouteResult(model="", rationale="no pick")
    record = decision_from_route(result, None, scope="turn", default_model=CLAUDE_SONNET)
    assert record.applied is False
    assert record.model == CLAUDE_SONNET


# ── decision_from_route: resolved (applied) path ──────────────────────────────


def test_decision_from_resolved_route_is_applied() -> None:
    result = _FakeRouteResult(
        model=CLAUDE_OPUS, rationale="deep reasoning", harness="claude-native"
    )
    resolved = ResolvedRoute(model=CLAUDE_OPUS, harness="claude-native", raw_model=CLAUDE_OPUS)
    record = decision_from_route(result, resolved, scope="session")
    assert record.applied is True
    assert record.model == CLAUDE_OPUS
    assert record.harness == "claude-native"
    assert record.scope == "session"
    # raw == model → same arm → no separate raw pick.
    assert record.raw_model is None


def test_decision_from_resolved_route_keeps_fallback_raw_model() -> None:
    """When the resolution fell back, the resolved.raw_model (the pick) rides
    onto the record as a distinct arm."""
    result = _FakeRouteResult(model=CLAUDE_OPUS, rationale="fell back", harness="claude-native")
    resolved = ResolvedRoute(model=CLAUDE_SONNET, harness="claude-native", raw_model=CLAUDE_OPUS)
    record = decision_from_route(result, resolved)
    assert record.applied is True
    assert record.model == CLAUDE_SONNET
    assert record.raw_model == CLAUDE_OPUS


# ── read-back: latest wins ────────────────────────────────────────────────────


async def test_latest_decision_returns_the_newest() -> None:
    store = _FakeStore()
    store.create("conv_l")
    await persist_decision("conv_l", store, build_decision(model=CLAUDE_OPUS, rationale="first"))
    await persist_decision(
        "conv_l", store, build_decision(model=CLAUDE_SONNET, rationale="second")
    )

    read = latest_decision("conv_l", store)
    assert read is not None
    assert read.model == CLAUDE_SONNET
    assert read.rationale == "second"


def test_latest_decision_none_when_no_decisions() -> None:
    store = _FakeStore()
    store.create("conv_empty")
    assert latest_decision("conv_empty", store) is None


async def test_latest_decision_ignores_other_item_types() -> None:
    """The read-back is a point read on the routing_decision type — a later
    message item must not shadow the routing decision."""
    store = _FakeStore()
    store.create("conv_mixed")
    await persist_decision(
        "conv_mixed", store, build_decision(model=CLAUDE_OPUS, rationale="routed")
    )
    # A non-routing item appended afterwards.
    from omnigent.entities.conversation import MessageData

    store.append(
        "conv_mixed",
        [
            NewConversationItem(
                type="message",
                response_id="resp_x",
                data=MessageData(
                    role="assistant",
                    content=[{"type": "output_text", "text": "hi"}],
                    agent="worker",
                ),
            )
        ],
    )
    read = latest_decision("conv_mixed", store)
    assert read is not None
    assert read.model == CLAUDE_OPUS


async def test_persist_returns_item_id() -> None:
    store = _FakeStore()
    store.create("conv_id")
    item_id = await persist_decision(
        "conv_id", store, build_decision(model=CLAUDE_OPUS, rationale="x")
    )
    assert item_id == "item_1"


async def test_persist_failure_does_not_raise() -> None:
    """A store append failure is best-effort: logged, not raised, and the id is
    None. The routing turn must never die on a persist error."""

    class _BoomStore:
        def append(
            self, conversation_id: str, items: list[NewConversationItem]
        ) -> list[ConversationItem]:
            raise RuntimeError("db down")

    item_id = await persist_decision(
        "conv_boom", _BoomStore(), build_decision(model=CLAUDE_OPUS, rationale="x")
    )
    assert item_id is None


# ── routed model override round-trip ──────────────────────────────────────────


def test_routed_model_override_round_trip() -> None:
    store = _FakeStore()
    store.create("conv_ov")
    assert routed_model_override("conv_ov", store) is None

    apply_routed_model_override("conv_ov", store, CLAUDE_OPUS)
    assert routed_model_override("conv_ov", store) == CLAUDE_OPUS

    # A re-route repins to the new model.
    apply_routed_model_override("conv_ov", store, CLAUDE_SONNET)
    assert routed_model_override("conv_ov", store) == CLAUDE_SONNET


def test_routed_model_override_missing_conversation_is_none() -> None:
    store = _FakeStore()
    assert routed_model_override("conv_absent", store) is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
