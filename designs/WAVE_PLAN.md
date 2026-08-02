# Wave-0 execution manifest — file partition + scope corrections

Lead-authored, wave 0. This is the operational companion to
`designs/PR_REWRITE_PLAN.md` (§4b streams, §4f partition) and
`designs/ROUTING_OVERVIEW.md` (subsystem → files). Every wave-1/2/3 agent reads
this file FIRST, then its stream's rows in `CUJ_STATUS.md` §2, then the trap list
(`CUJ_IMPLEMENTATION.md` + `INTELLIGENT_ROUTING_PLAN.md` §12).

The build branch is `routing-mvp-v2`, off `origin/main` (`042f0ddc`). The
reference implementation is `routing-mvp-v1` (`f200a8bd`), checked out at
`~/worktrees/omnigent-routing-v1` as the read-only oracle. Read it; never copy it
wholesale (plan 0d).

## Scope correction from wave-0 recon (read this before you size your stream)

`origin/main` has drifted forward since the plan's line counts were written. It
ALREADY ships more of the routing core than plan 2g credited. Verify against main
before you write — extend, do not re-create:

**Already on main (extend it, do not rebuild):**
- `omnigent/server/smart_routing.py` (850 lines): `RoutingResult(model, rationale,
  harness)`, the `RoutingClient` protocol, `ExternalRoutingClient`,
  `LLMRoutingClient`, `route_session_harness`, `route_turn`,
  `fetch_runner_models` / `_fetch_runner_catalog`, `_redirect_incompatible_pick`,
  `_AUTO_ROUTING_HARNESSES`, `_WORKER_NAME_TO_HARNESS`.
- `omnigent/cli.py` (~3586): builds one routing client at startup onto
  `RuntimeCaps.routing_client` (`_build_external_routing_client` /
  `_build_local_llm_routing_client`).
- `omnigent/entities/conversation.py`: `RoutingDecisionData(model, applied,
  rationale, agent)` — wave-0 already appended the 5 new fields.
- `omnigent/server/schemas.py`: `SessionCreateRequest` already had
  `model_override`, `cost_control_mode_override`, `harness_override` — wave-0
  added `smart_routing_message` + `subagent_routing_override`.
- `omnigent/entities/conversation.py` `Conversation`: `model_override`,
  `cost_control_mode_override`, `harness_override` columns already exist.
- The **native apply MECHANISM** already exists (this is the big one — plan
  streams 5/6 shrink accordingly):
  - `omnigent/inner/claude_native_executor.py` already types `/model <model>`
    into the pane (`_should_switch_model` → `inject_slash_command`).
  - `omnigent/inner/codex_native_executor.py` already pushes
    `thread/settings/update` with the model to the running thread.
  - `omnigent/server/routes/sessions/routes_core.py:1737` already fires a
    `model_change` event when `model_override` is PATCHed mid-session.
  - The two orchestration routing gates already call `route_session_harness`
    (auto-harness) and `route_turn` (per-turn) inline.

**ABSENT on main (build it):**
- `omnigent/claude_model_vocabulary.py` — the alias/custom-slot translation
  (`claude_model_command_arg`). Main types the RAW catalog id, which `/model`
  silently ignores; the vocabulary is the correctness layer. **v1-only.**
