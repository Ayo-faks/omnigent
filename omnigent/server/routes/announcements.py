"""Server-wide announcements shown in the app's top bar.

``GET /v1/announcements`` returns the *active* announcements for the banner —
readable by anyone (like ``GET /v1/info``), so the top bar renders without an
admin session. ``GET /v1/announcements/all`` returns every announcement (active
and inactive) for the admin editor, and ``PUT /v1/announcements`` replaces the
whole list (admin only), persisting ``<data_dir>/announcements.json``.

The full-list replace mirrors ``PUT /v1/sharing``: the admin editor holds the
list client-side and saves it in one shot, so a single atomic write is the whole
transaction. Incoming rows carry an ``id`` for edits (``created_at`` is
preserved) or omit it for new rows (server mints the id and timestamps).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.announcements_settings import (
    LEVELS,
    MAX_ANNOUNCEMENTS,
    MAX_LABEL_LEN,
    MAX_MESSAGE_LEN,
    MAX_URL_LEN,
    Announcement,
    _new_id,
    read_active_announcements,
    read_announcements,
    write_announcements,
)
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id
from omnigent.stores.permission_store import PermissionStore


class AnnouncementInput(BaseModel):
    """One announcement in a ``PUT /v1/announcements`` body.

    ``id`` is present when editing an existing row (its ``created_at`` is
    preserved) and absent for a new row (the server mints both).
    """

    id: str | None = None
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LEN)
    level: str = "info"
    active: bool = True
    dismissible: bool = True
    link_url: str | None = Field(default=None, max_length=MAX_URL_LEN)
    link_label: str | None = Field(default=None, max_length=MAX_LABEL_LEN)


class SetAnnouncementsRequest(BaseModel):
    """Body for ``PUT /v1/announcements`` — the full replacement list."""

    announcements: list[AnnouncementInput]


def _validated_link_url(value: str | None) -> str | None:
    """Return a safe link URL, or raise 400 for an unsupported scheme.

    Only ``http(s)`` absolute URLs and site-relative paths (``/…``) are allowed;
    ``javascript:`` / ``data:`` and the like are rejected so an admin-set link
    can't smuggle script into every user's banner.
    """
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    if trimmed.startswith("/") and not trimmed.startswith("//"):
        return trimmed
    scheme = urlparse(trimmed).scheme.lower()
    if scheme in ("http", "https"):
        return trimmed
    raise OmnigentError(
        "Announcement links must be an http(s) URL or a site-relative path.",
        code=ErrorCode.INVALID_INPUT,
    )


def _to_announcement(item: AnnouncementInput, existing: dict[str, Announcement]) -> Announcement:
    """Build a stored :class:`Announcement` from one request row.

    Rejects an unknown ``level`` with 400 (so a typo surfaces rather than
    silently downgrading to ``info``). Preserves ``created_at`` for an edited row
    and stamps ``updated_at`` on every write.
    """
    message = item.message.strip()
    if not message:
        # ``min_length=1`` only rejects an empty raw string; a whitespace-only
        # message trims to nothing and would be dropped on read, so reject it
        # here rather than storing a row that silently vanishes.
        raise OmnigentError(
            "Announcement message cannot be empty.",
            code=ErrorCode.INVALID_INPUT,
        )
    level = item.level.strip().lower()
    if level not in LEVELS:
        raise OmnigentError(
            f"Unknown announcement level {item.level!r}. Expected one of: "
            + ", ".join(LEVELS)
            + ".",
            code=ErrorCode.INVALID_INPUT,
        )
    now = int(time.time())
    prior = existing.get(item.id) if item.id else None
    link_url = _validated_link_url(item.link_url)
    label = (item.link_label or "").strip()
    return Announcement(
        id=prior.id if prior else _new_id(),
        message=message[:MAX_MESSAGE_LEN],
        level=level,
        active=item.active,
        dismissible=item.dismissible,
        link_url=link_url,
        # A label with no link is meaningless — drop it so the stored row is
        # coherent. A link with no label renders with the URL as its own text.
        link_label=(label[:MAX_LABEL_LEN] if link_url and label else None),
        created_at=prior.created_at if prior else now,
        updated_at=now,
    )


async def _require_admin(
    request: Request,
    auth_provider: AuthProvider | None,
    permission_store: PermissionStore | None,
) -> None:
    """Verify the caller is an admin, mirroring the sharing-router gate.

    Single-user mode (no permission store) skips the check. Multi-user mode
    raises 401 if unauthenticated or 403 if the user is not an admin.
    """
    if permission_store is None:
        return
    user_id = get_user_id(request, auth_provider)
    if user_id is None:
        raise OmnigentError("Authentication required", code=ErrorCode.UNAUTHORIZED)
    is_admin = await asyncio.to_thread(permission_store.is_admin, user_id)
    if not is_admin:
        raise OmnigentError(
            "Admin privileges required to manage announcements",
            code=ErrorCode.FORBIDDEN,
        )


def create_announcements_router(
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
) -> APIRouter:
    """Build the announcements router (mounted under ``/v1``)."""
    router = APIRouter()

    @router.get("/announcements")
    async def list_active(request: Request) -> dict[str, Any]:
        """Active announcements for the top-bar banner (any caller)."""
        items = await asyncio.to_thread(read_active_announcements)
        return {"object": "list", "data": [a.to_dict() for a in items]}

    @router.get("/announcements/all")
    async def list_all(request: Request) -> dict[str, Any]:
        """Every announcement, active or not, for the admin editor (admin only)."""
        await _require_admin(request, auth_provider, permission_store)
        items = await asyncio.to_thread(read_announcements)
        return {"object": "list", "data": [a.to_dict() for a in items]}

    @router.put("/announcements")
    async def replace(request: Request, body: SetAnnouncementsRequest) -> dict[str, Any]:
        """Replace the full announcements list (admin only).

        Validates and authorizes every row before writing so a bad row (unknown
        level, unsafe link, over the count cap) rejects the whole request rather
        than persisting a partial list.
        """
        await _require_admin(request, auth_provider, permission_store)
        if len(body.announcements) > MAX_ANNOUNCEMENTS:
            raise OmnigentError(
                f"Too many announcements (max {MAX_ANNOUNCEMENTS}).",
                code=ErrorCode.INVALID_INPUT,
            )
        existing = {a.id: a for a in await asyncio.to_thread(read_announcements)}
        items = [_to_announcement(item, existing) for item in body.announcements]
        stored = await asyncio.to_thread(write_announcements, items)
        return {"object": "list", "data": [a.to_dict() for a in stored]}

    return router
