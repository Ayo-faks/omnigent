# Task: produce a high-quality explainer video for the DPIA Desk features and demo usage

You are working in the worktree `/home/ayoola/qm/.copilot-azure/worktrees/dpia-investigation-demo`
(branch `feature/dpia-investigation-demo`). Do not commit, push, or open PRs. Do not modify app
source code — this task only captures footage and builds a video.

Deliverable: a ~2:30 landscape explainer video, `docs/video/dpia-explainer.mp4`, 1920x1080 @ 30fps,
with TTS voiceover, a subtle music bed ducked under the narration, on-screen lower-thirds, and real
captured app footage. Build it with **HyperFrames**: start from the `/hyperframes` entry skill and
route to `/product-launch-video` (this is a product demo/showcase of an app from a site-specific
brief; everything the intent interview needs is in this document — do not re-interview me).
Scaffold the HyperFrames project under `docs/video/project/` inside the worktree.

## What the product is (for narration accuracy)

DPIA Desk is a privacy-operations workspace for Data Protection Impact Assessments, built on the
Omnigent agent platform. Terminology source of truth: `docs/dpia-feature-guide.md` (read it before
writing narration). Core surfaces:

- **DPIA desk portfolio** (`/dpia`): all assessments, "Request a DPIA" entry, Incoming requests.
- **Case cockpit** (`/dpia/cases/student-success-alert`): header + decision-readiness banner and
  six tabs — Overview, Processing map, Evidence & questions, Screening, Full DPIA, Audit — plus a
  live agent-activity panel, stakeholder outreach panel, and a case agent chat dock.
- **Stakeholder workflow** (the demo story): a requester (Priya Shah, Procurement) files a guided
  DPIA request; the privacy officer (Alex Morgan) triages it from the Inbox, accepts it for
  screening, shares scoped questions with a stakeholder (IT Security); the contributor answers in a
  scoped respond view; the officer accepts the staged answers as recorded evidence, issues an
  outcome (approved with conditions), and the requester acknowledges it.
- Everything is **synthetic data only** — the closing card must say so.

## Step 0 — stack + footage capture (Playwright, before any HyperFrames work)

The footage capture script already exists: `scripts/dpia_video_capture.py`. It records 10 clips to
`docs/video/footage/*.webm` plus `docs/video/footage/manifest.json` describing each clip and its
suggested use. If `docs/video/footage/` already contains the 10 clips and manifest, skip capture.

1. Verify the stack (the script also preflights and fails fast):
   - Web: `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5178` → 200
   - API: `curl -s http://127.0.0.1:6777/v1/hosts` → JSON
2. If down, start from the worktree root (`cd /home/ayoola/qm/.copilot-azure/worktrees/dpia-investigation-demo`):
   - API (must run **unsandboxed** so the spawned codex reads `~/.codex/auth.json`; run as a
     background/async process):
     `uv run --no-sync omnigent server --host 127.0.0.1 --port 6777 --database-uri sqlite:////home/ayoola/qm/.copilot-azure/worktrees/dpia-investigation-demo/web/node_modules/dpia-demo.db --artifact-location /home/ayoola/qm/.copilot-azure/worktrees/dpia-investigation-demo/web/node_modules/dpia-demo-artifacts --agent examples/dpia_investigation --no-open`
   - Web (background/async): `cd web && OMNIGENT_URL=http://127.0.0.1:6777 pnpm exec vite --host 127.0.0.1 --port 5178`
3. Run capture: `cd /home/ayoola/qm/.copilot-azure/worktrees/dpia-investigation-demo && uv run --no-sync python scripts/dpia_video_capture.py`

Capture rules (already honored by the script — keep them if you re-record anything):
- Use **Python Playwright** with `executable_path=/home/ayoola/.cache/ms-playwright/chromium-1223/chrome-linux/chrome`.
  Never use the MCP Playwright browser tools — their screenshots/videos land on the wrong machine.
- Use `wait_until="load"`, never `networkidle` (SSE keeps the network busy forever).
- The script is read-mostly: it types into forms and opens dialogs but presses Escape instead of
  submitting, so no new requests are created (one harmless draft session may appear; drafts are
  filtered from officer views). Do not "fix" it to submit anything.
- High-res stills for title/end cards and cutaways already exist in `docs/images/` (28 images:
  `dpia-doc-*.png`, `dpia-flow-*.png`, `dpia-guide-*.png`).

## Footage → scene map

| Clip | Content |
| --- | --- |
| 01-portfolio.webm | Portfolio hero, Request a DPIA button, Incoming requests |
| 02-inbox.webm | Inbox rows, hover on "Review request" |
| 03-cockpit-overview.webm | Cockpit header, readiness banner, Overview scroll |
| 04-cockpit-tabs.webm | Tab tour: Processing map → Evidence & questions → Screening → Full DPIA → Audit (~4s each; cut on tab clicks) |
| 05-cockpit-deep.webm | Deep scroll: agent activity, outreach panel, chat dock |
| 06-requester-intake.webm | Priya fills the guided intake, opens Review & submit (trim the typing) |
| 07-requester-status.webm | Completed request: status card + outcome card + acknowledgement |
| 08-officer-review.webm | Officer review page: detail card, transcript, actions |
| 09-outreach.webm | Share-questions dialog, scoped question tick, accepted stakeholder row |
| 10-contributor-respond.webm | Contributor respond view read-back |

