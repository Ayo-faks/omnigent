# OpenClaw Onboarding — Implementation Design

Status: design / proposal. Author: Pat (with research assist).
Date: 2026-07-24.

Companion to [openclaw-integration-research.md](./openclaw-integration-research.md),
which establishes *why* OpenClaw is a peer ACP orchestrator (not a harness) and
*why* the "Omnigent drives OpenClaw" path is rejected. This doc specifies *how*
to build the two recommended onboarding paths.

## Overview

Help OpenClaw users adopt Omnigent via two independent, file-reading adapters
that require **no changes to OpenClaw** and **no export format**:

- **Option A — Config bridge**: translate a user's OpenClaw acpx agent list
  into Omnigent's `acp:` config block so their coding agents run in Omnigent.
- **Option B — Chat import**: add `"openclaw"` as an import source so users
  migrate existing conversations.

The two are separate code paths (A = live agent access; B = historical
transcripts) and can ship in either order. A is the higher-leverage, lower-risk
first step.

## Goals

- OpenClaw users reach their coding agents inside Omnigent with minimal setup.
- Reuse existing Omnigent machinery (generic `acp` harness, `session_import`
  framework, `omnigent import` / `omnigent setup` CLI surfaces).
- Zero credential storage: agents keep their own auth.
- No new user-facing CLI surface unless it already exists.

## Non-goals

- Driving OpenClaw as a sub-orchestrator (rejected — see research doc).
- Carrying over OpenClaw's multi-channel inbox / voice / Live Canvas.
- Any modification to OpenClaw or upstream contribution to acpx.
- A generic "export format" or `--format=openclaw` flag.

## Prerequisites (blocking unknowns)

Both options depend on file formats we have **not yet confirmed** — OpenClaw is
on the managed-device blocklist, so this is web/repo research plus a test
fixture from a user's machine, not local runtime inspection:

| Unblocks | Unknown | Needed for |
|---|---|---|
| A | acpx agent-config file: path + schema (where the registered-agent name→command list lives) | Config translator input |
| B | OpenClaw session store: on-disk path + format (JSONL? SQLite? Node data dir?) + record schema | Transcript reader |

Everything below is contingent on resolving the relevant row. The Omnigent-side
plumbing is small and well-understood; the format reverse-engineering is the
real cost.

## Option A — Config bridge

### Data flow

```
OpenClaw acpx config            translator            ~/.omnigent/config.yaml
  (agent name→command list)  ──────────────►   acp:
                                                  agents:
                                                    - {name: Codex,  command: …}
                                                    - {name: Claude, command: npx … claude-code-acp}

Omnigent session → harness "acp:<slug>" → generic acp harness → agent
```

Omnigent's `acp:` block and OpenClaw's acpx registry are the same shape: named
commands, each agent owning its own auth. The bridge is a translation, not a
protocol.

### Components

1. **Reader** — locate + parse the acpx agent config (schema TBD, per
   Prerequisites). Emit a normalized `list[(name, command, model?)]`.
2. **Translator** — map each entry to an `AcpAgentEntry`
   (`omnigent/onboarding/acp_auth.py`) and persist via the existing
   `acp_agents_settings()` + `_save_global_config()` path. `acp_auth.py`
   already owns slug derivation, dedup, and the settings-dict builder — reuse
   verbatim.
