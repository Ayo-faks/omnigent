"""File-backed server-wide announcements for the OSS server.

An admin posts short notices (maintenance windows, new-feature callouts,
incident banners) that every user sees in the app's top bar. The set is stored
as a JSON array in :func:`resolve_data_dir` (next to the ``admins`` roster and
the sharing overrides) so it survives restarts without a database migration and
takes effect without a redeploy — the read side is mtime-cached, mirroring the
``admins`` / sharing loaders, and the write side replaces the file atomically.

Each announcement is a small record:

- ``id`` — server-assigned, ``anc_`` + hex. Stable across edits so a user's
  per-announcement dismissal (client-side) sticks.
- ``message`` — the notice text (required, trimmed, length-capped).
- ``level`` — ``info`` / ``warning`` / ``success``, drives the banner styling.
- ``active`` — whether the banner shows it. Inactive rows stay in the file so an
  admin can toggle a notice off and back on without retyping it.
- ``dismissible`` — whether a user may dismiss it (a critical notice can be
  pinned).
- ``link_url`` / ``link_label`` — optional call-to-action link.
- ``created_at`` / ``updated_at`` — epoch seconds, server-managed.

A missing/empty/unreadable file yields an empty list (never raises) so a read on
the display hot path can't fail the app, and a malformed file is logged and
treated as empty rather than surfaced as an error.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omnigent.server.admin_list import resolve_data_dir

logger = logging.getLogger(__name__)

_ANNOUNCEMENTS_FILE = "announcements.json"

#: Levels an announcement may use, most-severe last. Anything else coerces to
#: ``info`` rather than being rejected on read (fail-safe display).
LEVELS: tuple[str, ...] = ("info", "warning", "success")

#: Guardrails so a single write can't bloat the file or the banner. The caps are
#: generous for a notice but bounded — the banner is chrome, not a content store.
MAX_MESSAGE_LEN = 2000
MAX_LABEL_LEN = 120
MAX_URL_LEN = 2000
MAX_ANNOUNCEMENTS = 50

# mtime cache keyed by absolute path → (mtime, parsed list). Keyed by path so a
# data-dir change (e.g. across tests) never reads through a stale entry.
_cache: dict[str, tuple[float, list[Announcement]]] = {}


@dataclass(frozen=True)
class Announcement:
    """One server-wide announcement (see the module docstring for fields)."""

    id: str
    message: str
    level: str
    active: bool
    dismissible: bool
    link_url: str | None
    link_label: str | None
    created_at: int
    updated_at: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the API and for on-disk storage (same shape)."""
        return {
            "object": "announcement",
            "id": self.id,
            "message": self.message,
            "level": self.level,
            "active": self.active,
            "dismissible": self.dismissible,
            "link_url": self.link_url,
            "link_label": self.link_label,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def resolve_announcements_path() -> Path:
    """Path of the file holding the announcements list."""
    return resolve_data_dir() / _ANNOUNCEMENTS_FILE


def _coerce_level(value: Any) -> str:
    """Return a known level, defaulting unknown/empty values to ``info``."""
    if isinstance(value, str) and value.strip().lower() in LEVELS:
        return value.strip().lower()
    return "info"


def _coerce_optional_str(value: Any, *, max_len: int) -> str | None:
    """Trim to a bounded string, or ``None`` for empty/non-string values."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    return trimmed[:max_len]


def _announcement_from_stored(raw: Any) -> Announcement | None:
    """Build an :class:`Announcement` from one stored/JSON record.

    Tolerant on read (missing/dirty fields are coerced or the row is dropped) so
    a hand-edited or older file never breaks the display path. Returns ``None``
    for a row with no usable message.
    """
    if not isinstance(raw, dict):
        return None
    message = raw.get("message")
    if not isinstance(message, str) or not message.strip():
        return None
    now = int(time.time())
    created = raw.get("created_at")
    updated = raw.get("updated_at")
    return Announcement(
        id=str(raw.get("id") or _new_id()),
        message=message.strip()[:MAX_MESSAGE_LEN],
        level=_coerce_level(raw.get("level")),
        active=bool(raw.get("active", True)),
        dismissible=bool(raw.get("dismissible", True)),
        link_url=_coerce_optional_str(raw.get("link_url"), max_len=MAX_URL_LEN),
        link_label=_coerce_optional_str(raw.get("link_label"), max_len=MAX_LABEL_LEN),
        created_at=created if isinstance(created, int) else now,
        updated_at=updated if isinstance(updated, int) else now,
    )


def _new_id() -> str:
    """Mint a stable announcement id (``anc_`` + 24 hex chars)."""
    return "anc_" + secrets.token_hex(12)


def read_announcements() -> list[Announcement]:
    """Return all announcements (active and inactive), newest first.

    mtime-cached: re-parses only when the file's modification time changes. A
    missing, empty, unreadable, or malformed file yields ``[]`` — the display
    path must never fail because of the announcements file.
    """
    path = resolve_announcements_path()
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _cache.pop(key, None)
        return []
    cached = _cache.get(key)
    if cached is not None and cached[0] == mtime:
        return list(cached[1])
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        parsed = json.loads(raw) if raw.strip() else []
    except json.JSONDecodeError:
        logger.warning("Ignoring malformed announcements file %s", path)
        _cache[key] = (mtime, [])
        return []
    if not isinstance(parsed, list):
        logger.warning("Ignoring announcements file %s: expected a JSON array", path)
        _cache[key] = (mtime, [])
        return []
    items = [a for a in (_announcement_from_stored(entry) for entry in parsed) if a is not None]
    items.sort(key=lambda a: a.created_at, reverse=True)
    _cache[key] = (mtime, items)
    return list(items)


def read_active_announcements() -> list[Announcement]:
    """Return only the announcements an admin has marked ``active``."""
    return [a for a in read_announcements() if a.active]


def write_announcements(items: list[Announcement]) -> list[Announcement]:
    """Persist the full announcements list atomically, newest first.

    Writes to a temp file in the data dir and ``os.replace``s it into place so a
    concurrent read never sees a half-written file, then invalidates the cache.
    Returns the stored list (sorted newest-first) so the caller can echo it.
    """
    ordered = sorted(items, key=lambda a: a.created_at, reverse=True)
    path = resolve_announcements_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps([a.to_dict() for a in ordered], indent=2)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    _cache.pop(str(path), None)
    return ordered
