import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:5178"
OUT = Path("/home/ayoola/qm/.copilot-azure/worktrees/dpia-investigation-demo/docs/images")
CHROME = "/home/ayoola/.cache/ms-playwright/chromium-1223/chrome-linux/chrome"
CASE = f"{BASE}/dpia/cases/student-success-alert"

PROPOSED_VALUE = "The model and primary database are hosted in London (UK region)."
RATIONALE = (
    "Hosting location changes the vendor and international-transfer assessment basis, "
    "so the Privacy Assessor finding must be replayed."
)


async def shot(page, url, name, full=True, settle=1.2):
    await page.goto(url, wait_until="networkidle")
    await page.wait_for_timeout(int(settle * 1000))
    await page.screenshot(path=str(OUT / name), full_page=full)
    print("saved", name)


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})

        await page.goto(f"{BASE}/dpia", wait_until="networkidle")
        await page.evaluate(
            "() => Object.keys(localStorage).filter(k => k.includes('dpia'))"
            ".forEach(k => localStorage.removeItem(k))"
        )

        await shot(page, f"{BASE}/inbox", "dpia-doc-inbox.png")
        await shot(page, f"{BASE}/dpia", "dpia-doc-portfolio.png")
        await shot(page, f"{BASE}/dpia/new", "dpia-doc-new-assessment.png")
        await shot(page, CASE, "dpia-doc-case-overview.png")
        await shot(page, f"{CASE}?tab=map", "dpia-doc-processing-map.png")
        await shot(page, f"{CASE}?tab=evidence", "dpia-doc-evidence.png")
        await shot(page, f"{CASE}?tab=screening", "dpia-doc-screening.png")
        await shot(page, f"{CASE}?tab=full", "dpia-doc-full-assessment.png")
        await shot(page, f"{CASE}?tab=audit", "dpia-doc-audit.png")

        await page.goto(f"{CASE}?agentActivity=1", wait_until="networkidle")
        await page.wait_for_timeout(1200)
        await page.screenshot(path=str(OUT / "dpia-doc-agent-activity.png"))
        print("saved dpia-doc-agent-activity.png")

        await page.goto(CASE, wait_until="networkidle")
        await page.wait_for_timeout(800)
        await page.get_by_role("button", name="Draft correction manually").click()
        dialog = page.get_by_role("dialog")
        await dialog.get_by_label("Proposed value for Model and database hosting").fill(
            PROPOSED_VALUE
        )
        await dialog.get_by_label("Correction rationale").fill(RATIONALE)
        await dialog.get_by_role("button", name="Create proposal").click()
        await page.wait_for_timeout(800)
        card = page.get_by_test_id("correction-proposal-card")
        await card.scroll_into_view_if_needed()
        await card.screenshot(path=str(OUT / "dpia-doc-proposal-card.png"))
        print("saved dpia-doc-proposal-card.png")

        chat = page.locator('section[aria-label="Case agent chat"]')
        await chat.scroll_into_view_if_needed()
        await chat.screenshot(path=str(OUT / "dpia-doc-chat-dock.png"))
        print("saved dpia-doc-chat-dock.png")

        nav = page.get_by_test_id("sidebar-primary-nav")
        await nav.screenshot(path=str(OUT / "dpia-doc-sidebar-nav.png"))
        print("saved dpia-doc-sidebar-nav.png")

        await browser.close()


asyncio.run(main())