## Storyboard + narration script (~2:30, ~150 wpm)

Use this script verbatim unless timing forces trims; keep terminology exact. VO = voiceover,
OST = on-screen text (lower-third or callout). Add gentle punch-ins/Ken Burns on an inner wrapper
(never animate the timed clip element) to focus on the UI region each line references.

**S1 · 0:00–0:08 · Title card** (still: `docs/images/dpia-guide-requests-home.png` blurred, or a
designed card in the app's palette)
- OST: "DPIA Desk — privacy assessments, run like operations"
- VO: "Every new tool, vendor, or data flow raises the same question: do we need a Data Protection
  Impact Assessment — and who does the work?"

**S2 · 0:08–0:24 · The desk** (01-portfolio)
- OST: "One portfolio for every assessment"
- VO: "DPIA Desk gives the privacy team one place to see every assessment: what's in flight, what's
  blocked, and what's arriving — including new requests from anywhere in the organisation."

**S3 · 0:24–0:42 · The cockpit** (03-cockpit-overview)
- OST: "Case cockpit · decision readiness at a glance"
- VO: "Each case opens into a cockpit. A readiness banner tracks exactly what stands between you and
  a defensible decision, over a live summary of the processing under review."

**S4 · 0:42–1:00 · Feature montage** (04-cockpit-tabs, fast cuts on each tab click)
- OST per cut: "Processing map" / "Evidence & questions" / "Screening" / "Full DPIA" / "Audit"
- VO: "The processing map charts how personal data moves. Evidence and open questions live beside
  the answers that resolve them. Screening findings roll up to an officer decision, the full DPIA
  assembles itself section by section, and every action lands in an audit trail."

**S5 · 1:00–1:14 · The agent** (05-cockpit-deep)
- OST: "An investigation agent does the legwork"
- VO: "Behind the desk, an Omnigent investigation agent reads the evidence, drafts findings, and
  proposes corrections — while you stay in control of every decision."

**S6 · 1:14–1:34 · Requester demo** (06-requester-intake, typing trimmed)
- OST: "Anyone can request a DPIA — no privacy training required"
- VO: "Here's how it works end to end. Priya in Procurement wants to pilot a vendor analytics tool.
  She opens Request a DPIA, and a guided intake captures the project, the data, the vendors — even
  the things she doesn't know yet. One click sends it to the Privacy Office."

**S7 · 1:34–1:50 · Officer triage** (02-inbox → 08-officer-review)
- OST: "Triage from the inbox: accept, clarify, or decline"
- VO: "The request lands in officer Alex Morgan's inbox. Alex reviews the structured summary and the
  conversation behind it, then accepts it for screening — or asks for clarification, or declines
  with a recorded reason."

**S8 · 1:50–2:10 · Outreach + contributor** (09-outreach → 10-contributor-respond)
- OST: "Scoped questions out · recorded evidence in"
- VO: "Open questions become scoped outreach. Alex shares just the relevant questions with IT
  Security; Jordan answers in a focused view, and the answers come back staged — ready to accept as
  recorded evidence, attributed to the person who gave them."

**S9 · 2:10–2:26 · Outcome** (07-requester-status)
- OST: "Approved with conditions · acknowledged by the requester"
- VO: "When screening completes, the outcome flows back to Priya: approved with conditions, each
  with an owner and a due date — and her acknowledgement is captured in the audit trail."

**S10 · 2:26–2:36 · Close** (designed end card)
- OST: "DPIA Desk · built on Omnigent" + "All data shown is synthetic"
- VO: "DPIA Desk. Privacy assessments, run like operations."

## Design direction

- Match the app: clean, light, enterprise-calm. Accent blue `#2563eb`, near-black ink on white,
  muted grays; rounded-lg cards. Type: Inter or a close geometric sans. No neon, no glitch.
- Motion: restrained — soft fades/slides for lower-thirds, 2–4% punch-ins on footage, cut on the
  tab clicks in S4. Footage already shows a synthetic blue cursor dot; do not add another cursor.
- Audio: neutral professional TTS voice (calm, mid-pace); understated corporate/ambient bed at
  low gain, ducked under VO; optional soft UI tick on S4 cuts.
- Captions: burn in subtle captions from the VO lines.

## Acceptance criteria

1. `docs/video/footage/` holds the 10 clips + manifest; footage is real app capture, 1920x1080.
2. HyperFrames project under `docs/video/project/` passes `npx hyperframes check`.
3. Final render at `docs/video/dpia-explainer.mp4`, 1920x1080, 30fps, ~2:20–2:45, VO + music +
   lower-thirds + captions, synthetic-data disclaimer on the closing card.
4. Narration uses the product terminology above; no invented feature claims.
5. Nothing committed; app code untouched; the control workspace `/home/ayoola/qm` untouched.
