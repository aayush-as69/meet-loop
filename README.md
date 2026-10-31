# Meetloop

**Meeting-to-execution agent.** Paste or dictate a messy meeting transcript and Meetloop turns it
into decisions and owned, dated action items. It remembers every earlier meeting and flags when a
new decision contradicts an old one. Nothing is posted until a human confirms; then approved
action items become GitHub issues, with reminders before deadlines and a weekly digest per person.

Built by voice with [Wispr Flow](https://wisprflow.ai), and designed for spoken input: filler
words, digressions and self-corrections ("Friday, no wait, make it Monday" means Monday).

## Features
- **Ingestion with speaker attribution**: paste, dictate, or drop a `.txt` / `.vtt` file (`Name: sentence` lines).
- **Extraction**: decisions, action items, owners, deadlines. Never invents an owner or a date;
  missing or ambiguous values are flagged `needs review`. Dates are resolved in code from the
  meeting date, not by the model.
- **Cross-meeting memory and conflict detection**: each new decision is checked against all earlier
  ones; conflicts are stored with the old decision, a reason and a severity.
- **Confirmation step**: review screen with editable owner/deadline and approve/reject per item.
- **Tracker integration**: "Confirm & Post" creates one GitHub issue per approved action item
  (owner, due date, source quote, link back). Idempotent: never posts the same item twice.
- **Reminders**: logged 24 h and 1 h before a deadline, and when overdue. Each fires once.
- **Weekly digest per person**: done, due soon, overdue, new decisions, with an email preview.

## Quick start
```bash
pip install -r requirements.txt
cp .env.example .env          # Windows: copy .env.example .env
# edit .env: GEMINI_API_KEY (or ANTHROPIC_API_KEY), GITHUB_TOKEN, GITHUB_REPO

python seed.py                # loads 3 demo meetings and prints the conflicts it finds
MEETLOOP_DB=meetloop_demo.db python app.py     # PowerShell: $env:MEETLOOP_DB="meetloop_demo.db"; python app.py
```
Open http://127.0.0.1:5000

| Page | What it does |
|---|---|
| `/` | meetings, with counts of decisions, actions, items needing review and conflicts |
| `/new` | paste / dictate / drop a transcript |
| `/meetings/<id>` | review, edit, approve, Confirm & Post |
| `/reminders` | overdue, due within 24 h, fired log |
| `/digest` | per-person weekly digest with email preview |

Add `?now=2026-11-12T10:00` to `/reminders` and `/digest` to simulate a date for demos.

## Configuration (`.env`)
| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` or `ANTHROPIC_API_KEY` | LLM for extraction and conflict checks (Anthropic wins if both are set) |
| `GITHUB_TOKEN` | token with **Issues: read & write** on the repo |
| `GITHUB_REPO` | `owner/name` where issues are created |
| `MEETLOOP_MODEL` | optional model override (default `gemini-flash-latest` / `claude-sonnet-5-5`) |
| `MEETLOOP_EXTRACTOR` | `auto` (LLM, rule-based fallback) or `rules` (never calls the API) |
| `MEETLOOP_BASE_URL` | optional public URL used for the meeting link inside issues |

Without an API key the app still works using the rule-based extractor, but it misses informal
decisions and skips conflict detection.

## Tests
```bash
python -m unittest discover -p "test_*.py"
```
All network calls (LLM and GitHub) are mocked, so the tests run offline.

## Layout
`app.py` Flask API and pages · `extractor.py` / `llm_extractor.py` extraction · `conflicts.py`
conflict detection · `poster.py` / `github.py` issue posting · `reminders.py` / `scheduler.py`
reminders · `digest.py` weekly digest · `db.py` / `schema.sql` SQLite · `static/app.html` UI ·
`demo_meetings/` seed data.

## Security notes
Secrets live only in `.env` (git-ignored). Tokens are never logged or returned by the API.
Transcript text is treated as untrusted data when sent to the model.
