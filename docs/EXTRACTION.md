# Meetloop Extraction

## Extraction model

Meetloop can use an LLM for extracting decisions, action items, owners and deadlines. A rule-based fallback is available.

## Review flags

The application should not invent an owner or deadline. Missing or ambiguous values are surfaced as review items so a human can correct them before approval.

## Dates

Dates are resolved in application code from the meeting date rather than delegated entirely to the model. This keeps relative phrases such as 'next Friday' tied to deterministic application context.

## Spoken input

The workflow is designed for messy spoken transcripts containing filler words, digressions and self-corrections. Users should review extracted fields before approval.

## Provider behavior

When both supported provider keys are configured, the current README documents the provider precedence. For deterministic/offline operation, use the rules extractor.
