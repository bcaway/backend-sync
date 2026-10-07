# backend-sync

> **BCAway Backend Sync — Automated Teacher Absence Ingestion Engine**  
> High-performance Python & GitHub Actions synchronization pipeline that monitors the BCA Class Cancellation Google Doc, parses attendance schedules to BCAway canonical standards, fingerprints changes with SHA-256 hashing, and updates Supabase in real-time.

---

## 🏛 System Architecture

```mermaid
flowchart TD
    Doc["BCA Class Cancellation Google Doc<br/>(Restricted @bergen.org Workspace)"]
    Playwright["Headless Playwright Chromium<br/>(Authenticated via CHROME_STATE_JSON)"]
    Parser["BCAway Parsing & Normalization Engine<br/>(sync_monitor.py)"]
    Hasher["SHA-256 Fingerprinting Cache"]
    EdgeFunction["Supabase Edge Function<br/>(POST /functions/v1/sync-absences)"]
    DB[("Supabase DB<br/>(teacher_absences)")]
    Worker["Cloudflare Worker<br/>(POST /notify-absence)"]
    App["BCAway Mobile App<br/>(Realtime Subscription)"]

    Doc -->|"Scrapes DOM & Tables"| Playwright
    Playwright --> Parser
    Parser --> Hasher
    Hasher -->|"Hash changed / First run"| EdgeFunction
    Hasher -.->|"Identical hash"| Skip["Skip network call (No-op)"]
    EdgeFunction -->|"Atomic Live Snapshot"| DB
    EdgeFunction -->|"Diff Events (insert/update/delete)"| Worker
    DB --> App
```

---

## ⚡ Scheduling Strategy & GitHub Actions Optimization

GitHub Actions has a 5-minute minimum cron resolution and VM boot overhead (30–60s per runner). Spawning 150 separate VMs across the morning rush would quickly exhaust GitHub Actions quota.

To achieve **true 1-minute peak resolution** while keeping off-peak jobs lightweight, scheduling is split across separate workflows:

### 1. Morning Peak Workflow (`teacher_sync_peak.yml`)
* **Local School Time:** 7:00 AM – 9:30 AM EDT *(or 6:00 AM – 8:30 AM EST)*.
* **Schedule:** Starts once at **11:00 UTC** on weekdays (`cron: '0 11 * * 1-5'`).
* **Execution:** Runs `sync_monitor.py --continuous --interval 60`.
* **Stop Condition:** The monitor exits when UTC reaches **13:30**.
* **Concurrency:** Isolated to a peak-specific group so off-peak runs do not cancel it.

### 2. Off-Peak Workflow (`teacher_sync_offpeak.yml`)
* **Schedule:** Every **10 minutes** outside the weekday peak window, and every 10 minutes all weekend.
* **Execution:** Always runs single-shot mode (`sync_monitor.py --once`).
* **Runtime:** Designed to finish quickly (~seconds) and terminate.
* **Concurrency:** Uses its own off-peak group so it cannot interfere with peak continuous runs.

### 3. Health Check Workflow (`teacher_sync_healthcheck.yml`)
* Runs every 30 minutes and inspects recent scheduled workflow history.
* Fails with an explicit alert when expected cadence is missing (e.g., no active peak run during peak, or missing recent off-peak runs).

> If exact minute-by-minute timing is business-critical, use an external scheduler (serverless cron / hosted scheduler) to trigger `workflow_dispatch` and use GitHub Actions as the execution layer.

---

## 🚀 Setup & Deployment Guide

### Step 1: Capture Google Workspace Session Cookies

Because the official BCA Class Cancellation Google Doc is restricted to `@bergen.org` accounts, Playwright uses exported browser session cookies to bypass the Google login wall.

Start a virtual Python Enviornment:
```bash
# 1. Clone or navigate to the repository
cd backend-sync

# 2. Create a virtual enviornment
python3 -m venv .venv

# 3. Activate the virtual enviornment
source .venv/bin/activate
```

Run the interactive session capture utility locally on your computer:

```bash
# 1. Install dependencies
python3 -m pip install -r requirements.txt
playwright install chromium

# 2. Launch the session capture helper
python3 capture_session.py
```

