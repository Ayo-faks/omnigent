import asyncio

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:5178"
CHROME = "/home/ayoola/.cache/ms-playwright/chromium-1223/chrome-linux/chrome"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME)
        page = await (await browser.new_context()).new_page()
        page.on("console", lambda m: print("console:", m.type, m.text[:200]))
        await page.goto(f"{BASE}/dpia/request", wait_until="networkidle")
        await page.get_by_role("button", name="Start a new request").click()
        await page.wait_for_timeout(8000)
        alert = page.get_by_role("alert")
        for i in range(await alert.count()):
            print("alert:", await alert.nth(i).inner_text())
        print(
            "panel text:",
            (await page.locator("section[aria-label='Request conversation']").inner_text())[:400],
        )
        await browser.close()


asyncio.run(main())
