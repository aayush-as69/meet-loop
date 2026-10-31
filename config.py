"""Tiny .env loader (no dependency). Real environment variables always win."""
import os

_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def load_env(path: str | None = None) -> bool:
    """Load KEY=VALUE lines from .env into os.environ without overriding anything already set."""
    try:
        f = open(path or _DEFAULT, encoding="utf-8")
    except OSError:
        return False
    with f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            elif " #" in value:
                value = value.split(" #", 1)[0].strip()
            if key and key not in os.environ:
                os.environ[key] = value
    return True


def _gemini_key() -> str | None:
    load_env()
    return ((os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()) or None


def _anthropic_key() -> str | None:
    load_env()
    return (os.environ.get("ANTHROPIC_API_KEY") or "").strip() or None


def provider() -> str | None:
    """'anthropic' if ANTHROPIC_API_KEY is set, else 'gemini' if GEMINI_API_KEY is set, else None."""
    return "anthropic" if _anthropic_key() else "gemini" if _gemini_key() else None


def api_key() -> str | None:
    """The key for whichever provider is configured (Anthropic wins if both are set)."""
    return _anthropic_key() or _gemini_key()


def model() -> str:
    load_env()
    default = "gemini-flash-latest" if provider() == "gemini" else "claude-sonnet-5-5"
    return os.environ.get("MEETLOOP_MODEL") or default


def timeout() -> float:
    try:
        return float(os.environ.get("MEETLOOP_LLM_TIMEOUT", "60"))
    except ValueError:
        return 60.0


def github_token() -> str | None:
    load_env()
    return (os.environ.get("GITHUB_TOKEN") or "").strip() or None


def github_repo() -> str | None:
    """'owner/name' — where issues are created."""
    load_env()
    return (os.environ.get("GITHUB_REPO") or "").strip() or None


def base_url() -> str | None:
    """Optional public address of this app, used for the meeting link inside GitHub issues
    (otherwise the address the browser used is taken, which is useless to teammates on localhost)."""
    load_env()
    return (os.environ.get("MEETLOOP_BASE_URL") or "").strip().rstrip("/") or None
