# OpenClaw ↔ Omnigent Integration: Research & Onboarding Options

Status: research / proposal. Author: Pat (with research assist).
Date: 2026-07-24.

## Goal

Understand what OpenClaw is, how (or whether) it fits Omnigent's harness
model, and what it would take to help OpenClaw users onboard to Omnigent.

## TL;DR

- **OpenClaw is not a coding agent — it's a peer orchestrator / chat gateway**
(TypeScript/Node; multi-channel: Slack, WhatsApp, Telegram, Discord, voice,
plus terminal + web). It runs coding agents the *same way Omnigent does*:
as an **ACP (Agent Client Protocol) client**, via its `@openclaw/acpx`
plugin.
- **Do not model OpenClaw as a harness.** A harness wraps a coding *agent*
Omnigent drives. OpenClaw is an *ACP client only* — it can't be an ACP
server for Omnigent to drive, and two ACP clients can't drive each other.
- **"Omnigent drives OpenClaw" is a nightmare and low-value** — it stacks two
orchestrators over an agent pool Omnigent already reaches directly. Rejected
unless the explicit goal is to absorb OpenClaw's *distinctive* surface
(Slack/voice/canvas), not its coding agents. See
[Rejected: driving OpenClaw](#rejected-omnigent-drives-openclaw).
- **Recommended: two independent, low-cost onboarding paths** that read
OpenClaw's own files and require **no changes to OpenClaw**:
  - **Option A — Config bridge**: translate a user's OpenClaw acpx agent list
  into Omnigent's `acp:` config block. Their coding agents work in Omnigent
  day one. Effort: **days**.
  - **Option B — Chat import**: add `"openclaw"` as an import source so users
  migrate existing conversations. OpenClaw uses a **SQLite** session store
  (a first for our importers, which are all JSONL). Effort: **days once the
  DB table schema is grabbed** — the one remaining unknown.

## Background: what OpenClaw actually is

OpenClaw ([github.com/openclaw/openclaw](https://github.com/openclaw/openclaw))
bills itself as a *"personal AI assistant that learns and grows with you,
running on your own devices."* Architecturally it is a **local-first gateway**:

- A **multi-channel inbox** (WhatsApp, Telegram, Slack, Discord, Google Chat,
Signal, iMessage, IRC…).
- **Multi-agent routing** to isolated agents/workspaces.
- Voice (wake word) and a "Live Canvas" visual workspace.
- Written in **TypeScript/JavaScript** (Node, pnpm).

It runs coding agents through the **Agent Client Protocol (ACP)** — the
Zed-originated, JSON-RPC-2.0 editor↔agent protocol
([agentclientprotocol.com](https://agentclientprotocol.com/overview/introduction))
— using the `@openclaw/acpx` runtime plugin
([openclaw ACP docs](https://docs.openclaw.ai/tools/acp-agents-setup),
[acpx](https://github.com/openclaw/acpx)). Users type `/acp spawn codex`,
`/acp spawn claude`, etc. Supported targets: codex, claude, gemini, cursor,
copilot, qwen, opencode, and more.

### The critical structural fact

**OpenClaw is an ACP *client only*.** It spawns external agents
(`OpenClaw → agent`); it does not expose an ACP-server surface that another
host could drive.

**Omnigent is *also* an ACP client.** Its generic `acp` harness
(`omnigent/inner/acp_harness.py`, `omnigent/inner/acp_executor.py`) spawns any
ACP agent, and it even lends those agents Omnigent's builtin tools over an MCP
relay (`omnigent/inner/_acp_omnigent_mcp.py`). Data flows `Omnigent → agent`.

So the two products are **peers over a shared pool of coding agents**, reaching
the same agents the same way. Neither sits above the other:

```
        ┌─────────── OpenClaw (ACP client) ───────────┐
        │  Slack / WhatsApp / voice / canvas / CLI     │
        └───────────────────┬──────────────────────────┘
                            │ ACP (acpx)
                            ▼
              ┌──────────────────────────────┐
              │  codex · claude · gemini ·    │   ← the actual coding agents
              │  cursor · qwen · opencode     │      (each with its own login)
              └──────────────────────────────┘
                            ▲
                            │ ACP (acp: block)
        ┌───────────────────┴──────────────────────────┐
        │  Omnigent (ACP client) — web / CLI / sessions │
        └───────────────────────────────────────────────┘
```

## Why OpenClaw is not a harness

Omnigent has two harness tracks
([harness-integration-guide](../.claude/skills/harness-integration-guide/SKILL.md)):

- **SDK / subprocess** (`claude-sdk`, `codex`, `cursor`, `goose`/`qwen` over
ACP…) — Omnigent owns the model lifecycle.
- **Native TUI** (`claude-native`, `pi-native`, `kimi-native`…) — Omnigent
mirrors a vendor's own TUI.

Both require a *coding agent* at the far end with a driveable protocol surface.
OpenClaw is an orchestrator, not an agent, and exposes no ACP/MCP server. The
only thing Omnigent could "drive" is OpenClaw's outer CLI/gateway — which lands
us in the rejected option below.

## Rejected: "Omnigent drives OpenClaw"

To drive OpenClaw, Omnigent would wrap OpenClaw's **outer CLI/gateway**
(`openclaw message send`, `openclaw agent`, WebChat) as a native-harness-shaped
integration — spawn the gateway, send messages, scrape/mirror replies back.

```
Omnigent (orchestrator) ──drives CLI──► OpenClaw (orchestrator) ──ACP──► agents
```

Net effects:

- **Two orchestrators stacked.** Two policy/permission engines, two session
models, doubled latency and failure surface.
- **The middle layer is redundant.** Omnigent already reaches the bottom agents
directly (Option A). OpenClaw in the middle only earns its place if you
specifically want *OpenClaw's* channels/voice/canvas surfaced through
Omnigent.
- **Heavy, fragile build.** Full native-harness P0+P1 checklist (transport,
output forwarder, auth, elicitation mapping, interrupt, cost, tests) —
**weeks** — against an orchestrator's outer surface, not a stable protocol.
- **Governance gap.** OpenClaw's own tool calls run under *its* policy engine,
partly outside Omnigent's control.

**Verdict: rejected** for the coding-agent use case. Revisit only if the goal
is explicitly to absorb OpenClaw's multi-channel/voice/canvas surface.

## Recommended options

Both read OpenClaw's own on-disk files, embed in Omnigent, and require **no
changes to OpenClaw** and **no export format / `--format` flag**. They are
**independent** code paths — A is live agent access, B is historical
transcripts — and can ship in either order.

### Option A — Config bridge (live agent access)

OpenClaw registers ACP agents as name→command pairs; Omnigent's `acp:` config
block does the same (`omnigent/onboarding/acp_auth.py`):

```yaml
# ~/.omnigent/config.yaml
acp:
  agents:
    - {name: Codex,       command: <codex acp command>}
    - {name: Claude Code, command: npx -y @zed-industries/claude-code-acp}
```

**What to build:** a small translator that reads a user's acpx agent config and
writes the equivalent `acp:` entries, surfaced as an `omnigent setup` step
("Import coding agents from OpenClaw?"). Each `acp:` agent then appears in the
harness picker as `acp:<slug>` and runs through the existing generic `acp`
harness — with Omnigent's tools, policies, web UI, and orchestration layered
on.

**Net effects:**

- ✅ User's coding agents work in Omnigent **day one**.
- ✅ **~Zero new harness code** — reuses the generic `acp` harness.
- ✅ Each agent keeps its own auth; Omnigent stores no credential.
- ❌ Does not carry over OpenClaw chat history (that's B).
- ❌ Does not carry over OpenClaw's channels/voice/canvas (out of scope).

**Effort: days.**

**Format: confirmed.** acpx stores agents in `~/.acpx/config.json` as an
`agents` object mapping name → `{command, args}`; OpenClaw wraps the same shape
under `plugins.entries.acpx.config.agents` in `~/.openclaw/openclaw.json`. The
translator input is known — no de-risk needed before building.

### Option B — Chat import (bring your history)

Omnigent already imports transcripts from claude/codex/kimi/kiro/pi/qwen. The
pattern is established; adding a source is a **new reader in an existing
dispatcher**, not a standalone tool:

1. `omnigent/session_import/models.py:12` — add `"openclaw"` to the
 `ImportSource` literal.
2. `omnigent/session_import/local.py` — add `load_openclaw_session(session_id)`
 plus an `if source == "openclaw"` branch in both dispatchers
 (`load_local_session`, `list_recent_local_session_ids`). The reader reads
 OpenClaw's transcript and normalizes to `NewConversationItem[]`.
3. **CLI needs the source registered, but no new command.** Add `"openclaw"`
 to the `--harness` `click.Choice` list *and* the `ImportSource` literal
 (`cli.py`, `import_session_command`); then `omnigent import --harness openclaw
 --session <id>` (and `--last N`) works. No new subcommand.

   **Naming caveat (raised in review):** the `--harness` flag is inconsistent
 with this doc's thesis that OpenClaw is *not* a harness — and OpenClaw is in
 fact the first import source that isn't a coding harness (the existing six all
 are). The flag really means "the local source that owns the transcript." To
 avoid telling users to pass `--harness openclaw`, add a `--source` alias
 (keeping `--harness` as a deprecated alias for back-compat) and document the
 import surface as `--source`. This is the recommended resolution; see the
 onboarding design doc's execution plan for where it lands.

Imported sessions are stored as ordinary Omnigent sessions, tagged with
`omnigent.import.source` and `omnigent.import.external_session_id` provenance
labels (`models.py`), so a source session is only imported once.

**Net effects:**

- ✅ Users migrate existing OpenClaw conversations into Omnigent.
- ✅ Slots into the existing import CLI + provenance model.
- ⚠️ Fidelity depends on OpenClaw's format — like Qwen/Kiro/Kimi, may preserve
visible messages but not native tool activity.

**Effort: days once the table schema is known.**

**Format: mostly confirmed, one gap.** OpenClaw moved to **SQLite** — sessions
live in a per-agent DB at `~/.openclaw/agents/<agentId>/agent/
openclaw-agent.sqlite` (older installs used legacy `sessions.json`/JSONL). Path
and format are known; the **table/column schema inside that DB** is not, and it
gates the reader. That's a one-command `sqlite3 .schema` grab from a real
install — the only remaining unknown. Note B would be the **first
SQLite-backed importer** (existing readers are all JSONL), so it queries a DB
rather than parsing a file.

## Comparison


|                         | A: Config bridge               | B: Chat import                    | Rejected: drive OpenClaw |
| ----------------------- | ------------------------------ | --------------------------------- | ------------------------ |
| Delivers                | Live coding-agent access       | Historical transcripts            | OpenClaw's full surface  |
| New code                | Config translator + setup step | One reader in existing dispatcher | Full native harness      |
| Touches OpenClaw?       | No (reads its config)          | No (reads its transcripts)        | No (drives its CLI)      |
| `--format` flag needed? | No                             | No                                | No                       |
| Effort                  | Days (format confirmed)        | Days (after DB schema grab)       | Weeks                    |
| Policy control          | Full (Omnigent)                | Full (Omnigent)                   | Split across two engines |
| Fragility               | Low (stable ACP)               | Low (file read)                   | High (scraping)          |


## Open questions / next steps

1. **B's session-DB table schema (the one gating unknown):** path/format are
 known (`~/.openclaw/agents/<id>/agent/openclaw-agent.sqlite`); the row shape
 is not. A `sqlite3 .schema` dump from a real install unblocks B.
2. **Which value first?** A (get agents working) is the higher-leverage,
 lower-risk start and its format is already confirmed, so it can begin now; B
 (bring history) follows once the schema grab lands.

## Compliance note (not a blocker for OSS)

A Databricks managed-device compliance check flags the
**Clawdbot/Moltbot/OpenClaw** family as **prohibited**. That policy governs
**Databricks-managed devices** — it does **not** apply to OSS Omnigent users
running OpenClaw on their own machines, who are the audience for this feature.
Its only practical effect: OpenClaw can't be installed on our own
execution/CI environment, so live fixtures (e.g. B's `.sqlite` schema) must
come from an OSS contributor's unmanaged machine.

## Sources

- OpenClaw repo — [https://github.com/openclaw/openclaw](https://github.com/openclaw/openclaw)
- OpenClaw ACP agents setup — [https://docs.openclaw.ai/tools/acp-agents-setup](https://docs.openclaw.ai/tools/acp-agents-setup)
- acpx (ACP client CLI) — [https://github.com/openclaw/acpx](https://github.com/openclaw/acpx)
- Agent Client Protocol spec — [https://agentclientprotocol.com/overview/introduction](https://agentclientprotocol.com/overview/introduction)
- Zed ACP ("bring your own agent") — [https://zed.dev/acp](https://zed.dev/acp)

