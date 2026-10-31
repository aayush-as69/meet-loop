import re


def slug(text, max_len: int = 40) -> str:
    """'Launch Date!' -> 'launch_date' (lowercase, underscores, short)."""
    s = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    if len(s) > max_len:
        s = s[:max_len].rsplit("_", 1)[0] or s[:max_len]
    return s


TENTATIVE = re.compile(
    r"\b(?:maybe|might|perhaps|possibly|probably|i\s+guess|if\s+possible|try\s+to|hopefully|if\s+we\s+have\s+time)\b", re.I)
