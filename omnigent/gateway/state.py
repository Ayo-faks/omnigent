"""Gateway-servlet discovery state.

The host daemon writes ``~/.omnigent/gateway-servlet.json`` when the servlet
is listening; session-launch code on the same machine reads it to register
sessions. The file carries the loopback URL and the admin bearer, so it is
written 0600. A stale file (daemon crash) is harmless: registration attempts
against a dead URL fail fast and launches fall back to the direct gateway.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

_logger = logging.getLogger(__name__)


def _state_path() -> Path:
    """
    Path of the servlet discovery file.

    :returns: ``~/.omnigent/gateway-servlet.json`` (same convention as
        ``host.pid``).
    """
    return Path.home() / ".omnigent" / "gateway-servlet.json"


@dataclass(frozen=True)
class ServletState:
    """Published servlet coordinates.

    :param url: Loopback base URL, e.g. ``"http://127.0.0.1:53211"``.
    :param admin_token: Bearer for the ``/admin/*`` control plane.
    :param pid: PID of the host process that owns the listener.
    """

    url: str
    admin_token: str
    pid: int


def write_servlet_state(state: ServletState) -> None:
    """
    Atomically publish the servlet state file at mode 0600.

    :param state: Coordinates to publish.
    :returns: None.
    """
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"url": state.url, "admin_token": state.admin_token, "pid": state.pid}
    fd, tmp_name = tempfile.mkstemp(prefix=".gateway-servlet.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.write("\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def read_servlet_state() -> ServletState | None:
    """
    Read the published servlet state, if any.

    :returns: Parsed :class:`ServletState`, or ``None`` when the file is
        absent or unreadable/malformed (callers fall back to the direct
        gateway).
    """
    try:
        raw = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    url = raw.get("url")
    admin_token = raw.get("admin_token")
    pid = raw.get("pid")
    if not isinstance(url, str) or not url or not isinstance(admin_token, str) or not admin_token:
        return None
    return ServletState(url=url, admin_token=admin_token, pid=pid if isinstance(pid, int) else -1)


def clear_servlet_state(owner_pid: int) -> None:
    """
    Remove the state file, but only when this process still owns it.

    :param owner_pid: PID that wrote the file; a file re-written by a newer
        daemon (different pid) is left in place.
    :returns: None.
    """
    state = read_servlet_state()
    if state is None or state.pid != owner_pid:
        return
    try:
        _state_path().unlink(missing_ok=True)
    except OSError:
        _logger.warning("could not remove %s", _state_path(), exc_info=True)
