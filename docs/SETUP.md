# Meetloop Setup

## Requirements

Use the Python version supported by the current requirements and test environment. Install dependencies with:

~~~bash
pip install -r requirements.txt
~~~

Create configuration:

~~~bash
cp .env.example .env
~~~

Windows:

~~~cmd
copy .env.example .env
~~~

Configure the LLM provider, GitHub token and target repository as required.

## Seed and run

~~~bash
python seed.py
python app.py
~~~

Open http://127.0.0.1:5000.

## Demo mode

The application provides seeded meetings and deterministic date simulation for reminders/digests. Do not use real credentials or sensitive meeting material in the demo dataset.

## Integration configuration

- GEMINI_API_KEY or ANTHROPIC_API_KEY enables LLM extraction.
- GITHUB_TOKEN enables GitHub issue operations.
- GITHUB_REPO selects the destination repository.
- MEETLOOP_EXTRACTOR=rules forces the offline rule-based path.
- MEETLOOP_DB selects the SQLite database path.
