#!/usr/bin/env bash
# Barrier-1 apply check (plan 4e barrier 1, item 3).
#
# Proves the two hardest layers — claude /model injection and codex settings
# push — apply a model to a RUNNING native session with NO router involved,
# before any routing gate exists to reach them through. Wave 0 writes this so
# the check exists before wave 1 finishes; no wave-1 stream owns it.
#
# It pins a hardcoded model onto a live claude pane (R2: tmux pane capture) and
# a live codex session (R3: config.toml + newest rollout turn_context), then
# asserts the process actually runs that model. This is a scaffold with the
# assertion points marked TODO(barrier-1): the lead fills the exact model ids and
# session-bring-up against the wave-1 apply layer once streams 5/6 land, using
# recipes R0/R2/R3 from designs/CUJ_STATUS.md §1. It is deliberately not wired to
# a live stack at wave-0 time — it documents and enforces the check's shape.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=/dev/null
[ -f "$HERE/dev-env.sh" ] && source "$HERE/dev-env.sh"

# Target models come from the environment so no model id is hardcoded here
# (repo no-hardcoded-models guard). The lead exports these at barrier-1 time
# from a task_v1 arm in omnigent/model_fallbacks.py, e.g.
#   BARRIER1_CLAUDE_MODEL=databricks-<claude arm>  (a TASK_V1_ARMS["claude"] id)
#   BARRIER1_CODEX_MODEL=databricks-<codex arm>    (a TASK_V1_ARMS["codex"] id)
CLAUDE_MODEL="${BARRIER1_CLAUDE_MODEL:-}"
CODEX_MODEL="${BARRIER1_CODEX_MODEL:-}"
[ -n "$CLAUDE_MODEL" ] || { echo "set BARRIER1_CLAUDE_MODEL (a claude task_v1 arm)"; exit 2; }
[ -n "$CODEX_MODEL" ] || { echo "set BARRIER1_CODEX_MODEL (a codex task_v1 arm)"; exit 2; }

echo "barrier-1 apply check — no router in the loop"
echo "  claude target: $CLAUDE_MODEL"
echo "  codex  target: $CODEX_MODEL"

fail() { echo "BARRIER-1 FAIL: $*" >&2; exit 1; }

# --- claude pane (R2) ---------------------------------------------------------
# TODO(barrier-1): with streams 5/6 landed, bring up a claude-native session
# (R0), PATCH model_override=$CLAUDE_MODEL, and assert via R2 (tmux capture of
# the runner's pane) that the status bar shows the target and the transcript
# holds exactly one /model injection. Until then this is a documented skip.
echo "  [skip] claude pane assertion — fill against wave-1 stream 5 (R2)"

# --- codex session (R3) -------------------------------------------------------
# TODO(barrier-1): bring up a codex-native session (R0), PATCH
# model_override=$CODEX_MODEL, and assert via R3 that config.toml and the newest
# rollout turn_context both name the target model.
echo "  [skip] codex session assertion — fill against wave-1 stream 6 (R3)"

echo "barrier-1 apply check: scaffold OK (assertions pending wave-1 apply layer)"
