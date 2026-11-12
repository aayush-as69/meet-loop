# Meetloop Security

## Secrets

Keep LLM API keys and GitHub tokens in `.env` and out of source control.

## GitHub access

Use the narrowest repository permissions possible. A token capable of writing issues is an external side-effect credential and should be treated as sensitive.

## Transcript data

Meeting transcripts are untrusted input. Do not include secrets, credentials or sensitive material in demo transcripts. Treat model output as untrusted until reviewed.

## Prompt/instruction boundary

Transcript content should be handled as meeting data, not as trusted application instructions. Review extracted fields before any external action.

## Operational controls

Rotate exposed credentials, use a dedicated test repository for demos, and inspect generated issue content before connecting a sensitive repository.

## Scope

This project documentation describes application-level controls. It is not a claim of independent security testing or production certification.
