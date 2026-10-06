#!/usr/bin/env python3
"""
BCAway Teacher Absence Synchronization Engine
Monitors the BCA Class Cancellation Google Doc via headless Playwright,
parses the attendance schedule per BCAway standards, detects changes via
content fingerprinting, and synchronizes live state with Supabase.

Designed for GitHub Actions execution:
- Peak Window (11:00 UTC - 13:30 UTC): Continuous internal 60s polling loop.
- Off-Peak Window: Periodic single-shot execution every 10 minutes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
    EASTERN_TZ = ZoneInfo("America/New_York")
except ImportError:
    import pytz  # type: ignore
    EASTERN_TZ = pytz.timezone("America/New_York")

import requests
from bs4 import BeautifulSoup

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Configuration & Defaults
# ---------------------------------------------------------------------------

DEFAULT_DOC_URL = (
    "https://docs.google.com/document/d/e/2PACX-1vRkhySmwAiTtY88tcshckpV4F0vRrULccaGrYl_Sf2ubWpyyXA4l8c-KAOuMzSwFe-qyAQhLqXzVsbA/pub"
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHROME_STATE_PATH = os.path.join(SCRIPT_DIR, "chrome_state.json")
DEBUG_DIR = os.path.join(SCRIPT_DIR, ".debug")

MONTH_NAME_TO_INT = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

ALL_PERIODS = ["igs", "1", "2", "3", "4", "5", "6", "7", "8", "9"]


# ---------------------------------------------------------------------------
# Logging Utilities
# ---------------------------------------------------------------------------

def log(msg: str, level: str = "INFO") -> None:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    now_et = datetime.now(EASTERN_TZ).strftime("%H:%M:%S ET")
    print(f"[{now_utc} | {now_et}] [{level.upper()}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Period & Attendance Normalization (BCAway Standard)
# ---------------------------------------------------------------------------

def normalize_periods(raw_value: Optional[str]) -> str:
    """
    Normalizes period descriptions into the BCAway canonical format:
    - 'all' / 'all day' -> 'igs, 1, 2, 3, 4, 5, 6, 7, 8, 9'
    - Ranges ('1-3') -> expanded to '1, 2, 3'
    - Standalone numbers ('5') -> '5'
    - 'igs' -> 'igs'
    - Ordered: 'igs' first (if present), followed by numerical periods 1-9.
    """
    if not raw_value:
        return ""

    text = (
        str(raw_value)
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u00a0", " ")
        .strip()
    )

    if not text:
        return ""

    # Rule 1: 'All' / 'All day'
    if re.search(r"\ball\b", text, flags=re.IGNORECASE):
        return ", ".join(ALL_PERIODS)

    # Pre-normalize common connectors and words
    text = re.sub(r"\b(?:through|thru|to)\b", "-", text, flags=re.IGNORECASE)
    text = re.sub(r"&|\band\b|\+|\/|;", ",", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:periods?|mods?|p\.?)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*-\s*", "-", text)

    tokens = [t.strip().lower() for t in text.split(",") if t.strip()]
    periods: set[str] = set()

    for token in tokens:
        if token == "igs":
            periods.add("igs")
            continue

        # Numerical ranges: e.g. 1-4, 7-9
        range_match = re.match(r"^([1-9])-([1-9])$", token)
        if range_match:
            start, end = int(range_match.group(1)), int(range_match.group(2))
            if start <= end:
                for p in range(start, end + 1):
                    periods.add(str(p))
            continue

        # Single digit: 1-9
        if re.match(r"^[1-9]$", token):
            periods.add(token)
            continue

        # Standalone digits fallback inside token
        digits = re.findall(r"\b[1-9]\b", token)
        for d in digits:
            periods.add(d)

    if not periods:
        return ""

    # Sort: 'igs' first, then numerical periods 1 through 9
    sorted_periods: List[str] = []
    if "igs" in periods:
        sorted_periods.append("igs")

    for p in range(1, 10):
        if str(p) in periods:
            sorted_periods.append(str(p))

    return ", ".join(sorted_periods)


# ---------------------------------------------------------------------------
# Date Parsing
# ---------------------------------------------------------------------------

def parse_header_date(text: str) -> Optional[str]:
    """
    Extracts the absence date from document header text:
    'BCA Class Cancellation List \n {Month} {Day}, {YYYY}'
    Returns ISO date format 'YYYY-MM-DD' or None if not found.
    """
    clean_text = (
        text.replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u00a0", " ")
    )

    # Primary header regex
    header_pattern = re.compile(
        r"BCA\s+Class\s+Cancellation\s+List[\s\S]*?([a-zA-Z]+)\s+(\d{1,2}),?\s+(\d{4})",
        re.IGNORECASE,
    )
    match = header_pattern.search(clean_text)

    # Fallback to Month Day, Year pattern
    if not match:
        fallback_pattern = re.compile(r"([a-zA-Z]+)\s+(\d{1,2}),?\s+(\d{4})", re.IGNORECASE)
        match = fallback_pattern.search(clean_text)

    if not match:
        return None

    month_raw = match.group(1).lower()
    month_int = MONTH_NAME_TO_INT.get(month_raw)
    if not month_int:
        return None

    try:
        day_int = int(match.group(2))
        year_int = int(match.group(3))
        if 1 <= day_int <= 31 and 2020 <= year_int <= 2100:
            return f"{year_int:04d}-{month_int:02d}-{day_int:02d}"
    except ValueError:
        return None

    return None


def get_today_in_new_york() -> str:
    """Returns today's date in America/New_York formatted as YYYY-MM-DD."""
    return datetime.now(EASTERN_TZ).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# HTML Parsing & Table Extraction
