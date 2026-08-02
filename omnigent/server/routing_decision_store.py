"""Main-agent routing-decision writer/reader + the routed model override.

The persistence half of Smart Routing (plan 2d). A routed create or turn
produces a :class:`~omnigent.server.smart_routing.RoutingResult`; stream 1's
:func:`~omnigent.server.routing_contract.resolve_route` turns that pick into a
servable :class:`~omnigent.server.routing_contract.ResolvedRoute` (or ``None``
on an honest decline). This module maps either outcome into a
:class:`RoutingDecisionData`, persists it as a ``routing_decision`` conversation
item (no new table — the web reads it from the session snapshot), and reads the
latest one back.

Three honesty rules from plan 3i live in :func:`build_decision`:

* A fallback stamps ``raw_model`` distinct from ``model`` — the chip shows the
  arm the router *asked for* next to the arm that was *applied*.
* A prefix/spelling-only difference is the same arm, so ``raw_model`` is dropped
  (a gateway spelling pin like ``glm-5-2`` → ``system.ai.glm-5-2`` is not a
  substitution).
* An honest decline records ``applied=false`` and writes no pin; the record
  keeps the router's would-have pick as ``model`` so the UI renders "would have
  picked X".

The routed model itself is pinned through the existing ``model_override``
session key (:func:`apply_routed_model_override` /
:func:`routed_model_override`); the store already persists that column, so this
module only names the round-trip the routing path uses. The
``subagent_routing_override`` key is owned elsewhere (wave-2) and is not touched
here.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any, Protocol

from omnigent.entities.conversation import NewConversationItem, RoutingDecisionData
from omnigent.server.routing_contract import (
    MODEL_ID_PREFIXES,
    ResolvedRoute,
    RoutingScope,
)

if TYPE_CHECKING:
    from omnigent.entities.conversation import ConversationItem

_logger = logging.getLogger(__name__)

#: Conversation-item type the decision persists as. Registered on the entity
#: side (``ITEM_TYPE_TO_DATA_CLS`` / ``NON_CONTENT_ITEM_TYPES``), so the agent
#: loop never feeds a router note back into the brain's context.
DECISION_ITEM_TYPE = "routing_decision"


class RouteResult(Protocol):
    """The pick shape this module maps from (main's ``RoutingResult``).

    A structural type so unit tests and the wave-2 gates can pass any object
    carrying the three fields without importing the routing seam.
    """

    model: str
    rationale: str
    harness: str | None


class _AppendStore(Protocol):
    """Minimal store surface the writer needs (satisfied by ``ConversationStore``)."""

    def append(
        self, conversation_id: str, items: list[NewConversationItem]
    ) -> list[ConversationItem]: ...


def _bare_id(model: str) -> str:
    """Strip a known catalog prefix so two spellings of one arm compare equal.

    Uses the contract's :data:`MODEL_ID_PREFIXES` (the single owned list) rather
    than the routing seam, so this module never waits on another stream.
    """
    bare = model.strip()
    for prefix in MODEL_ID_PREFIXES:
        if bare.startswith(prefix):
            return bare[len(prefix) :]
    return bare


def _distinct_arm(raw: str | None, model: str) -> str | None:
    """Return *raw* only when it names a different arm than *model*.

    A prefix/spelling-only difference (e.g. a gateway spelling pin) is the same
    arm, so it earns no ``raw_model`` — the chip must not read a spelling as a
    substitution (plan 3i).
    """
    if not raw:
        return None
    return raw if _bare_id(raw) != _bare_id(model) else None


def build_decision(
    *,
    model: str,
    rationale: str,
    harness: str | None = None,
    scope: RoutingScope = "turn",
    applied: bool = True,
    raw_model: str | None = None,
    attempted_override: str | None = None,
    agent: str | None = None,
    decision_id: str | None = None,
) -> RoutingDecisionData:
    """Build a :class:`RoutingDecisionData` from mapped routing fields.

    The primitive both :func:`decision_from_route` and the wave-2 gates use.
    ``raw_model`` is normalized through :func:`_distinct_arm`, so a caller can
    pass the router's verbatim pick unconditionally and a spelling-only
    difference is dropped. ``decision_id`` is generated when absent so every
    persisted row carries a stable join key.

    :param model: The servable model the turn runs on (``applied``), or the
        router's would-have pick (an honest decline, ``applied=false``).
    :param rationale: The router's one-line explanation.
    :param harness: Harness the routed model belongs to, e.g.
        ``"claude-native"``.
    :param scope: Which decision this is — session / turn / child_session /
        native_subagent.
    :param applied: ``True`` when the brain actually ran on ``model``.
    :param raw_model: The router's pick verbatim; kept only when it names a
        different arm than ``model``.
    :param attempted_override: The model the user had pinned when routing ran,
        rendered as "would have picked X, kept your Y".
    :param agent: Sub-agent name when mirrored into a parent transcript.
    :param decision_id: Stable id; generated when ``None``.
    :returns: The decision payload, ready for :func:`persist_decision`.
    """
    return RoutingDecisionData(
        model=model,
        applied=applied,
        rationale=rationale,
        agent=agent,
        harness=harness,
        scope=scope,
        decision_id=decision_id or _new_decision_id(),
        raw_model=_distinct_arm(raw_model, model),
        attempted_override=attempted_override,
    )


def decision_from_route(
    result: RouteResult,
    resolved: ResolvedRoute | None,
    *,
    scope: RoutingScope = "turn",
    attempted_override: str | None = None,
    agent: str | None = None,
    decision_id: str | None = None,
    default_model: str | None = None,
) -> RoutingDecisionData:
    """Map a route result + its resolution into a decision record.

    The two-outcome mapping the create/turn gates call once they have a pick:

    * **Resolved** (``resolved`` given): the arm is servable after the fallback
      and gateway-spelling pin. The record is ``applied=true`` on
      ``resolved.model``; ``resolved.raw_model`` becomes the chip's raw pick when
      a fallback made it name a different arm.
    * **Honest decline** (``resolved is None``): no servable arm and no servable
      fallback (plan 3i). The record is ``applied=false`` and writes no pin — it
      keeps the router's would-have pick as ``model`` (falling back to
      ``default_model`` only if the pick is somehow empty), so the UI renders
      "would have picked".

    :param result: The router's pick (``model`` / ``rationale`` / ``harness``).
    :param resolved: Stream 1's servable resolution, or ``None`` on a decline.
    :param scope: Which decision this is.
    :param attempted_override: Model the user had pinned, if any.
    :param agent: Sub-agent name when mirrored into a parent transcript.
    :param decision_id: Stable id; generated when ``None``.
    :param default_model: Session default, used only if a decline's pick is
        empty (keeps the non-empty-model invariant without inventing an id).
    :returns: The decision payload.
    """
    if resolved is None:
        return build_decision(
            model=result.model or default_model or "",
            rationale=result.rationale,
            harness=None,
            scope=scope,
            applied=False,
            raw_model=None,
            attempted_override=attempted_override,
            agent=agent,
            decision_id=decision_id,
        )
    return build_decision(
        model=resolved.model,
        rationale=result.rationale,
        harness=resolved.harness or result.harness,
        scope=scope,
        applied=True,
        raw_model=resolved.raw_model,
        attempted_override=attempted_override,
        agent=agent,
        decision_id=decision_id,
    )


async def persist_decision(
    session_id: str,
    store: _AppendStore,
    record: RoutingDecisionData,
) -> str | None:
    """Persist *record* as a ``routing_decision`` item and publish it live.

    The append runs in a worker thread (the store is sync) and is best-effort:
    a persist failure is logged, never raised, and the live event still fires so
    the web renders the chip. Mirrors the resilience of the other routing-decision
    writers.

    :param session_id: Session / conversation identifier.
    :param store: Store exposing ``append``.
    :param record: The decision to persist.
    :returns: The store-assigned item id, or ``None`` when the append failed.
    """
    item = NewConversationItem(
        type=DECISION_ITEM_TYPE,
        response_id=f"routing_{uuid.uuid4().hex}",
        data=record,
    )
    try:
        persisted = await asyncio.to_thread(store.append, session_id, [item])
        persisted_id: str | None = persisted[0].id if persisted else None
    except Exception:
        _logger.exception("routing decision persist failed for session=%s", session_id)
        persisted_id = None

    _publish_decision(session_id, persisted_id, record)
    return persisted_id


def _publish_decision(session_id: str, item_id: str | None, record: RoutingDecisionData) -> None:
    """Broadcast the decision chip to any live SSE subscriber.

    Lazily imports ``session_stream`` so this module stays importable without
    the runtime (unit tests, catalog probes). A no-op when nothing is listening.
    """
    from omnigent.runtime import session_stream

    session_stream.publish(
        session_id,
        {
            "type": "response.output_item.done",
            "item": {
                "id": item_id,
                "type": DECISION_ITEM_TYPE,
                **record.model_dump(),
            },
        },
    )


def latest_decision(
    session_id: str,
    store: Any,
) -> RoutingDecisionData | None:
    """Return the newest persisted routing decision for a session.

    The read-back the web snapshot and wave-2 gates use to answer "what did the
    router last decide for this session". Reads the single newest
    ``routing_decision`` item; the store's ``type`` filter keeps it a point read
    rather than a transcript scan.

    :param session_id: Session / conversation identifier.
    :param store: Store exposing ``list_items``.
    :returns: The latest decision payload, or ``None`` when the session has no
        routing decision.
    """
    page = store.list_items(session_id, limit=1, order="desc", type=DECISION_ITEM_TYPE)
    for item in page.data:
        data = item.data
        if isinstance(data, RoutingDecisionData):
            return data
    return None


# ── Routed model override round-trip (the routing session key) ───────────────


def apply_routed_model_override(
    session_id: str,
    store: Any,
    model: str,
) -> None:
    """Pin *model* as the session's ``model_override``.

    The write half of the routed-model round-trip. The store already persists
    ``model_override`` (no schema change); this names the one write the routing
    path makes so create/turn gates share a single entry point.

    :param session_id: Session / conversation identifier.
    :param store: Store exposing ``update_conversation``.
    :param model: The routed servable model id to pin.
    """
    store.update_conversation(session_id, model_override=model)


def routed_model_override(
    session_id: str,
    store: Any,
) -> str | None:
    """Read back the session's pinned ``model_override``.

    :param session_id: Session / conversation identifier.
    :param store: Store exposing ``get_conversation``.
    :returns: The pinned model id, or ``None`` when the session is unpinned or
        missing.
    """
    conv = store.get_conversation(session_id)
    return getattr(conv, "model_override", None) if conv is not None else None


def _new_decision_id() -> str:
    """Return a fresh decision id (the transcript ↔ telemetry join key)."""
    return f"rd_{uuid.uuid4().hex}"
