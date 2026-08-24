# Task: wulo-work explainer v2 — rebrand + Apple-grade re-edit

You are working in the worktree `/home/ayoola/qm/.copilot-azure/worktrees/dpia-investigation-demo`
(branch `feature/dpia-investigation-demo`). Do not commit, push, or open PRs. Do not modify app
source code — this task only captures footage and builds a video. Everything you produce lives
under `docs/video/`. The control workspace `/home/ayoola/qm` stays untouched.

## What already exists (v1 — treat as your baseline, then surpass it)

- Delivered v1: `docs/video/dpia-explainer.mp4` (144.5s, 1920x1080@30fps, H.264+AAC, −14.4 LUFS).
- HyperFrames project: `docs/video/project/` — `BRIEF.md`, `STORYBOARD.md`, `SCRIPT.md`,
  `audio_meta.json`, `cut_times.json`, ten frames under `compositions/frames/`, caption skin at
  `.hyperframes/caption-skin.html`, Azure TTS pipeline at `tools/azure_tts.py`.
- v1 brief (structure to follow, now superseded on branding): `docs/dpia-video-prompt.md`.
- Old footage in `docs/video/footage/` and stills in `docs/images/` show the **old brand — none of
  it is reusable on screen**. Re-capture everything you show.

## The rebrand (the reason v2 exists)

- The app has been rebranded: the product is now **wulo-work**, with a **new logo**, attributed as
  **"wulo-work by Neuter Labs"**.
- **On-screen truth wins.** Take the exact rendered casing, wordmark, and logo from the rebranded
  app UI — do not trust this document or old docs for spelling. Defaults if the UI is ambiguous:
  product "wulo-work", attribution "by Neuter Labs".
- Locate the new logo asset in the app source (look in `web/public/` and `web/src/` — read-only)
  and copy it into the project's `assets/`; never redraw it.
- Verify whether the feature area is still called "DPIA Desk" inside the rebranded UI and
  `docs/dpia-feature-guide.md`. If it is, keep it; the platform mentions change, the feature name
  only changes if the UI changed it.
- Narration updates (verbatim otherwise, en-GB spelling preserved):
  - S5: "an Omnigent investigation agent" → "a wulo-work investigation agent" (adjust to the UI's
    actual naming).
  - S10 VO: "DPIA Desk. Privacy assessments, run like operations." → keep, unless the UI renamed
    the desk; the close card line becomes "wulo-work · by Neuter Labs".
  - Every other "Omnigent" in OST/cards becomes the new brand.
- TTS pronunciation gate: audition "wulo-work" and "Neuter Labs" before generating the full set.
  Feed TTS "Wulo Work" (spoken form) if the hyphen mangles delivery; SSML phoneme if needed.
  On-screen copy always keeps the real casing. Keep `<say-as interpret-as="characters">DPIA</say-as>`.

## Step 0 — stack + full re-capture

Same stack as v1 (`docs/dpia-video-prompt.md` § Step 0): API on 6777 (**must run unsandboxed** so
the spawned codex reads `~/.codex/auth.json` — a sandboxed API accepts requests but runner attach
hangs and the intake card never appears), Vite on 5178, Python Playwright with
`executable_path=/home/ayoola/.cache/ms-playwright/chromium-1223/chrome-linux/chrome`,
`wait_until="load"` never `networkidle`, never MCP browser tools.

Re-run `scripts/dpia_video_capture.py` unchanged for the ten clips, then re-shoot title/close
plates as fresh 1920x1080 screenshots of the rebranded UI (a small throwaway script in /tmp; do
not edit repo scripts, do not submit forms).

Hard-won capture rules from v1 — apply them:

