import asyncio
from pathlib import Path

from playwright.async_api import async_playwright, expect

BASE = "http://127.0.0.1:5178"
OUT = Path("/home/ayoola/qm/.copilot-azure/worktrees/dpia-investigation-demo/docs/images")
CHROME = "/home/ayoola/.cache/ms-playwright/chromium-1223/chrome-linux/chrome"
REVIEW_URL = f"{BASE}/dpia/requests/req-vendor-wellbeing-analyti-mt46wvrq"


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME)
        page = await (
            await browser.new_context(viewport={"width": 1440, "height": 900})
        ).new_page()

        await page.goto(f"{BASE}/dpia/request", wait_until="load", timeout=90000)
        await expect(page.get_by_role("heading", name="Request a DPIA")).to_be_visible(
            timeout=20000
        )
        await page.wait_for_timeout(2500)
        await page.screenshot(path=str(OUT / "dpia-guide-requests-home.png"), full_page=True)
        print("saved dpia-guide-requests-home.png")

        await page.goto(REVIEW_URL, wait_until="load", timeout=90000)
        await expect(page.get_by_test_id("dpia-request-detail-card")).to_be_visible(timeout=30000)
        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(OUT / "dpia-guide-review-completed.png"), full_page=True)
        print("saved dpia-guide-review-completed.png")

        await page.goto(
            f"{BASE}/dpia/cases/student-success-alert", wait_until="load", timeout=90000
        )
        panel = page.get_by_test_id("stakeholder-outreach-panel")
        await expect(panel).to_be_visible(timeout=30000)
        await page.wait_for_timeout(2500)
        await panel.scroll_into_view_if_needed()
        await panel.screenshot(path=str(OUT / "dpia-guide-outreach-accepted.png"))
        print("saved dpia-guide-outreach-accepted.png")

        await browser.close()


asyncio.run(main())
