"""Create GitHub issues. urllib only; the token is passed in and never logged or put in an error."""
import json
import re
import urllib.error
import urllib.request

API = "https://api.github.com"
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubError(Exception):
    """A readable, token-free message about why an issue wasn't created."""


def valid_repo(repo: str) -> bool:
    return bool(_REPO.match(repo))


def _post(url: str, headers: dict, payload: dict, timeout: float) -> tuple[int, dict]:
    """The only function that touches the network (tests replace it). Returns (status, json body)."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode() or "{}")
        except ValueError:
            body = {}
        return e.code, body


def create_issue(token: str, repo: str, title: str, body: str, timeout: float = 20) -> dict:
    """POST /repos/{repo}/issues. Returns {"number": int, "url": str} or raises GitHubError."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
               "User-Agent": "meetloop"}
    try:
        status, data = _post(f"{API}/repos/{repo}/issues", headers, {"title": title, "body": body}, timeout)
    except GitHubError:
        raise
    except Exception as e:      # DNS failure, timeout, connection reset...
        raise GitHubError(f"Couldn't reach GitHub ({type(e).__name__}).") from None

    if status == 201 and isinstance(data, dict) and data.get("number") and data.get("html_url"):
        return {"number": int(data["number"]), "url": str(data["html_url"])}

    detail = str(data.get("message") or "").strip() if isinstance(data, dict) else ""
    detail = detail.replace(token, "***")       # only GitHub's own words can echo the token back
    hint = {401: "GitHub rejected the token (expired or wrong).",
            403: "GitHub refused the request (token lacks Issues write access, or rate limited).",
            404: f"Repository '{repo}' not found, or the token can't see it.",
            410: "Issues are disabled for this repository.",
            422: "GitHub rejected the issue as invalid."}.get(status, f"GitHub returned HTTP {status}.")
    raise GitHubError(f"{hint} ({detail})" if detail and detail not in hint else hint)
