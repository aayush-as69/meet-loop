# Meetloop Architecture

## System boundary

Meetloop is a Flask application backed by SQLite. It combines deterministic workflow logic with optional LLM extraction and a GitHub Issues integration.

~~~text
Transcript input
      |
      v
  Flask app
      |
      +--> extractor / LLM extractor
      |
      +--> decision memory + conflict detection
      |
      +--> review / approval
      |
      +--> GitHub poster
      |
      +--> reminders / digest
      |
      v
    SQLite
~~~

The model proposes structured meeting facts. Application code remains responsible for validation, date resolution and external side effects.

## Data flow

A transcript is ingested and stored with its meeting context. Candidate decisions and actions are extracted. Missing or ambiguous owners/deadlines are marked for review. New decisions are compared with earlier decisions before the user confirms anything.

## Side-effect boundary

GitHub issue creation happens only after human approval. The posting layer is idempotent so an already-posted approved item is not posted again.

## Fallback

When an LLM key is unavailable, the rule-based extractor keeps the core workflow usable, with reduced extraction coverage.
