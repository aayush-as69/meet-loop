# Meetloop Deployment

## Current posture

Meetloop is a functional prototype. The repository is suitable for local/demo operation; a public deployment needs additional operational controls.

## Before public deployment

- Use a production WSGI server rather than Flask's development server.
- Put the application behind HTTPS.
- Store secrets in a managed secret system or protected environment.
- Use a persistent SQLite volume or move to a managed database if concurrency requires it.
- Restrict the GitHub token to the intended repository.
- Add authentication and authorization appropriate to the deployment audience.
- Add monitoring, backups and log retention.
- Review transcript privacy and retention requirements.

## External integrations

Verify GitHub permissions and LLM provider behavior in the actual deployment environment. The local test suite uses mocks and does not establish production connectivity.
