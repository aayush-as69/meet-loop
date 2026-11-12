# Meetloop Documentation

Technical documentation for the meeting-to-execution agent.

## Documentation map

| Document | Purpose |
|---|---|
| ARCHITECTURE.md | Meeting ingestion, extraction, memory and workflow architecture |
| SETUP.md | Local setup, demo seed and configuration |
| EXTRACTION.md | LLM/rule extraction, date resolution and review flags |
| CONFLICTS.md | Cross-meeting decision memory and contradiction detection |
| GITHUB.md | Approval-to-GitHub issue posting and idempotency |
| REMINDERS.md | Deadline reminders and weekly digest behavior |
| SECURITY.md | Secrets, transcript handling and integration permissions |
| TESTING.md | Offline test strategy and integration verification |
| DEPLOYMENT.md | Production deployment considerations |

## Core principle

The model proposes structured meeting facts; deterministic application logic resolves dates, validates fields and controls side effects. Nothing is posted to GitHub until a human confirms it.

## Source-of-truth rule

Implementation and tests are authoritative. Documentation must distinguish the rule-based fallback from LLM-powered extraction and must not imply that GitHub issues, reminders or email are sent when the relevant integration is only mocked.