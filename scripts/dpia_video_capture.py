import asyncio
import json
import shutil
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright, expect

WEB = "http://127.0.0.1:5178"
API = "http://127.0.0.1:6777"
ROOT = Path("/home/ayoola/qm/.copilot-azure/worktrees/dpia-investigation-demo")
FOOTAGE = ROOT / "docs" / "video" / "footage"
RAW = FOOTAGE / "raw"
CHROME = "/home/ayoola/.cache/ms-playwright/chromium-1223/chrome-linux/chrome"
VIEW = {"width": 1920, "height": 1080}
CASE_URL = f"{WEB}/dpia/cases/student-success-alert"
REVIEW_URL = f"{WEB}/dpia/requests/req-vendor-wellbeing-analyti-mt46wvrq"
RESPOND_URL = f"{WEB}/dpia/respond/93dcf47fcdb84428bb67b40fc152610c"

CURSOR_JS = """
window.addEventListener('DOMContentLoaded', () => {
  const dot = document.createElement('div');
    dot.style.cssText = [
        'position:fixed',
        'z-index:2147483647',
        'width:22px',
        'height:22px',
        'border-radius:50%',
        'background:rgba(37,99,235,.85)',
        'border:2.5px solid #fff',
        'box-shadow:0 1px 6px rgba(0,0,0,.45)',
        'pointer-events:none',
        'left:-60px',
        'top:-60px',
        'transform:translate(-50%,-50%)'
    ].join(';');
  document.body.appendChild(dot);
  window.addEventListener('mousemove', (e) => {
    dot.style.left = e.clientX + 'px';
    dot.style.top = e.clientY + 'px';
  }, { passive: true });
  window.addEventListener('mousedown', () => {
    dot.style.transform = 'translate(-50%,-50%) scale(.75)';
  });
  window.addEventListener('mouseup', () => {
    dot.style.transform = 'translate(-50%,-50%) scale(1)';
  });
});
"""

MANIFEST = []


def preflight():
    checks = (
        (
            WEB,
            "cd web && OMNIGENT_URL=http://127.0.0.1:6777 pnpm exec vite "
            "--host 127.0.0.1 --port 5178",
        ),
        (
            f"{API}/v1/hosts",
            "uv run --no-sync omnigent server --host 127.0.0.1 --port 6777 ... "
            "(see docs/dpia-video-prompt.md)",
        ),
    )
    for url, hint in checks:
        try:
            with urllib.request.urlopen(url, timeout=5) as res:
                res.read(64)
        except (OSError, ValueError) as exc:
            raise SystemExit(
                f"stack check failed for {url}: {exc}\nstart it with: {hint}"
            ) from exc


async def glide(page, total, step=140, pause=110):
    remaining = total
    while remaining > 0:
        delta = min(step, remaining)
        await page.mouse.wheel(0, delta)
        remaining -= delta
        await page.wait_for_timeout(pause)


async def glide_up(page, total, step=160, pause=90):
    remaining = total
    while remaining > 0:
        await page.mouse.wheel(0, -min(step, remaining))
        remaining -= step
        await page.wait_for_timeout(pause)


async def drift(page, locator):
    await locator.scroll_into_view_if_needed()
    await page.wait_for_timeout(300)
    box = await locator.bounding_box()
    if box:
        await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=42)
    await page.wait_for_timeout(350)


async def click_soft(page, locator):
    await drift(page, locator)
    await locator.click()


async def type_into(page, label, text, delay=22):
    field = page.get_by_label(label)
    await drift(page, field)
    await field.press_sequentially(text, delay=delay)


async def record(pw_browser, name, description, suggested_use, fn):
    ctx = await pw_browser.new_context(
        viewport=VIEW, record_video_dir=str(RAW), record_video_size=VIEW
    )
    await ctx.add_init_script(CURSOR_JS)
    page = await ctx.new_page()
    try:
        await fn(page)
        await page.wait_for_timeout(1200)
    finally:
        await ctx.close()
    src = Path(await page.video.path())
    dest = FOOTAGE / f"{name}.webm"
    if dest.exists():
        dest.unlink()
    src.rename(dest)
    MANIFEST.append(
        {"file": dest.name, "description": description, "suggested_use": suggested_use}
    )
    print("recorded", dest.name)


