# Meetloop GitHub Integration

## Purpose

Approved action items can become GitHub Issues in the configured repository.

## Approval boundary

Unreviewed model output is never supposed to become an external GitHub side effect. The user must confirm the action item first.

## Idempotency

The poster records enough state to avoid posting the same approved item repeatedly. Re-running a confirmation should therefore not create duplicate issues for an already-posted item.

## Token security

Keep GITHUB_TOKEN outside source control. Grant only the repository permissions required by the application.

## Safe operation

Before connecting a real repository:

- use a dedicated test repository;
- inspect the generated issue body;
- verify owner/deadline fields;
- confirm the token scope;
- test duplicate-post behavior.

## Failure handling

A failed GitHub call should remain visible as an integration failure rather than being presented as a successfully posted issue.
