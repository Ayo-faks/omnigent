"""Application-level WebSocket close codes for the terminal bridges.

A leaf module on purpose: these codes are the wire contract between the
server's ``/attach`` route, the runner, the native CLI client, and the
browser, so all four need them without importing the bridge that serves
the socket. :mod:`omnigent.terminals.ws_bridge` pulls in FastAPI and the
tmux machinery, which a CLI client reading a close code has no use for.

Keep this module dependency-free. ``web/src/components/blocks/
TerminalSession.ts`` mirrors these values on the browser side.
"""

from __future__ import annotations

from typing import Final

# RFC 6455 reserves the 4xxx band for application use. The 44xx band
# mirrors HTTP 4xx, as 4500 mirrors 5xx.

# 4404 tells the client's reconnect loop to stop — sent on a
# pre-attach lookup miss and on PTY EOF when the tmux session is
# genuinely gone (Claude exited / the session was killed).
WS_CLOSE_TERMINAL_NOT_FOUND: Final[int] = 4404

# 4405 means the user *detached* from tmux: the ``tmux attach`` child
# exited (PTY EOF) but the session is still alive. The client must NOT
# treat this as a terminal-gone exit: a detach misread as 4404 would
# tear the whole session (and runner) down.
WS_CLOSE_TERMINAL_DETACHED: Final[int] = 4405

# 4400 is the WS analogue of the HTTP 400 ``wrong_replica``: the runner
# tunnel is bound but not on this replica (the ``?omnigent_slice_key=``
# reached a replica that doesn't hold the tunnel — the key doesn't match
# where it lives). Unlike 4500 (a genuine failure), the request is valid
# and just misrouted: the client re-dials keyless and reaches the replica
# the tunnel actually lives on. Mirrors the fetch path's keyless
# re-address on a ``wrong_replica`` 400.
WS_CLOSE_WRONG_REPLICA: Final[int] = 4400

WS_CLOSE_INTERNAL_ERROR: Final[int] = 4500

__all__ = [
    "WS_CLOSE_INTERNAL_ERROR",
    "WS_CLOSE_TERMINAL_DETACHED",
    "WS_CLOSE_TERMINAL_NOT_FOUND",
    "WS_CLOSE_WRONG_REPLICA",
]
