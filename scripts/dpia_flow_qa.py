import asyncio
from pathlib import Path

from playwright.async_api import async_playwright, expect

BASE = "http://127.0.0.1:5178"
OUT = Path("/home/ayoola/qm/.copilot-azure/worktrees/dpia-investigation-demo/docs/images")
CHROME = "/home/ayoola/.cache/ms-playwright/chromium-1223/chrome-linux/chrome"


async def shot(page, name):
    await page.screenshot(path=str(OUT / name), full_page=True)
    print("saved", name)


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME)
        requester = await (
            await browser.new_context(viewport={"width": 1440, "height": 900})
        ).new_page()
        officer = await (
            await browser.new_context(viewport={"width": 1440, "height": 900})
        ).new_page()

        print("== requester: start + intake")
        await requester.goto(f"{BASE}/dpia/request", wait_until="networkidle")
        await requester.get_by_role("button", name="Start a new request").click()
        await expect(requester.get_by_test_id("dpia-intake-card")).to_be_visible(timeout=20000)
        await expect(
            requester.get_by_label("Message the DPIA agent"),
        ).to_be_enabled(timeout=30000)

        await requester.get_by_label("Your name").fill("Priya Shah")
        await requester.get_by_label("Your team").fill("Procurement")
        await requester.get_by_label("Project title").fill("Vendor Wellbeing Analytics")
        await requester.get_by_label("Purpose").fill(
            "Score student wellbeing survey responses with a vendor model "
            "to prioritise support outreach."
        )
        await requester.get_by_label("Data subjects").fill("Enrolled students")
        await requester.get_by_label("Personal data involved").fill(
            "Survey responses, student identifiers, wellbeing scores"
        )
        await requester.get_by_label("Vendors / processors").fill("Acme Analytics Ltd")
        await requester.get_by_label("Timeline").fill("Pilot in October")
        await requester.get_by_label("Known unknowns (one per line)").fill(
            "Hosting location\nSubprocessor list"
        )
        await shot(requester, "dpia-flow-1-request-intake.png")

        await requester.get_by_role("button", name="Review & submit").click()
        await requester.get_by_role("button", name="Submit to DPIA Office").click()
        try:
            await expect(requester.get_by_test_id("dpia-request-status")).to_be_visible(
                timeout=60000
            )
        except AssertionError:
            alerts = requester.get_by_role("alert")
            for i in range(await alerts.count()):
                print("requester alert:", await alerts.nth(i).inner_text())
            raise
        await shot(requester, "dpia-flow-2-request-submitted.png")

        print("== officer: portfolio + inbox + review")
        await officer.goto(f"{BASE}/dpia", wait_until="networkidle")
        await expect(
            officer.get_by_test_id("dpia-incoming-requests").get_by_text(
                "Vendor Wellbeing Analytics"
            )
        ).to_be_visible(timeout=30000)
        await shot(officer, "dpia-flow-3-portfolio-incoming.png")

        await officer.goto(f"{BASE}/inbox", wait_until="networkidle")
        await expect(officer.get_by_text("DPIA request awaiting triage")).to_be_visible(
            timeout=30000
        )
        await shot(officer, "dpia-flow-4-inbox-request.png")

        await officer.get_by_role("link", name="Review request").click()
        await expect(officer.get_by_test_id("dpia-request-detail-card")).to_be_visible(
            timeout=20000
        )
        review_url = officer.url
        await shot(officer, "dpia-flow-5-request-review.png")

        print("== officer: accept + share outreach")
        await officer.get_by_role("button", name="Accept for screening").click()
        await expect(
            officer.get_by_role("button", name="Send outcome to requester")
        ).to_be_visible(timeout=20000)

        await officer.goto(f"{BASE}/dpia/cases/student-success-alert", wait_until="networkidle")
        panel = officer.get_by_test_id("stakeholder-outreach-panel")
        await panel.scroll_into_view_if_needed()
        await officer.get_by_role("button", name="Share questions with a stakeholder").click()
        await officer.get_by_label("Stakeholder team").fill("IT Security")
        await officer.get_by_role("checkbox").first.check()
        await shot(officer, "dpia-flow-6-share-dialog.png")
        await officer.get_by_role("button", name="Create scoped outreach").click()
        link_el = officer.locator("p.break-all")
        await expect(link_el).to_be_visible(timeout=30000)
        respond_path = (await link_el.inner_text()).strip()
        print("respond link:", respond_path)
        await officer.get_by_role("button", name="Done").click()

        print("== contributor: answer scoped questions")
        contributor = await (
            await browser.new_context(viewport={"width": 1440, "height": 900})
        ).new_page()
        await contributor.goto(f"{BASE}{respond_path}", wait_until="networkidle")
        await expect(contributor.get_by_test_id("dpia-respond-card")).to_be_visible(timeout=20000)
        first_question = contributor.get_by_test_id("dpia-respond-card").locator("textarea").first
        await expect(first_question).to_be_visible(timeout=30000)
        await expect(
            contributor.get_by_label("Message the DPIA agent"),
        ).to_be_enabled(timeout=30000)
        await contributor.get_by_label("Your name").fill("Jordan Ali")
        await contributor.get_by_label("Your team").fill("IT Security")
        for textarea in (
            await contributor.get_by_test_id("dpia-respond-card").locator("textarea").all()
        ):
            await textarea.fill(
                "Confirmed with the vendor: the model and primary database "
                "are hosted in London (UK region)."
            )
        await shot(contributor, "dpia-flow-7-respond-form.png")
        await contributor.get_by_role("button", name="Review & submit answers").click()
        await contributor.get_by_role("button", name="Submit answers").click()
        await expect(contributor.get_by_text("Answers submitted")).to_be_visible(timeout=20000)
        await shot(contributor, "dpia-flow-8-respond-submitted.png")

        print("== officer: accept response")
        await officer.reload(wait_until="networkidle")
        panel = officer.get_by_test_id("stakeholder-outreach-panel")
        await panel.scroll_into_view_if_needed()
        await expect(panel.get_by_role("button", name="Accept as recorded answers")).to_be_visible(
            timeout=40000
        )
        await shot(officer, "dpia-flow-9-pending-response.png")
        await panel.get_by_role("button", name="Accept as recorded answers").click()
        await expect(panel.get_by_text("accepted", exact=False).first).to_be_visible(timeout=20000)

        print("== officer: send outcome")
        await officer.goto(review_url, wait_until="load")
        await expect(
            officer.get_by_role("button", name="Send outcome to requester")
        ).to_be_visible(timeout=30000)
        await officer.get_by_role("button", name="Send outcome to requester").click()
        await officer.get_by_label("Reasons (one per line)").fill(
            "Screening indicates a full DPIA is likely before launch; "
            "hosting evidence is now recorded."
        )
        await officer.get_by_label("Condition owner").fill("Procurement")
        await shot(officer, "dpia-flow-10-outcome-dialog.png")
        await officer.get_by_role("button", name="Send outcome").click()
        await expect(officer.get_by_text("Outcome sent")).to_be_visible(timeout=30000)

        print("== requester: outcome + acknowledge")
        await requester.goto(f"{BASE}/dpia/request", wait_until="load")
        await requester.get_by_role("button", name="Open").first.click()
        await expect(requester.get_by_test_id("dpia-outcome-card")).to_be_visible(timeout=40000)
        await shot(requester, "dpia-flow-11-outcome-requester.png")
        await requester.get_by_role("button", name="Acknowledge outcome").click()
        await expect(requester.get_by_text("Acknowledged")).to_be_visible(timeout=20000)
        await shot(requester, "dpia-flow-12-outcome-acknowledged.png")

        await browser.close()
        print("QA COMPLETE")


asyncio.run(main())
