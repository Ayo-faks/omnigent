#!/usr/bin/env bash
# One-shot verification for create-time Smart Routing.
#
# Proves the PR's contract end-to-end: Smart Routing is decided ONCE at session
# create (never per-turn), for every routable harness — the native TUIs
# (claude-native / codex-native), the in-process claude-sdk agents (polly /
# debby), and the cross-family "auto" harness. A session created WITHOUT a
# routing prompt is not routed.
#
# What it checks
#   1. Unit suites (routing_create, resolve_route, the model_override /
#      child-session integration tests) pass.
#   2. Against a running local stack: a create carrying a smart_routing_message
#      pins a SERVABLE model (databricks-<arm> / system.ai.glm-5-2), for each
#      harness — proving create-time routing + servable-spelling resolution.
#   3. A create WITHOUT a smart_routing_message is NOT routed (model_override
#      stays null) — proving there is no per-turn routing fallback.
#
# Usage
#   scripts/verify_smart_routing.sh                 # unit tests + live checks
#   SKIP_LIVE=1 scripts/verify_smart_routing.sh     # unit tests only
#   ROUTING_SERVER=http://localhost:6868 scripts/verify_smart_routing.sh
#
# Requires: a stack brought up per LOCAL_SETUP.md (server + host, routing
# profile + databricks-sdk configured). See LOCAL_SETUP.md if the live checks
# report the host is not gateway-backed or the router 401s.

set -uo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

SERVER="${ROUTING_SERVER:-http://localhost:6868}"
pass=0
fail=0
ok()   { echo "PASS  $*"; pass=$((pass + 1)); }
bad()  { echo "FAIL  $*" >&2; fail=$((fail + 1)); }
note() { echo "      $*"; }

echo "== 1. Unit suites =="
if uv run --no-sync python -m pytest -q \
    tests/server/test_routing_create.py \
    tests/server/test_resolve_route.py \
    tests/server/integration/test_sessions_model_override.py \
    >/tmp/verify_smart_routing_pytest.log 2>&1; then
  ok "unit suites (routing_create + resolve_route + model_override/child)"
else
  bad "unit suites — see /tmp/verify_smart_routing_pytest.log"
  tail -20 /tmp/verify_smart_routing_pytest.log >&2
fi

if [ "${SKIP_LIVE:-}" = "1" ]; then
  echo ""
  echo "== live checks skipped (SKIP_LIVE=1) =="
  echo "== $pass passed, $fail failed =="
  [ "$fail" -eq 0 ] || exit 1
  exit 0
fi

echo ""
echo "== 2. Live create-time routing ($SERVER) =="

if ! curl -sf "$SERVER/v1/hosts" >/dev/null 2>&1; then
  bad "server not reachable at $SERVER — bring the stack up (LOCAL_SETUP.md) or set SKIP_LIVE=1"
  echo "== $pass passed, $fail failed =="
  exit 1
fi

# Discover a routable agent id per harness from the live catalog (no hardcoded
# ids): the native UI wrappers and any claude-sdk brain agent (polly / debby).
_agent_for() {  # $1 = harness
  curl -s "$SERVER/v1/agents" 2>/dev/null | python3 -c "
import sys, json
want = sys.argv[1]
for a in json.load(sys.stdin).get('data', []):
    if a.get('harness') == want:
        print(a['id']); break
" "$1"
}

CODEX_AGENT="$(_agent_for codex-native)"
CLAUDE_AGENT="$(_agent_for claude-native)"
SDK_AGENT="$(_agent_for claude-sdk)"

# Create a session and echo its persisted (harness, model_override).
_create() {  # $1=agent_id $2=harness_override $3=smart_routing_message(optional)
  local agent="$1" harness="$2" msg="${3:-}"
  python3 - "$SERVER" "$agent" "$harness" "$msg" <<'PY'
import sys, json, urllib.request
server, agent, harness, msg = sys.argv[1:5]
body = {"agent_id": agent, "harness_override": harness, "cost_control_mode_override": "on"}
if msg:
    body["smart_routing_message"] = msg
req = urllib.request.Request(
    f"{server}/v1/sessions", data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"}, method="POST",
)
try:
    d = json.load(urllib.request.urlopen(req, timeout=30))
except Exception as e:  # noqa: BLE001
    print(f"ERR {e}"); sys.exit(0)
print(f"{d.get('harness')}\t{d.get('model_override')}")
PY
}

# A servable model id is prefixed (databricks-*) or the glm system.ai. spelling.
_is_servable() { case "$1" in databricks-*|system.ai.*) return 0;; *) return 1;; esac; }

P_BUGFIX='Fix the trailing-whitespace trimming bug in the config loader and add a regression test.'
P_TRIVIAL='What testing framework does this project use?'

# --- codex-native: create with prompt routes a servable codex arm ---
if [ -n "$CODEX_AGENT" ]; then
  IFS=$'\t' read -r h m <<<"$(_create "$CODEX_AGENT" codex-native "$P_TRIVIAL")"
  if [ "$h" = "codex-native" ] && _is_servable "$m"; then
    ok "codex-native create routed a servable model ($m)"
  else
    bad "codex-native create: harness=$h model=$m (want codex-native + servable)"
  fi
else
  note "skip codex-native — no codex-native agent registered"
fi

# --- claude-native: create with prompt routes a servable claude arm ---
if [ -n "$CLAUDE_AGENT" ]; then
  IFS=$'\t' read -r h m <<<"$(_create "$CLAUDE_AGENT" claude-native "$P_BUGFIX")"
  if [ "$h" = "claude-native" ] && _is_servable "$m"; then
    ok "claude-native create routed a servable model ($m)"
  else
    bad "claude-native create: harness=$h model=$m (want claude-native + servable)"
  fi
else
  note "skip claude-native — no claude-native agent registered"
fi

# --- claude-sdk (polly/debby): create with prompt routes a servable claude arm ---
if [ -n "$SDK_AGENT" ]; then
  IFS=$'\t' read -r h m <<<"$(_create "$SDK_AGENT" claude-sdk "$P_BUGFIX")"
  if [ "$h" = "claude-sdk" ] && _is_servable "$m"; then
    ok "claude-sdk create routed a servable model ($m)"
  else
    bad "claude-sdk create: harness=$h model=$m (want claude-sdk + servable)"
  fi

  # --- auto: cross-family, picks harness + servable model ---
  IFS=$'\t' read -r h m <<<"$(_create "$SDK_AGENT" auto "$P_TRIVIAL")"
  if { [ "$h" = "claude-native" ] || [ "$h" = "codex-native" ]; } && _is_servable "$m"; then
    ok "auto create routed harness+servable model ($h / $m)"
  else
    bad "auto create: harness=$h model=$m (want a native harness + servable)"
  fi

  # --- NO prompt: not routed (proves per-turn routing is gone) ---
  IFS=$'\t' read -r h m <<<"$(_create "$SDK_AGENT" claude-sdk "")"
  if [ "$m" = "None" ] || [ -z "$m" ]; then
    ok "create WITHOUT a routing prompt is not routed (model_override=$m)"
  else
    bad "create without prompt got model_override=$m (expected none — no per-turn routing)"
  fi
else
  note "skip claude-sdk / auto — no claude-sdk agent registered"
fi

echo ""
echo "== $pass passed, $fail failed =="
[ "$fail" -eq 0 ] || exit 1
