# Learn With Communities Campaign Tracker

A local terminal app that checks whether students have earned the required Google Skills certifications
shown on their **public** profiles, keeps a verification history in SQLite, and exports CSV reports.

## Features
- Import profile URLs from CSV (validated, normalised, duplicates detected).
- Verify all students, only incomplete ones, or retry only retryable failures.
- Conservative, configurable rate limiting with retries and 429 handling.
- Six distinct statuses; unreadable profiles are never counted as "incomplete".
- Append-only verification history.
- Textual terminal UI: dashboard, students, courses, failed records, export, settings, diagnostics.
- Five CSV reports.

## Architecture
`ui` (Textual screens) → `services` (import, verification, evaluator, reports, exporter) →
`scraper` (fetcher, throttle, retry, parser) and `database` (SQLAlchemy repositories).
Screens never touch the database directly; they call the service layer.

## Folder structure
```
campaign/   courses.json (required courses) + loader/schemas
core/       settings, constants, exceptions, logging
database/   models, repositories, session
scraper/    fetcher, controlled_fetcher, throttle, retry, classifier, parser/
services/   csv_import, verification_service, evaluator, reports, exporter, bootstrap
ui/         app, screens/, widgets/, styles/
tests/      pytest suite (no live network)
data/       tracker.db, tracker.log, exports/   (git-ignored)
main.py     entry point
```

## Dependencies
Python 3.11+, Textual, Rich, SQLAlchemy 2.x, HTTPX, BeautifulSoup4, Pydantic, pydantic-settings,
pytest, pytest-asyncio (see `requirements.txt`).

## Installation (Windows / PowerShell)
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # optional
```
macOS/Linux: `source .venv/bin/activate`.

## Running
```powershell
python main.py
```
Keys: `1`… dashboard and other screens via the sidebar, `e` export, `s` settings, `r` refresh,
`F2` diagnostics, `q` quit.

## CSV input format
First line must be the header `Google Skills Profile URL`, then one public profile URL per row:
```
Google Skills Profile URL
https://www.skills.google/public_profiles/<profile-id>
```
UTF-8; blank rows are skipped; invalid URLs are reported with reasons; duplicates (after safe
normalisation) are stored once.

## Required campaign certifications
Defined in `campaign/courses.json` (name + course template ID). Edit that file to change the
campaign; history is kept, dropped courses are deactivated, never deleted.

## Verification modes
- **All** – every registered student (re-checks completed ones too).
- **Incomplete only** – students whose last read was INCOMPLETE.
- **Retry failures** – only retryable failures (e.g. timeouts, server errors).
A preview with exclusions is shown and must be confirmed before any request is made.

## Verification statuses
`COMPLETED`, `INCOMPLETE` (profile read, courses missing), `PRIVATE_INACCESSIBLE`, `NOT_FOUND`,
`FAILED` (network/server/timeout/parse error), `NEEDS_REVIEW` (ambiguous). Only COMPLETED and
INCOMPLETE mean the profile was actually read.

## Rate limiting
Defaults: 2 s between request starts, 1 concurrent request, exponential backoff with jitter,
`Retry-After` honoured on HTTP 429 (one retry, capped pause). All values are `CT_*` settings
(`CT_REQUEST_DELAY_SECONDS`, `CT_MAX_CONCURRENCY`, `CT_MAX_RETRIES`, …). These are our own conservative
choices, not official Google limits. The app only reads public pages and does not bypass login or
anti-bot protections: a private profile is recorded as such.

## Database storage
`data/tracker.db` (SQLite, local). Stores profile URLs, verification records and per-course results.
No names, e-mails or page contents are stored. Logs avoid full URLs.

## Report exports
Export screen (`e`) → "Export all reports", or call `ReportExporter` from code. Output folder:
`data/exports` (`CT_EXPORT_DIR` to change). Files are never overwritten (`name_1.csv`, …).

| File | Contents |
|---|---|
| `student_summary` | Student ID, URL, status, completed, missing, %, last verified |
| `course_report` | Course, template ID, completed, missing, % (evaluated students only) |
| `missing_certifications` | URL, missing course names, timestamp |
| `failed_profiles` | URL, classification, HTTP status, diagnostic, last attempt |
| `campaign_summary` | Totals per status and completion statistics |

Notes: reports use each student's **latest attempt**. Counts are blank (not 0) when the latest attempt
did not read the profile. Timestamps are UTC ISO-8601. "Student ID" is the local database ID.
Completion % = completed / evaluated, so failed or inaccessible profiles are excluded from it.

## Troubleshooting
- *Startup failed*: check `campaign/courses.json` and `.env` values.
- *Many FAILED results*: network issue or throttling; wait, then use "Retry failures".
- *PRIVATE_INACCESSIBLE*: the student must make the profile public.
- *Export error*: the output folder is not writable; set `CT_EXPORT_DIR`.
- Details: `F2` diagnostics or `data/tracker.log`.

## Testing
```powershell
pytest            # whole suite, uses temp databases and mocked HTTP only
pytest tests/test_exporter.py -v
```

## Known limitations
- Depends on Google Skills' current public-profile HTML; a layout change yields NEEDS_REVIEW/FAILED, not wrong counts.
- JavaScript-rendered content is not executed.
- Single local user; no multi-campaign UI.
- Reports are CSV only.