1. A Chromium browser window will open.
2. Sign in with your authorized `@bergen.org` Google Workspace account and complete any two-factor authentication prompts.
3. Once the document renders in the browser, return to your terminal and press **Enter**.
4. The script will export your session to `chrome_state.json`.

Deactivate the venv afterwards:
```bash
deactivate
```
---

### Step 2: Configure GitHub Repository Secrets

Go to your repository on GitHub:  
**Settings** $\rightarrow$ **Secrets and variables** $\rightarrow$ **Actions** $\rightarrow$ **New repository secret**.

Add the following secrets:

| Secret Name | Required | Description | Example / Source |
| :--- | :---: | :--- | :--- |
| `CHROME_STATE_JSON` | **Yes** | Entire content of `chrome_state.json` exported in Step 1. | `{"cookies": [...], "origins": [...]}` |
| `SUPABASE_URL` | **Yes** | Your Supabase project URL. | `https://xyzcompany.supabase.co` |
| `SYNC_SECRET` | **Yes** | Bearer secret for `/functions/v1/sync-absences`. | Configured in Supabase Edge Functions. Created with: `openssl rand -base64 64`|
| `GOOGLE_DOC_URL` | *Optional* | Published Google Doc URL. | Defaults to BCA's published cancellation doc if omitted. |

---

### Step 3: Test & Verify

1. In GitHub, navigate to the **Actions** tab.
2. Select **BCA Teacher Absence Sync (Off-Peak)** from the left sidebar.
3. Click **Run workflow**:
   * Force: `true` (forces a push to Supabase to verify connectivity).
4. Check the workflow logs. You should see:
   ```text
   Parsed N absence(s) for YYYY-MM-DD
   Submitting N record(s) to Supabase (/functions/v1/sync-absences)...
   Supabase sync accepted (200)
   ```
5. Check your BCAway mobile app — today's absences will appear instantly via Supabase Realtime!

---

## 🛠 Local Development & Testing

You can run and test the engine locally using a `.env` file:

```bash
# 1. Copy sample environment file
cp .env.example .env

# 2. Fill in your credentials in .env
# SUPABASE_URL=...
# SYNC_SECRET=...

# 3. Run a single-shot test
python3 sync_monitor.py --once

# 4. Force push regardless of hash
python3 sync_monitor.py --once --force

# 5. Run continuous monitor locally (e.g. 10s intervals for testing)
python3 sync_monitor.py --continuous --interval 10
```

---

## 📋 Parsing Rules & BCAway Normalization

The parsing engine mirrors BCAway's period and teacher normalization rules:

1. **Header Date Parsing:** Matches `BCA Class Cancellation List \n {Month} {Day}, {YYYY}` and converts to ISO `YYYY-MM-DD`. Falls back to current date in `America/New_York` if the header is missing.
2. **Table Parsing:** Discards table header rows (`Teacher`, `Period`, etc.), maps Column 0 to teacher name, and Column 1 to periods impacted.
3. **Period Normalization:**
   * `"all"` / `"all day"` $\rightarrow$ `"igs, 1, 2, 3, 4, 5, 6, 7, 8, 9"`
   * Ranges (`"1-3"`) $\rightarrow$ `"1, 2, 3"`
   * Standalone periods (`"5"`) $\rightarrow$ `"5"`
   * `"igs"` $\rightarrow$ Always ordered first
   * Connectors (`"through"`, `"to"`, `"&"`, `"+"`) are normalized to standard delimiters.
4. **Deterministic Hashing:** Absences are sorted alphabetically by teacher and hashed using SHA-256. Redundant Supabase calls and duplicate push notifications are skipped if the content hasn't changed.

---

## 🔍 Debugging & Troubleshooting

### Expired Session Cookies
If Google invalidates the session or cookies expire:
* The workflow will detect the Google ServiceLogin redirect, log `Authentication wall detected! Google Workspace session has expired.`, and save debug files to `.debug/`.
* The GitHub Actions workflow automatically uploads `debug_last_page.html` as a downloadable artifact.
* **Fix:** Re-run `python3 capture_session.py` on your computer and update the `CHROME_STATE_JSON` GitHub secret.

### Inspecting Artifacts
Whenever a workflow run fails or encounters an unexpected page structure, download the `debug-artifacts` zip from the GitHub Actions run summary page to inspect the exact HTML received by Chromium.
