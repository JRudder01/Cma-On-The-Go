"""End-to-end Streamlit browser smoke test. No production database secrets used."""
from __future__ import annotations
import re
from playwright.sync_api import sync_playwright

APP_URL = "http://127.0.0.1:8501"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(APP_URL, wait_until="domcontentloaded", timeout=30000)

        page.get_by_text("CMA On The Go", exact=True).first.wait_for(timeout=25000)
        page.get_by_label("Access passphrase").fill("CI-MOCK-PASSWORD-ONLY")
        page.get_by_role("button", name="Unlock").click()

        page.get_by_text("Quick Market Scan").wait_for(timeout=25000)
        page.get_by_text("ZIP code", exact=True).first.click(timeout=15000)
        page.get_by_role("textbox", name="ZIP code").wait_for(timeout=15000)
        page.get_by_text("City + state", exact=True).first.click(timeout=15000)
        page.get_by_role("textbox", name="City", exact=True).wait_for(timeout=15000)

        page.get_by_text("📁 Projects", exact=True).first.click()
        page.get_by_text("Saved desktop projects").wait_for(timeout=15000)
        page.get_by_text("🏘️ Communities", exact=True).first.click()
        page.get_by_text("Community library").wait_for(timeout=15000)
        page.get_by_text("📍 Quick Scan", exact=True).first.click()
        page.get_by_text("Quick Market Scan").wait_for(timeout=15000)

        assert not errors, "Browser JavaScript errors: " + repr(errors)
        print("PASS: initial load, password, two geography changes, Projects, Communities, Quick Scan")
        browser.close()


if __name__ == "__main__":
    main()