async def scene_portfolio(page):
    await page.goto(f"{WEB}/dpia", wait_until="load", timeout=90000)
    await expect(page.get_by_role("heading", name="DPIA Investigation Desk")).to_be_visible(
        timeout=30000
    )
    await page.wait_for_timeout(2800)
    await drift(page, page.get_by_role("link", name="Request a DPIA"))
    await page.wait_for_timeout(900)
    await glide(page, 700)
    await drift(page, page.get_by_test_id("dpia-incoming-requests"))
    await page.wait_for_timeout(1800)


async def scene_inbox(page):
    await page.goto(f"{WEB}/inbox", wait_until="load", timeout=90000)
    await page.wait_for_timeout(2600)
    rows = page.get_by_role("link", name="Review request")
    if await rows.count() > 0:
        await drift(page, rows.first)
    else:
        await glide(page, 400)
    await page.wait_for_timeout(1600)


async def scene_cockpit_overview(page):
    await page.goto(CASE_URL, wait_until="load", timeout=90000)
    await expect(page.get_by_role("tab", name="Overview")).to_be_visible(timeout=30000)
    await page.wait_for_timeout(3200)
    await glide(page, 900)
    await page.wait_for_timeout(1800)
    await glide(page, 700)
    await page.wait_for_timeout(1500)


async def scene_cockpit_tabs(page):
    await page.goto(CASE_URL, wait_until="load", timeout=90000)
    await expect(page.get_by_role("tab", name="Overview")).to_be_visible(timeout=30000)
    await page.wait_for_timeout(2400)
    for tab in ("Processing map", "Evidence & questions", "Screening", "Full DPIA", "Audit"):
        await click_soft(page, page.get_by_role("tab", name=tab))
        await page.wait_for_timeout(2400)
        await glide(page, 420)
        await page.wait_for_timeout(700)
        await glide_up(page, 420)


async def scene_cockpit_deep(page):
    await page.goto(CASE_URL, wait_until="load", timeout=90000)
    await expect(page.get_by_role("tab", name="Overview")).to_be_visible(timeout=30000)
    await page.wait_for_timeout(2600)
    await glide(page, 2400, step=170, pause=95)
    await page.wait_for_timeout(2200)


async def scene_requester_intake(page):
    await page.goto(f"{WEB}/dpia/request", wait_until="load", timeout=90000)
    await expect(page.get_by_role("heading", name="Request a DPIA")).to_be_visible(timeout=30000)
    await page.wait_for_timeout(1800)
    await click_soft(page, page.get_by_role("button", name="Start a new request"))
    await expect(page.get_by_test_id("dpia-intake-card")).to_be_visible(timeout=30000)
    await page.wait_for_timeout(1200)
    await type_into(page, "Your name", "Priya Shah", delay=30)
    await type_into(page, "Your team", "Procurement", delay=30)
    await type_into(page, "Project title", "Vendor Wellbeing Analytics", delay=24)
    await type_into(
        page,
        "Purpose",
        "Score student wellbeing survey responses with a vendor model "
        "to prioritise support outreach.",
        delay=9,
    )
    await type_into(page, "Data subjects", "Enrolled students", delay=14)
    await type_into(
        page,
        "Personal data involved",
        "Survey responses, student identifiers, wellbeing scores",
        delay=9,
    )
    await type_into(page, "Vendors / processors", "Acme Analytics Ltd", delay=14)
    await type_into(page, "Timeline", "Pilot in October", delay=14)
    await type_into(
        page, "Known unknowns (one per line)", "Hosting location\nSubprocessor list", delay=12
    )
    await page.wait_for_timeout(600)
    await click_soft(page, page.get_by_role("button", name="Review & submit"))
    await page.wait_for_timeout(2600)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(900)


async def scene_requester_status(page):
    await page.goto(f"{WEB}/dpia/request", wait_until="load", timeout=90000)
    await expect(page.get_by_text("Your requests")).to_be_visible(timeout=30000)
    await page.wait_for_timeout(1800)
    row = page.locator("li", has_text="req-vendor-wellbeing")
    if await row.count() == 0:
        row = page.locator("li", has_text="Status: completed")
    if await row.count() > 0:
        await click_soft(page, row.first.get_by_role("button", name="Open"))
    else:
        await click_soft(page, page.get_by_role("button", name="Open").first)
    await expect(page.get_by_test_id("dpia-request-status")).to_be_visible(timeout=30000)
    await page.wait_for_timeout(1800)
    outcome = page.get_by_test_id("dpia-outcome-card")
    if await outcome.count() > 0:
        await drift(page, outcome)
        await page.wait_for_timeout(2400)
    await glide(page, 500)
    await page.wait_for_timeout(1500)