# ---------------------------------------------------------------------------

def parse_absences_from_html(html: str) -> Tuple[str, List[Dict[str, str]]]:
    """
    Parses cancellation date and teacher absences table from the document HTML.
    Returns (date_str, absences_list).
    """
    soup = BeautifulSoup(html, "html.parser")
    full_text = soup.get_text(separator=" ")

    # 1. Parse date
    date_str = parse_header_date(full_text)
    if not date_str:
        today = get_today_in_new_york()
        log(f"Header date not found in document text; defaulting to today ET ({today})", "WARN")
        date_str = today

    # 2. Locate tables
    tables = soup.find_all("table")
    if not tables:
        log("No <table> elements discovered in document HTML.", "WARN")
        return date_str, []

    # Choose candidate table (either table with Teacher/Period header or the first non-empty table)
    target_table = None
    for tbl in tables:
        tbl_text = tbl.get_text().lower()
        if "teacher" in tbl_text or "period" in tbl_text or "absence" in tbl_text:
            target_table = tbl
            break

    if target_table is None:
        target_table = tables[0]

    rows = target_table.find_all("tr")
    raw_absences: List[Dict[str, str]] = []

    for row in rows:
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue

        col1_text = cells[0].get_text().strip()
        col2_text = cells[1].get_text().strip()

        # Skip table headers
        col1_lower = col1_text.lower()
        if not col1_text or col1_lower in ("teacher", "faculty", "name", "staff"):
            continue

        periods_impacted = normalize_periods(col2_text)
        if not periods_impacted:
            continue

        raw_absences.append({
            "teacher": col1_text,
            "periods_impacted": periods_impacted,
        })

    # Sort alphabetically by teacher for deterministic JSON snapshot hashing
    raw_absences.sort(key=lambda a: a["teacher"].lower())
    return date_str, raw_absences


# ---------------------------------------------------------------------------
# Hashing & State Check
# ---------------------------------------------------------------------------