- `read_model_env` (launch-pin read) in the claude bridge, and
  `write_codex_config_model` (mirror the accepted codex switch into
  `config.toml` so it doesn't revert next turn). **v1-only.**
- `omnigent/gateway_inference.py` — wave-0 stubbed it; wave-1 s4 fills the checks.
- `omnigent/runner/subagent_routing.py` and everything subagent — **absent.**
- `omnigent/smart_routing_cli.py` — **absent.**
- `RoutingSettings` as a dataclass — main parses the `routing:` YAML dict inline
  in cli.py; if a stream wants a typed settings object it creates one, but note
  main has no such class today.

## Wave-0 contract surfaces (import these; never wait on an implementation)

- `omnigent/server/routing_contract.py` — the single contract module. Backend
  predicate type + `RoutingBackend` protocol (2f), frozen arms + `FAMILY_FALLBACK`
  + `SERVABLE_ALIASES` + `ResolvedRoute` + `resolve_route()` stub (3i), the
  decision-record added-field set, `SubagentRouteDecision` + loopback paths (2c),
  gateway harness-spelling groups (3f), and the create/response added-field sets.
- `omnigent/runtime/caps.py` — `RuntimeCaps.routing_backend_predicate` (2f).
- `omnigent/gateway_inference.py` — `gateway_inference_map()` + two family checks.
- `omnigent/server/routing_turn_gate.py` — `route_turn_for_session()` (wave-2 s1).
- `omnigent/server/routing_create.py` — `resolve_smart_routing_create()` +
  `resolve_fixed_native_model_routing()` (wave-2 s2).
- Migration `66b439064d06_add_gateway_inference_to_hosts.py` — empty, chains off
  the true head `c4d5e6f7a8b9`. Wave-1 s4 fills `upgrade()`/`downgrade()`; do NOT
  create a second migration.

## File partition (plan 4f) — you own your row, you READ everything else

An agent stages ONLY the files it owns (`git add <paths>`, never `git add -A`). An
agent that hits a failure in a file it does not own reports to the lead and does
not fix it (plan 4a). `omnigent/cli.py` is **lead-owned** for the whole build:
send the lead your cli.py delta and the lead applies it.

### Wave 1 — foundations (7 streams)

| Stream | Owns (creates/edits) | Notes |
| --- | --- | --- |
| W1·1 routing core + seam | `omnigent/server/smart_routing.py` | Extend main's. Fill `resolve_route()` in routing_contract. NO cost ladder / MODEL_LISTS / allowlist (3i r1). Keep `_redirect_incompatible_pick`. |
| W1·2 backend selection | `omnigent/server/routing_backend.py` (new) | The two-backend dispatch around `RuntimeCaps.routing_backend_predicate`. Wraps main's two clients. Satisfies `RoutingClient`. Lead wires it into cli.py. |
| W1·3 decision persistence | decision writer/reader; `session_overrides` keys | Fields already on `RoutingDecisionData` (wave-0). `subagent_routing_override` key belongs to W2·4; its Inherit row to W2·5. |
| W1·4 gateway signal | `omnigent/gateway_inference.py`, host frames, hosts route, migration `66b439064d06` body | Fill the two family checks + persist the column. Web half is W2·5. |
| W1·5 claude apply | `omnigent/claude_model_vocabulary.py` (new), `omnigent/claude_native.py`, `omnigent/claude_native_bridge.py`, `omnigent/inner/claude_native_executor.py`, `omnigent/inner/claude_sdk_executor.py` | Main already injects `/model`; ADD the vocabulary translation + launch-pin read. The claude hook script is W2·3. |
| W1·6 codex model apply | `omnigent/codex_native_forwarder.py`, `omnigent/codex_native_app_server.py`, `omnigent/codex_native_bridge.py`, `omnigent/inner/codex_native_executor.py` | Main already pushes `thread/settings/update`; ADD `write_codex_config_model` mirror + the glm gateway route. |
| W1·7 codex hooks + trust | `omnigent/inner/codex_executor.py` | `hooks.json` generation + trust handshake + `python -I`. Different files from W1·6 — that's why codex splits in two. |

### Wave 2 — integration (6 streams)

| Stream | Owns | Notes |
| --- | --- | --- |
| W2·1 turn gate | `omnigent/server/routing_turn_gate.py`; the turn call site in orchestration | Fill `route_turn_for_session()`. Session-start cadence: a turn with a `model_override` does not re-route. |
| W2·2 create paths | `omnigent/server/routing_create.py`; the create call site in orchestration | Fill both resolvers. Keep `_routing_host_for_create` authorize-before-lookup (plan 5b / §4.3d trap). |
| W2·3 subagent transport | `omnigent/server/subagent_routing_transport.py` (new), `omnigent/inner/hook_scripts/**` | Loopback endpoint + env plumbing + hook subprocess + the claude hook script. |
| W2·4 subagent policy | `omnigent/server/subagent_routing_policy.py` (new; runner-side logic), `omnigent/server/routes/sessions/routes_hooks.py` (thin relay) | `resolve_subagent_route` → `SubagentRouteDecision` (contract). Family constraints. `subagent_routing_override` key. |
| W2·5 web | `web/src/**` | Dialog, harness row, gating consumption, decision card, and the in-session model-indicator fix (plan 2e). |
| W2·6 CLI | `omnigent/smart_routing_cli.py` (new), `omnigent/cli_native.py` | `--smart-routing`/`-p` flags, preflight, both dispatch tiers. Consumes the wave-0 HTTP contract, not server code. |

### Wave 3 — closure (3 streams + lead)

| Stream | Owns |
| --- | --- |
| W3·1 session-create tool | expose `sys_session_create` to the Smart Routing harness agents (plan 3c) |
| W3·2 coverage sweep | test files only, any area — cover the `CUJ_STATUS.md` §2 inventory |
| W3·3 verification (barrier-3) | the standing verification agent's barrier-3 runs (plan 6e) |

Lead holds: barrier-2 fallout, `cli.py`, the docs-deletion commit (plan 3a), the
PR body, and the full gate (plan 6a).

## Barriers (plan 4e) — the lead holds each; a wave does not start until it passes

- **Barrier 1** (after wave 1): every stream's unit tests pass in ONE run; the
  contract file is unchanged (a stream needing a new signature tells the lead,
  who re-declares it for everyone); `scripts/barrier1_apply_check.sh` proves the
  apply layers work with no router (R2 + R3).
- **Barrier 2** (after wave 2): `CUJ_STATUS.md` §2 runs 1–3 (matrix + spawn/toggle
  + CLI rows), the first end-to-end proof.
- **Barrier 3** (after wave 3): §2 run 4 + the full gate.