1. `page.mouse.wheel` scrolls whatever is under the pointer. Move the pointer over the main pane
   (e.g. `page.mouse.move(1150, 620)`) before any glide, or you record a page that never scrolls
   (v1's clips 03/05 failed exactly this way).
2. Every clip starts with a blank white load prefix (2–13s, worse on cold Vite). Never trust the
   scripted wait times: probe content onset per clip with
   `ffmpeg -ss T -i clip.webm -vframes 1 -vf "signalstats,metadata=print:key=lavfi.signalstats.YAVG" -f null -`
   (YAVG 235 = blank, ~228 = content) and choose `data-media-start` from measured onset.
3. A clip shorter than its scene gets a frozen-tail extension:
   `ffmpeg -i in.webm -vf "tpad=stop_mode=clone:stop_duration=N" -c:v libvpx -b:v 4M -an out-ext.webm`.
4. Clip 09 types "Legal & Compliance" into the share dialog — story says IT Security. Use only the
   accepted-row window (content from ~37.8s in v1's recording; re-probe yours) and fresh rebranded
   stills for the dialog moment.
5. Qualify all ten clips with ffprobe (1920x1080, sane duration) + a timestamped contact sheet
   before any HyperFrames work; confirm no error toasts (a dead API mid-capture puts a 502 toast
   in the footage), no real data, new brand visible.

## Pipeline notes that will save you hours (v1 scar tissue)

- `export HYPERFRAMES_BROWSER_PATH=/home/ayoola/.cache/ms-playwright/chromium-1223/chrome-linux/chrome`
  before any `npx hyperframes check|snapshot|preview|render` (ARM64 has no Headless Shell; run
  browser-launching commands unsandboxed).
- Footage mounts as `<video data-frame-video="approved" …>` inside frames with explicit
  `data-frame-video-x/y/width/height` + `data-media-start`; `assemble-index.mjs` hoists it to the
  host root and **consumes the tag from the frame file** (leaves a hoist comment). Keep a
  reinsertion script like /tmp/dpia-fix-frames.py from v1: specs → tags → assemble, every rebuild.
- Hoisted videos live at host root: frame timelines cannot tween them. Footage reframes are
  **static geometry** (2–4% overscan aimed at the UI region); animated Ken Burns is only for
  in-frame `<img>` stills on untimed inner wrappers.
- Never use `immediateRender: false` on tweens targeting distinct elements — it renders them
  visible before their start time (v1's close card flashed all lines at once).
- Narration: Azure AI Speech only. Voice `en-GB-Ollie:DragonHDLatestNeural` (swedencentral,
  resource `speech-voicelab-e5dj24rvkgx2c`, rg `rg-salescoach-swe`) worked well; `en-GB-Ada` HD is
  the female alternative. Word timings via Azure STT fast transcription
  (`/speechtotext/transcriptions:transcribe?api-version=2024-11-15`, en-GB). Reuse and adapt
  `docs/video/project/tools/azure_tts.py`: per-line WAVs, loudnorm I=-16 TP=-1.5, lead/tail pads
  are the pacing lever (v1: lead 1.0s on L1, tails 0.45–3.6s → 144.5s total; acceptance 140–165s),
  frame-keyed `audio_meta.json`, SFX offsets anchored on word timestamps (STT normalizes spelling —
  match word prefixes, e.g. "acknowledg"; v1 also had to patch "run-like" → "run like" in words[]
  before caption build).
- `music: none` in STORYBOARD frontmatter; no BGM anywhere. SFX only from the bundled library
  (`~/.agents/skills/media-use/audio/assets/sfx/`), copied into `.media/audio/sfx/`.
- After narration: `audio.mjs sync-durations` (mechanical), captions build (skin auto-picked),
  assemble, `transitions verify`, `npx hyperframes lint` + `check`, snapshots at every scene
  midpoint + cut±0.15s + internal splice points, and inspect the sheets — v1's real defects were
  only visible there.
- Loudness conform on the final MP4 if needed: audio-only trim + limiter with `-c:v copy`; target
  ≈ −16 to −14 LUFS integrated, true peak ≤ −2 dBFS. Verify with `ebur128`, blackdetect, per-scene
  volumedetect, and rendered-frame sampling from the actual MP4.

## The v2 creative bar — cut it like an Apple product film editor

v1 is competent and correct. v2 must feel **designed**. Direction:

- **One idea per shot.** If a frame carries a lower-third, an eyebrow, and a tag, remove until one
  element carries the beat. Prefer removing over adding — every overlay must earn its frame.
- **Typography is the design.** One family (the app's own; Inter is staged locally in
  `assets/fonts/`), at most two sizes per frame, tight negative tracking on display sizes,
  generous margins (≥96px), consistent 8px spacing grid. Lower-thirds become quieter: text +
  hairline accent rule, minimal container, no heavy shadows (≤8% opacity if any).
- **Color discipline.** White, near-black ink, and exactly one accent taken from the new wulo-work
  brand (read it from the rebranded UI tokens — do not assume v1's #2563eb survived the rebrand).
- **Motion restraint.** Long-tail `power3` only, moves ≤3%, nothing animates in the back half of a
  hold. Cuts land ON word beats (you have word timestamps — use them for every cut time, not round
  numbers). After each key line, allow 0.8–1.2s of visual-only air; stillness is a feature.
- **Open cold, close clean.** S1: rebranded product plate, one quiet title move, no clutter.
  S10: new logo mark → "wulo-work" wordmark → "by Neuter Labs" small, then "All data shown is
  synthetic" in muted gray; hold ≥3s; no exit motion.
- **Sound like hardware films.** ≤6 SFX total, all ≤0.2 volume: keep the S7 inbox ping, S9
  confirmation chime, one soft close accent; drop the typing patter; keep tab ticks only if they
  sit under the VO without reading as UI noise. Silence between lines must be clean.
- **Captions.** Keep the burned captions and the bottom ~17% keep-out, but check the skin still
  matches the new brand tokens after the frame.md remix; lighter visual weight preferred.

## Scene skeleton (keep v1's ten-scene structure; upgrade each shot)

Same map as `docs/dpia-video-prompt.md` (S1 title · S2 portfolio · S3 cockpit/readiness · S4
five-tab montage with hard cuts on real tab clicks · S5 investigation agent · S6 Priya's intake,
typing spliced out · S7 inbox → officer review · S8 scoped outreach → IT Security → Jordan's
read-back · S9 outcome + acknowledgement · S10 close). Narration stays verbatim except the brand
substitutions above. Re-derive all internal cut times from the new narration's word timestamps.

## Acceptance criteria

1. Ten fresh clips + manifest under `docs/video/footage/`, rebranded UI visible, 1920x1080,
   qualified by probe + contact sheet; zero old-brand pixels anywhere in the cut.
2. Project passes `npx hyperframes check`; snapshot sheets inspected at midpoints, cuts, and
   splices; no blank frames, no caption/lower-third overlap, no double cursor.
3. Final render at `docs/video/wulo-work-explainer.mp4`: 1920x1080, 30fps, 140–165s, Azure en-GB
   HD VO + burned captions + ≤6 SFX, no music, "wulo-work · by Neuter Labs" close with the real
   logo and the synthetic-data disclaimer.
4. Narration and OST use the rebranded terminology exactly as rendered in the app; no invented
   claims; feature-guide truth for the workflow (Priya Shah → Alex Morgan → IT Security/Jordan Ali).
5. Nothing committed; app source untouched; only `docs/video/` (and nothing else) gains files;
   `/home/ayoola/qm` untouched. Baseline `git status --porcelain` for both trees before starting;
   diff against it at the end.
