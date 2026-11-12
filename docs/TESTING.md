# Meetloop Testing

## Automated tests

~~~bash
python -m unittest discover -p "test_*.py"
~~~

The repository's core tests mock LLM and GitHub calls so they can run offline.

## Functional smoke test

Verify:

1. Seed demo meetings.
2. Ingest a transcript.
3. Extract decisions/actions.
4. Confirm ambiguous fields are marked for review.
5. Inspect conflict detection against earlier decisions.
6. Edit owner/deadline.
7. Approve an action.
8. Post it to a test GitHub repository.
9. Repeat the operation and verify idempotency.
10. Exercise reminder and digest views.

## Test boundary

Offline tests prove application behavior under mocked integrations. They do not prove live GitHub permissions, live LLM quality or live email delivery.
