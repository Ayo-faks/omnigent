import asyncio
from pathlib import Path

from playwright.async_api import async_playwright, expect

BASE = "http://127.0.0.1:5178"
OUT = Path("/home/ayoola/qm/.copilot-azure/worktrees/dpia-investigation-demo/docs/images")
CHROME = "/home/ayoola/.cache/ms-playwright/chromium-1223/chrome-linux/chrome"
REVIEW_URL = f"{BASE}/dpia/requests/req-vendor-wellbeing-analyti-mt46wvrq"


async def shot(page, name):
    await page.screenshot(path=str(OUT / name), full_page=True)
    print("saved", name)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME)
        officer = await (
            await browser.new_context(viewport={"width": 1440, "height": 900})
        ).new_page()
        requester = await (
            await browser.new_context(viewport={"width": 1440, "height": 900})
        ).new_page()

        print("== officer: send outcome")
        await officer.goto(REVIEW_URL, wait_until="load")
        await expect(
            officer.get_by_role("button", name="Send outcome to requester")
        ).to_be_visible(timeout=40000)
        await officer.get_by_role("button", name="Send outcome to requester").click()
        await officer.get_by_label("Reasons (one per line)").fill(
            "Screening indicates a full DPIA is likely before launch; "
            "hosting evidence is now recorded."
        )
        await officer.get_by_label("Condition owner").fill("Procurement")
        await shot(officer, "dpia-flow-10-outcome-dialog.png")
        await officer.get_by_role("button", name="Send outcome").click()
        await expect(officer.get_by_text("Outcome sent")).to_be_visible(timeout=40000)

        print("== requester: outcome + acknowledge")
        await requester.goto(f"{BASE}/dpia/request", wait_until="load")
        submitted = requester.locator("li", has_text="req-vendor-wellbeing-analyti-mt46wvrq")
        await expect(submitted).to_be_visible(timeout=20000)
        await submitted.get_by_role("button", name="Open").click()
        await expect(requester.get_by_test_id("dpia-outcome-card")).to_be_visible(timeout=60000)
        await shot(requester, "dpia-flow-11-outcome-requester.png")
        await requester.get_by_role("button", name="Acknowledge outcome").click()
        await expect(requester.get_by_role("button", name="Acknowledged")).to_be_visible(
            timeout=20000
        )
        await shot(requester, "dpia-flow-12-outcome-acknowledged.png")

        await browser.close()
        print("QA COMPLETE")


asyncio.run(main())