def compute_snapshot_hash(date_str: str, absences: List[Dict[str, str]]) -> str:
    """Generates a deterministic SHA256 hex digest for the date + absences dataset."""
    canonical_payload = {
        "date": date_str,
        "absences": [
            {
                "teacher": a["teacher"].strip(),
                "periods_impacted": a["periods_impacted"].strip(),
            }
            for a in absences
        ],
    }
    encoded = json.dumps(canonical_payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# ---------------------------------------------------------------------------
# Web Scraping via Playwright
# ---------------------------------------------------------------------------

def save_debug_artifacts(html: Optional[str], metadata: Dict[str, Any]) -> None:
    """Saves HTML snapshot and metadata JSON for GitHub Actions artifact inspection."""
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        if html:
            html_path = os.path.join(DEBUG_DIR, "debug_last_page.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
        meta_path = os.path.join(DEBUG_DIR, "debug_last_response.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
        log(f"Debug artifacts saved to {DEBUG_DIR}", "DEBUG")
    except Exception as e:
        log(f"Failed to write debug artifacts: {e}", "WARN")


def is_auth_wall(html: str) -> bool:
    """Detects if the response is Google's OAuth / ServiceLogin wall instead of the document."""
    lowered = html.lower()
    if "<table" in lowered and "cancellation" in lowered:
        return False
    indicators = [
        "accounts.google.com/servicelogin",
        "accounts.google.com/v3/signin",
        "servicelogin?service=wise",
        "action=\"/signin/v2/challenge\"",
    ]
    return any(indicator in lowered for indicator in indicators)


def scrape_document_html(doc_url: str, chrome_state_file: str) -> Optional[str]:
    """
    Fetches the published Google Doc using headless Playwright with session cookies.
    Returns page HTML string or None on failure.
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

    if not os.path.exists(chrome_state_file):
        # Check if environment variable contains the JSON
        env_state = os.environ.get("CHROME_STATE_JSON")
        if env_state and env_state.strip():
            log(f"Writing CHROME_STATE_JSON from environment into {chrome_state_file}...", "INFO")
            with open(chrome_state_file, "w", encoding="utf-8") as f:
                f.write(env_state.strip())
        else:
            log(f"Session state file '{chrome_state_file}' not found and CHROME_STATE_JSON secret not set.", "ERROR")
            log("Run capture_session.py locally to generate cookies and set the secret.", "ERROR")
            return None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            context = browser.new_context(
                storage_state=chrome_state_file,
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            log(f"Navigating to document: {doc_url}")
            try:
                page.goto(doc_url, timeout=15000, wait_until="domcontentloaded")
            except PlaywrightTimeoutError as te:
                log(f"Navigation timed out: {te}", "ERROR")
                save_debug_artifacts(None, {"error": "navigation_timeout", "details": str(te)})
                return None

            # Small wait for dynamic rendering
            try:
                page.wait_for_selector("table", timeout=3000)
            except PlaywrightTimeoutError:
                pass

            html = page.content()

            if is_auth_wall(html):
                log("=" * 70, "ERROR")
                log("Authentication wall detected! Google Workspace session has expired.", "ERROR")
                log("Please re-run 'capture_session.py' and update CHROME_STATE_JSON in GitHub Secrets.", "ERROR")
                log("=" * 70, "ERROR")
                save_debug_artifacts(html, {"error": "auth_wall_detected", "url": doc_url})
                return None

            return html

        finally:
            browser.close()


# ---------------------------------------------------------------------------
# Supabase Synchronization
# ---------------------------------------------------------------------------

def push_to_supabase(
    supabase_url: str,
    sync_secret: str,
    date_str: str,
    absences: List[Dict[str, str]],
) -> Tuple[bool, Dict[str, Any]]:
    """
    Submits normalized absences payload to Supabase Edge Function 'sync-absences'.
    Returns (success, response_json).
    """
    base_url = supabase_url.rstrip("/")
    endpoint = f"{base_url}/functions/v1/sync-absences"

    headers = {
        "Authorization": f"Bearer {sync_secret}",
        "Content-Type": "application/json",
    }
    payload = {
        "date": date_str,
        "absences": absences,
    }

    log(f"Submitting {len(absences)} record(s) for {date_str} to Supabase ({endpoint})...")

    try:
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=20)
    except requests.RequestException as e:
        log(f"Network request to Supabase failed: {e}", "ERROR")
        return False, {"error": str(e)}

    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}

    if resp.status_code >= 200 and resp.status_code < 300:
        log(f"Supabase sync accepted ({resp.status_code}): {body}", "INFO")
        return True, body
    else:
        log(f"Supabase sync rejected ({resp.status_code}): {body}", "ERROR")
        return False, body


# ---------------------------------------------------------------------------
# Scrape & Sync Orchestrator
# ---------------------------------------------------------------------------

class SyncOrchestrator:
    def __init__(
        self,
        doc_url: str,
        supabase_url: str,
        sync_secret: str,
        chrome_state_file: str,
    ):
        self.doc_url = doc_url
        self.supabase_url = supabase_url
        self.sync_secret = sync_secret
        self.chrome_state_file = chrome_state_file
        self.last_synced_hash: Optional[str] = None

    def execute_cycle(self, force: bool = False) -> bool:
        """
        Executes one full scrape-diff-sync cycle.
        Returns True if successful, False on error.
        """
        html = scrape_document_html(self.doc_url, self.chrome_state_file)
        if not html:
            return False

        date_str, absences = parse_absences_from_html(html)
        current_hash = compute_snapshot_hash(date_str, absences)

        log(f"Parsed {len(absences)} absence(s) for {date_str} (Hash: {current_hash[:12]}...)")

        if not force and self.last_synced_hash == current_hash:
            log("No changes detected in absences snapshot since last cycle. Skipping push.", "INFO")
            return True

        if self.last_synced_hash is None:
            log("Initial cycle or hash update. Pushing to Supabase...")
        else:
            log(f"Absence changes detected! (Old: {self.last_synced_hash[:10]}..., New: {current_hash[:10]}...)")

        success, _ = push_to_supabase(
            self.supabase_url,
            self.sync_secret,
            date_str,
            absences,
        )

        if success:
            self.last_synced_hash = current_hash
            return True
        return False


# ---------------------------------------------------------------------------
# Window Determination & Main Execution
# ---------------------------------------------------------------------------

def is_within_peak_window(utc_dt: datetime) -> bool:
    """
    Peak window is defined as 11:00 AM UTC to 1:30 PM UTC (13:30 UTC),
    corresponding to 7:00 AM - 9:30 AM EDT on weekdays.
    """
    # 0 = Monday, 4 = Friday
    if utc_dt.weekday() > 4:
        return False

    current_minutes = utc_dt.hour * 60 + utc_dt.minute
    start_minutes = 11 * 60       # 11:00 UTC
    end_minutes = 13 * 60 + 30    # 13:30 UTC

    return start_minutes <= current_minutes < end_minutes


def run_continuous_monitor(
    orchestrator: SyncOrchestrator,
    interval_seconds: int = 60,
    stop_at_utc_hour: int = 13,
    stop_at_utc_minute: int = 30,
) -> None:
    """
    Continuous polling loop for the morning rush window.
    Runs every interval_seconds until target UTC time is reached.
    """
    log("=" * 80)
    log(f"STARTING PEAK CONTINUOUS MONITOR (Interval: {interval_seconds}s, End: {stop_at_utc_hour:02d}:{stop_at_utc_minute:02d} UTC)")
    log("=" * 80)

    while True:
        now_utc = datetime.now(timezone.utc)
        current_minutes = now_utc.hour * 60 + now_utc.minute
        target_minutes = stop_at_utc_hour * 60 + stop_at_utc_minute

        if current_minutes >= target_minutes:
            log(f"Current UTC time ({now_utc.strftime('%H:%M')}) has reached peak window end ({stop_at_utc_hour:02d}:{stop_at_utc_minute:02d}).")
            log("Peak monitor completed cleanly. Exiting.")
            break

        cycle_start = time.time()
        try:
            orchestrator.execute_cycle()
        except Exception as e:
            log(f"Cycle encountered an unhandled exception: {e}", "ERROR")

        elapsed = time.time() - cycle_start
        sleep_duration = max(1.0, float(interval_seconds) - elapsed)

        # Check if sleeping would overshoot window
        seconds_until_end = (target_minutes - (datetime.now(timezone.utc).hour * 60 + datetime.now(timezone.utc).minute)) * 60
        if seconds_until_end <= 0:
            break

        actual_sleep = min(sleep_duration, max(1.0, float(seconds_until_end)))
        time.sleep(actual_sleep)


def main():
    parser = argparse.ArgumentParser(description="BCAway Absence Sync Monitor")
    parser.add_argument("--once", action="store_true", help="Run a single scrape and sync cycle then exit.")
    parser.add_argument("--continuous", action="store_true", help="Force continuous monitoring loop.")
    parser.add_argument("--interval", type=int, default=60, help="Loop interval in seconds (default: 60).")
    parser.add_argument("--force", action="store_true", help="Bypass hash check and force push to Supabase.")
    args = parser.parse_args()

    # Load and validate settings
    raw_doc_url = os.environ.get("GOOGLE_DOC_URL")
    doc_url = raw_doc_url.strip() if raw_doc_url and raw_doc_url.strip() else DEFAULT_DOC_URL
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    sync_secret = os.environ.get("SYNC_SECRET", "").strip()
    chrome_state = os.environ.get("CHROME_STATE_PATH", CHROME_STATE_PATH)

    if not supabase_url:
        log("Missing required environment variable: SUPABASE_URL", "ERROR")
        sys.exit(1)

    if not sync_secret:
        log("Missing required environment variable: SYNC_SECRET", "ERROR")
        sys.exit(1)

    orchestrator = SyncOrchestrator(
        doc_url=doc_url,
        supabase_url=supabase_url,
        sync_secret=sync_secret,
        chrome_state_file=chrome_state,
    )

    now_utc = datetime.now(timezone.utc)
    in_peak = is_within_peak_window(now_utc)

    # Determine execution mode
    if args.once:
        log("Running in explicit single-shot mode (--once)...")
        success = orchestrator.execute_cycle(force=args.force)
        sys.exit(0 if success else 1)

    if args.continuous:
        log("Running in explicit continuous mode (--continuous)...")
        run_continuous_monitor(orchestrator, interval_seconds=args.interval)
        sys.exit(0)

    # Auto-detection mode:
    # If currently in peak window -> run continuous loop until 13:30 UTC
    # Otherwise -> run single-shot cycle
    if in_peak:
        log(f"Auto-mode: Current time ({now_utc.strftime('%H:%M')} UTC) is inside peak window (11:00-13:30 UTC).")
        run_continuous_monitor(orchestrator, interval_seconds=args.interval)
    else:
        log(f"Auto-mode: Current time ({now_utc.strftime('%H:%M')} UTC) is outside peak window. Executing single cycle.")
        success = orchestrator.execute_cycle(force=args.force)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
