# Meetloop

**Meeting-to-execution agent for turning messy meeting transcripts into confirmed decisions, owned action items and trackable follow-through.**

Meetloop accepts pasted or dictated transcripts and `.txt` / `.vtt` files, extracts decisions and action items, remembers earlier decisions, flags contradictions, and waits for human confirmation before creating GitHub issues.

Built for spoken input: filler words, digressions and self-corrections are treated as normal meeting data.

## Product flow

```text
Transcript
   │
   ▼
Extraction
(decisions · owners · deadlines)
   │
   ├───────────────┐
   ▼               ▼
Review          Earlier decisions
   │               │
   │               ▼
   │          Conflict detection
   └───────┬───────┘
           ▼
     Human confirmation
           │
           ▼
     GitHub issue
           │
           ▼
 reminders + weekly digest
```

**Important:** the model proposes structured facts; deterministic application logic resolves dates, validates fields and controls side effects.

## Features

| Area | Capability |
|---|---|
| Ingestion | Paste, dictate or upload `.txt` / `.vtt` transcripts |
| Speaker attribution | `Name: sentence` transcript format |
| Extraction | Decisions, actions, owners and deadlines |
| Safety | Missing/ambiguous owner or date → `needs review` |
| Date handling | Dates resolved from meeting date in application code |
| Memory | Earlier decisions are retained across meetings |
| Conflict detection | New decisions checked against prior decisions |
| Human review | Edit, approve or reject each extracted item |
| GitHub | Approved actions become issues |
| Idempotency | Same approved item is not posted twice |
| Reminders | 24-hour, 1-hour and overdue reminder events |
| Digest | Weekly per-person summary and email preview |
| Offline fallback | Rule-based extraction when no LLM key is configured |

## Quick start

### Requirements

Python 3.10+ is recommended by the project environment. Install dependencies with:

```bash
pip install -r requirements.txt
```

Create the environment file:

```bash
cp .env.example .env
```

Windows:

```cmd
copy .env.example .env
```

Configure the integrations:

- `GEMINI_API_KEY` or `ANTHROPIC_API_KEY`
- `GITHUB_TOKEN`
- `GITHUB_REPO`

Then seed the demo database:

```bash
python seed.py
```

Start the application:

```bash
python app.py
```

Open **http://127.0.0.1:5000**.

## Configuration

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` | LLM extraction and conflict checks; Anthropic takes precedence when both are set |
| `GITHUB_TOKEN` | GitHub Issues read/write integration |
| `GITHUB_REPO` | Repository receiving approved issues |
| `MEETLOOP_MODEL` | Optional model override |
| `MEETLOOP_EXTRACTOR` | `auto` for LLM + fallback or `rules` for rule-only operation |
| `MEETLOOP_BASE_URL` | Optional public URL included in generated GitHub issue links |
| `MEETLOOP_DB` | SQLite database path |

Without an LLM key, the application still runs using the rule-based extractor. It is more limited with informal decisions and does not perform the LLM-based conflict analysis path.

## Human approval boundary

Meetloop deliberately does **not** auto-post model output.

1. Extract candidate decisions/actions.
2. Mark incomplete or ambiguous fields for review.
3. Compare new decisions with previous meeting memory.
4. Let the user edit the owner/deadline.
5. Approve or reject each item.
6. Only approved items are eligible for GitHub posting.
7. Posting is idempotent so repeated requests do not create duplicates.

GitHub is therefore the execution target, not the place where unreviewed model output becomes an external side effect.

## Conflict detection

Each meeting is checked against earlier stored decisions.

A conflict record keeps the previous decision, the new decision, a reason and severity. This allows a user to review contradictions instead of silently replacing historical context.

A detected conflict is a review signal, not proof that two statements are objectively contradictory.

## Reminders and digest

Meetloop records:

- 24-hour reminder events
- 1-hour reminder events
- overdue events
- weekly per-person digest information

The UI includes a digest/email preview. Integration behavior should be verified separately before treating the preview as a live outbound email service.

For demos, `/reminders` and `/digest` accept `?now=2026-11-12T10:00` so a deterministic date can be simulated.

## Tests

```bash
python -m unittest discover -p "test_*.py"
```

Network calls to the LLM and GitHub are mocked in the test suite so core tests can run offline.

For an integration demo, additionally verify transcript ingestion, extraction/review flags, conflict detection, human approval, GitHub issue creation, duplicate-post protection, reminder behavior and digest generation.

## Security

- Keep API keys and GitHub tokens in `.env`; never commit them.
- GitHub tokens should have only the permissions required for the configured repository.
- Transcript content is untrusted input when sent to the model.
- Do not paste secrets, credentials or sensitive meeting material into demo transcripts.
- Treat the GitHub token as a production secret and rotate it if exposed.
- Review generated issue contents before enabling the integration on sensitive repositories.

## Repository structure

| File | Responsibility |
|---|---|
| `app.py` | Flask application, routes and UI |
| `extractor.py` / `llm_extractor.py` | Extraction and LLM integration |
| `conflicts.py` | Cross-meeting contradiction detection |
| `poster.py` / `github.py` | GitHub issue integration |
| `reminders.py` / `scheduler.py` | Reminder scheduling |
| `digest.py` | Weekly digest logic |
| `db.py` / `schema.sql` | SQLite persistence |
| `seed.py` | Demo data |
| `static/app.html` | Web interface |
| `demo_meetings/` | Sample meetings |

## Documentation

| Guide | Purpose |
|---|---|
| [Documentation index](docs/README.md) | Complete guide map |
| [Architecture](docs/ARCHITECTURE.md) | System design and boundaries |
| [Setup](docs/SETUP.md) | Local configuration and demo |
| [Extraction](docs/EXTRACTION.md) | LLM/rule extraction behavior |
| [Conflicts](docs/CONFLICTS.md) | Decision memory and contradiction handling |
| [GitHub integration](docs/GITHUB.md) | Approval, posting and idempotency |
| [Reminders](docs/REMINDERS.md) | Deadline events and digest |
| [Security](docs/SECURITY.md) | Secrets and integration boundaries |
| [Testing](docs/TESTING.md) | Offline and integration verification |
| [Deployment](docs/DEPLOYMENT.md) | Production considerations |

## Project status

**Functional prototype**

Meetloop's key design choice is the human approval boundary: extraction and conflict detection can be AI-assisted, but external GitHub side effects require explicit confirmation.