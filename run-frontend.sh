#!/bin/sh
set -eu
. "$(cd "$(dirname "$0")" && pwd)/dev-env.sh"
# nvm's lazy-load shim breaks in non-interactive shells; use the binary directly.
PATH="$HOME/.nvm/versions/node/v24.14.0/bin:$PATH"
cd "$WORKTREE/web"
# No `--` before the flags: `pnpm run dev -- --port N` makes vite treat
# `--port N` as post-`--` app args and ignore it, so the dev server drifts to
# a default port (5173/5174) instead of ROUTING_FRONTEND_PORT. --strictPort
# fails loudly if the port is taken rather than silently picking another.
OMNIGENT_URL="http://localhost:$ROUTING_SERVER_PORT" exec pnpm run dev --port "$ROUTING_FRONTEND_PORT" --strictPort