3. **Setup step** — an `omnigent setup` entry ("Import coding agents from
   OpenClaw?") that runs the reader, previews the discovered agents, and writes
   the `acp:` block on confirm.

### Touch points

| File | Change |
|---|---|
| `omnigent/onboarding/openclaw_config.py` (new) | Reader + translator to `AcpAgentEntry` list |
| `omnigent/onboarding/acp_auth.py` | None — reuse `AcpAgentEntry`, `acp_agents_settings`, `slugify` |
| `omnigent/onboarding/harness_install.py` (or setup flow) | Add the "import from OpenClaw" step |
| `omnigent/cli.py` | Wire the step into `omnigent setup` (no new top-level command) |

### Behavior notes

- Idempotent: re-running merges without duplicating (slug dedup already exists).
- Soft validation only: `command_binary_on_path()` flags a missing binary as a
  hint, never a hard gate — the agent owns its own install.
- No secrets written (the `acp:` block deliberately carries no credential ref).

## Option B — Chat import

### Data flow

```
OpenClaw session store        load_openclaw_session()        POST /v1/import
  (transcript, format TBD)  ────────────────────────►   NewConversationItem[]  ──►  new Omnigent session
                                                                                     + provenance labels
```

### Touch points (mirrors the Qwen/Kiro/Pi/Kimi precedent)

| File | Change |
|---|---|
| `omnigent/session_import/models.py:12` | Add `"openclaw"` to the `ImportSource` `Literal` |
| `omnigent/session_import/local.py` | Add `load_openclaw_session(session_id) -> LocalSessionImport`; add `if source == "openclaw"` to `load_local_session` (line ~943) and `list_recent_local_session_ids` (line ~110); export in `__all__` |
| `omnigent/cli.py` | Add `"openclaw"` to the `--harness` `click.Choice` list (hardcoded, separate from `ImportSource`); no new subcommand. See naming note below — recommend adding a `--source` alias since OpenClaw is not a harness. |

#### Naming: `--harness` vs. `--source`

The `import` command selects the source with `--harness`, but OpenClaw is **not
a harness** (that's the core finding of the research doc), and it is the first
import source that isn't a coding harness — the existing six (claude, codex,
kimi, kiro, pi, qwen) all are. The flag really means "the local source that owns
the transcript." Rather than instruct users to type `--harness openclaw`, add a
`--source` alias to the command and treat `--harness` as a deprecated alias for
back-compat (target removal a release later, per the deprecation convention).
Under the hood both map to the same `ImportSource`. This keeps the surface
honest without breaking existing `--harness` usage.

### Reader contract

Mirror `load_pi_session` (`local.py:839`) as the reference implementation:

- Resolve the store root from an env override (e.g. `OPENCLAW_HOME`) falling
  back to the default data dir.
- Locate the transcript for `session_id`; raise `SessionImportNotFoundError`
  when missing/ambiguous/empty.
- Parse records; select the active branch if the store is branched.
- Normalize each record to `NewConversationItem` (`omnigent/entities/
  conversation.py:668`) with `MessageData` for messages.
- Return `LocalSessionImport(source="openclaw", external_session_id=…,
  workspace=…, items=…)`.

`list_recent_local_session_ids("openclaw", limit=…)` returns recent parent
session ids for the `--last N` batch path, using the same
`_recent_unique_session_ids` helper.

### Provenance & idempotency

The server tags imported sessions with `omnigent.import.source` and
`omnigent.import.external_session_id` (`models.py`), so a source session is
imported only once — the CLI already reports "Already imported …; skipped."
No new work here.

### Fidelity

Like Qwen/Kiro/Kimi, expect to preserve **visible messages** first; native
tool-call activity is best-effort and depends on whether OpenClaw's store
records it. Document the fidelity level in the CLI help string alongside the
existing note.

## Testing

- **A:** unit-test the reader against captured acpx-config fixtures (valid,
  malformed, empty); test the translator produces the expected
  `acp_agents_settings` dict; test idempotent re-run. Manual: run `omnigent
  setup`, confirm agents appear in the harness picker as `acp:<slug>` and a
  turn dispatches.
- **B:** unit-test `load_openclaw_session` against captured transcript fixtures
  (mirror `tests/**` for pi/qwen import); test not-found/ambiguous/empty raise
  `SessionImportNotFoundError`; test `list_recent_local_session_ids` ordering.
  Manual: `omnigent import --harness openclaw --session <id>` and verify the
  session renders with provenance labels.
- No real OpenClaw runtime required (blocklisted) — all tests run off captured
  file fixtures.

## Execution plan

The solution above answers *what* we're building. This section answers *how we
ship it* — the milestones, the order, why that order, and rough ETAs. All
estimates assume one engineer part-time and are calendar-rough, not commitments;
they exist to expose sequencing and dependencies early, not to be precise.

### What gates everything (do this before writing code)

Two format unknowns (see Prerequisites) block the readers, and one policy
question blocks *shipping*. These are cheap to resolve and expensive to
discover mid-build, so they come first:

- **M0 — De-risk (≈2–3 days).** Resolve the acpx config schema (unblocks A) and
  the OpenClaw session-store format (unblocks B) from a captured fixture on a
  sanctioned machine; get a yes/no on the compliance question. **Exit criterion:
  one real acpx config file and one real transcript file checked in as test
  fixtures, plus a written compliance answer.** If compliance says no, we stop
  here — that's the point of doing it first.

### Milestones and sequencing

```
M0 de-risk ──┬──► M1 config bridge (A) ──► M2 chat import (B)
             │         │                        │
             └─ compliance ─┘ (both gated on M0's yes/no)
```

A and B are independent code paths, but we sequence them A→B deliberately (see
prioritization). Each milestone is independently shippable and independently
useful.

| Milestone | Scope | Depends on | Rough ETA |
|---|---|---|---|
| **M0 — De-risk** | Confirm both formats; compliance yes/no; check in fixtures | — | 2–3 days |
| **M1 — Config bridge (Option A)** | Reader + translator to `AcpAgentEntry`; `omnigent setup` step; unit tests off fixtures | M0 (acpx schema + compliance) | 3–4 days |
| **M2 — Chat import (Option B)** | `"openclaw"` in `ImportSource` + `--harness` `Choice`; `--source` alias (`--harness` deprecated); `load_openclaw_session`; dispatcher branches; unit tests off fixtures | M0 (session format + compliance) | 3–5 days |

### Prioritization — and *why*

To avoid the P0/P1 ambiguity Cathy flagged, the ordering below is **sequencing
by value-and-risk, not a statement of importance.** "M1 before M2" means *build
M1 first*, not *M2 doesn't matter*. Rationale:

1. **M0 first — dependency + kill-switch.** Everything downstream reads file
   formats we haven't confirmed, and the whole effort is void if compliance says
   no. Front-loading the cheapest work that can invalidate the project is the
   highest-leverage sequencing decision here.
2. **M1 (Option A) before M2 (Option B) — higher customer impact per unit
   effort, lower risk.** A gets a user's coding agents *working* in Omnigent —
   the core adoption CUJ ("I can do my work here") — and reuses the existing
   `acp` harness, so its risk is low and its blast radius is one setup step. B
   brings *history*, which improves migration but isn't required to be
   productive, and carries more fidelity risk (depends on how much OpenClaw's
   store records). So A is both more impactful and safer → it goes first.
3. **Within each milestone, "reader before wiring."** The reader (parsing
   OpenClaw's files) is the only novel, risk-bearing code; the wiring
   (translator / dispatcher branch / CLI) is precedented and mechanical. Land
   and test the reader against fixtures first so the risky part is proven before
   the plumbing.

### Definition of done (per milestone)

- **M1:** `omnigent setup` discovers a user's OpenClaw agents, previews them,
  and writes the `acp:` block on confirm; each appears in the harness picker as
  `acp:<slug>` and dispatches a turn. Unit tests cover valid/malformed/empty
  config and idempotent re-run.
- **M2:** `omnigent import --harness openclaw --session <id>` (and `--last N`)
  produces an Omnigent session with provenance labels; re-import is skipped.
  Unit tests cover the reader and not-found/ambiguous/empty paths.

### Rollout mechanics

- Independent, additive changes — no flag strictly required, but gating the
  M1 setup step behind an existing onboarding flag is fine for a staged rollout.
- Ship M1, then M2, each behind its own PR so value lands incrementally.
- **Compliance sign-off from M0 is a hard gate** — neither milestone merges
  until it's a documented yes.

## Open questions

1. acpx agent-config path + schema (blocks A).
2. OpenClaw session-store path + format + record schema (blocks B).
3. Env-var override name for the OpenClaw data dir (propose `OPENCLAW_HOME`,
   matching the `PI_CODING_AGENT_DIR` / `QWEN_HOME` precedent).
4. Compliance: OpenClaw is on a Databricks managed-device blocklist. Confirm
   with the check's owner that reading a user's local OpenClaw data for
   migration is sanctioned before implementation.

## Sources

See [openclaw-integration-research.md](./openclaw-integration-research.md#sources).
