# ScholarOS Automated Ingest Pipeline

A compliant, fact-only scholarship ingestion pipeline that runs automatically on GitHub Actions every Sunday at **6:00 AM CST** (`12:00 UTC`), writes clean deduplicated rows into the `Ingest_Staging` tab of **ScholarOS — Shared Scholarship Hub**, and hands off directly to your Sunday **8:00 AM CST** Gemini `/scholarshipsearch` scheduled run.

## Compliance status
- **Watchlist (main source)**: a curated list of official sponsor pages in `config.yaml` (`sources.watchlist.pages`). Each page was checked: robots.txt allows it and the site's Terms of Use have no clause against automated access. The pipeline makes one request per page per week and stores only facts (title, sponsor, deadline, amount, URL). To add a page, check its Terms first and run `python scripts/check_source.py URL`. Every audited site and its verdict is in `docs/SOURCES.md`.
- **CareerOneStop (U.S. Dept. of Labor API)**: not accepting new API registrations (as of Nov 2026). It skips itself when no credentials are set.
- **Bold.org, Unigo, Niche**: **disabled**. Unigo's Terms of Use prohibit "any automatic device, process or means" to access or copy the site, and Bold.org (bot checkpoint, HTTP 429) and Niche (HTTP 403) actively block bots. Do not re-enable them without written permission.
- The HTTP client obeys robots.txt (including `Crawl-delay`). It treats an unreachable or access-restricted robots.txt as "disallow", and it stops contacting a domain for the rest of the run after a 403/429.
- See `CLAUDE.md` for the rules any AI assistant must follow in this repo.

## 5-Minute Cloud Setup (GitHub Actions — Free Tier)

### Step 1: Create a Google Service Account (2 minutes)
1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project (e.g., `scholaros-ingest`).
2. Enable the **Google Sheets API** and **Google Drive API**.
3. Go to **IAM & Admin → Service Accounts → Create Service Account**.
4. Open the created service account → **Keys → Add Key → Create new key (JSON)**. Download the `.json` file.
5. Copy the service account's email address (looks like `...@...iam.gserviceaccount.com`), open your **ScholarOS — Shared Scholarship Hub** Google Sheet, click **Share**, and give that email **Editor** access.

### Step 2: GitHub Repo
The code lives at `github.com/Senai-sorsa/scholaros-ingest-pipeline` (public; no secrets are stored in the code).

### Step 3: Add GitHub Repository Secrets (1 minute)
In your GitHub repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add:
- `SCHOLAROS_SHEET_ID`: The ID from your ScholarOS Google Sheet URL (`https://docs.google.com/spreadsheets/d/<THIS_PART>/edit`).
- `GOOGLE_SERVICE_ACCOUNT_JSON`: The entire raw contents of the service account `.json` key file.
- *(Optional, only if you already have them)* `CAREERONESTOP_USER_ID` and `CAREERONESTOP_API_TOKEN`. CareerOneStop is not accepting new registrations right now.

### Step 4: Test It Immediately
Go to the **Actions** tab in GitHub → **Weekly ScholarOS Ingest Pipeline** → **Run workflow**. Check the `Ingest_Staging` tab in ScholarOS to verify new rows appear.

## Local CLI Usage
```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in .env, then run:
python ingest.py --all --dry-run --export-xlsx
```