async def scene_officer_review(page):
    await page.goto(REVIEW_URL, wait_until="load", timeout=90000)
    await expect(page.get_by_test_id("dpia-request-detail-card")).to_be_visible(timeout=30000)
    await page.wait_for_timeout(2600)
    await glide(page, 1000)
    await page.wait_for_timeout(1600)
    await glide(page, 800)
    await page.wait_for_timeout(1600)


async def scene_outreach(page):
    await page.goto(CASE_URL, wait_until="load", timeout=90000)
    panel = page.get_by_test_id("stakeholder-outreach-panel")
    await expect(panel).to_be_visible(timeout=30000)
    await drift(page, panel)
    await page.wait_for_timeout(1600)
    await click_soft(page, page.get_by_role("button", name="Share questions with a stakeholder"))
    dialog = page.get_by_role("dialog")
    await expect(dialog).to_be_visible(timeout=15000)
    team = dialog.get_by_label("Stakeholder team")
    await drift(page, team)
    await team.press_sequentially("Legal & Compliance", delay=26)
    boxes = dialog.get_by_role("checkbox")
    if await boxes.count() > 0:
        await click_soft(page, boxes.first)
    await page.wait_for_timeout(1800)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(900)
    view_links = page.get_by_role("link", name="Open stakeholder view")
    if await view_links.count() > 0:
        await drift(page, view_links.first)
        await page.wait_for_timeout(1800)


async def scene_contributor(page):
    await page.goto(RESPOND_URL, wait_until="load", timeout=90000)
    await page.wait_for_timeout(2800)
    await glide(page, 900)
    await page.wait_for_timeout(1800)
    await glide(page, 500)
    await page.wait_for_timeout(1400)


async def main():
    preflight()
    FOOTAGE.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME)
        await record(
            browser,
            "01-portfolio",
            "DPIA desk portfolio: hero, Request a DPIA button, scroll to "
            "Incoming requests section.",
            "Problem framing / product intro scene.",
            scene_portfolio,
        )
        await record(
            browser,
            "02-inbox",
            "Inbox with DPIA rows: request awaiting triage, hover on Review request link.",
            "Officer triage scene opener.",
            scene_inbox,
        )
        await record(
            browser,
            "03-cockpit-overview",
            "Case cockpit: header, readiness banner, slow scroll through the Overview tab.",
            "Cockpit hero scene.",
            scene_cockpit_overview,
        )
        await record(
            browser,
            "04-cockpit-tabs",
            "Tab tour: Processing map, Evidence & questions, Screening, "
            "Full DPIA, Audit — ~4s each with a small scroll.",
            "Fast feature montage; cut on each tab click.",
            scene_cockpit_tabs,
        )
        await record(
            browser,
            "05-cockpit-deep",
            "Deep scroll to the bottom of the case page: agent activity panel, "
            "outreach panel, chat dock.",
            "Agent narrative scene.",
            scene_cockpit_deep,
        )
        await record(
            browser,
            "06-requester-intake",
            "Requester types the guided intake (Priya Shah / Vendor Wellbeing Analytics), "
            "opens Review & submit dialog, closes it.",
            "Demo scene: requester journey. Trim typing freely.",
            scene_requester_intake,
        )
        await record(
            browser,
            "07-requester-status",
            "Requester reopens the completed request: transcript, status card, "
            "outcome card with acknowledgement.",
            "Demo scene: outcome delivery.",
            scene_requester_status,
        )
        await record(
            browser,
            "08-officer-review",
            "Officer review page for the completed request: detail card, "
            "transcript scroll, actions area.",
            "Demo scene: officer triage.",
            scene_officer_review,
        )
        await record(
            browser,
            "09-outreach",
            "Stakeholder outreach panel: open share dialog, type team, tick a "
            "scoped question, close, hover accepted row.",
            "Demo scene: officer outreach + reconciliation.",
            scene_outreach,
        )
        await record(
            browser,
            "10-contributor-respond",
            "Contributor respond page (submitted read-back): scoped questions "
            "with recorded answers.",
            "Demo scene: contributor journey.",
            scene_contributor,
        )
        await browser.close()
    shutil.rmtree(RAW, ignore_errors=True)
    (FOOTAGE / "manifest.json").write_text(json.dumps(MANIFEST, indent=2) + "\n")
    print("manifest written with", len(MANIFEST), "clips ->", FOOTAGE / "manifest.json")


asyncio.run(main())
