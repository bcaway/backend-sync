#!/usr/bin/env python3
"""
Session Capture Helper for BCAway Backend Sync.

Launches a headful Chromium browser via Playwright to allow you to log in
to your @bergen.org Google Workspace account. Once logged in, it exports
the session storage state (cookies, local storage, tokens) to `chrome_state.json`.

You can then copy the contents of `chrome_state.json` directly into your
GitHub repository secrets as `CHROME_STATE_JSON`.
"""

import json
import os
import sys

DEFAULT_TARGET_URL = (
    "https://docs.google.com/document/d/e/2PACX-1vRkhySmwAiTtY88tcshckpV4F0vRrULccaGrYl_Sf2ubWpyyXA4l8c-KAOuMzSwFe-qyAQhLqXzVsbA/pub"
)
OUTPUT_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_state.json")


def main():
    print("=" * 80)
    print("  BCAway Session Capture - Google Workspace Login")
    print("=" * 80)
    print("\nThis script will open a Chromium browser window.")
    print("1. Log in with your authorized @bergen.org Google Workspace account.")
    print("2. Complete any 2-factor authentication / approval prompts.")
    print("3. Ensure you can view the BCA Class Cancellation document in the browser.")
    print("4. Return to this terminal and press ENTER when finished.\n")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[ERROR] Playwright is not installed.")
        print("Please run:")
        print("    pip install -r requirements.txt")
        print("    playwright install chromium\n")
        sys.exit(1)

    target_url = os.environ.get("GOOGLE_DOC_URL", DEFAULT_TARGET_URL)

    with sync_playwright() as p:
        print("[INFO] Launching Chromium browser (headed mode)...")
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        print(f"[INFO] Navigating to: {target_url}")
        try:
            page.goto(target_url, wait_until="domcontentloaded")
        except Exception as e:
            print(f"[WARN] Initial navigation notice: {e}")
            print("[INFO] Opening Google accounts login page instead...")
            page.goto("https://accounts.google.com/ServiceLogin")

        print("\n" + "-" * 80)
        input(">>> Once you are fully logged in and can view the document, press [ENTER] here... ")
        print("-" * 80 + "\n")

        # Save storage state
        print(f"[INFO] Saving session storage state to: {OUTPUT_STATE_PATH}")
        context.storage_state(path=OUTPUT_STATE_PATH)
        browser.close()

    if not os.path.exists(OUTPUT_STATE_PATH):
        print("[ERROR] Failed to create chrome_state.json.")
        sys.exit(1)

    file_size = os.path.getsize(OUTPUT_STATE_PATH)
    print(f"\n[SUCCESS] Saved {OUTPUT_STATE_PATH} ({file_size} bytes).")

    # Verify JSON content
    try:
        with open(OUTPUT_STATE_PATH, "r", encoding="utf-8") as f:
            state_data = json.load(f)
        cookies_count = len(state_data.get("cookies", []))
        origins_count = len(state_data.get("origins", []))
        print(f"[INFO] Captured {cookies_count} cookie(s) and {origins_count} origin storage item(s).")
    except Exception as e:
        print(f"[WARN] Could not verify JSON structure: {e}")

    print("\n" + "=" * 80)
    print("  NEXT STEPS (Add to GitHub Secrets)")
    print("=" * 80)
    print("1. Go to your GitHub repository -> Settings -> Secrets and variables -> Actions.")
    print("2. Create a new repository secret named:")
    print("     CHROME_STATE_JSON")
    print("3. Paste the entire content of chrome_state.json as the secret value.")
    print("4. Done! The GitHub Actions workflow will authenticate seamlessly.")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
