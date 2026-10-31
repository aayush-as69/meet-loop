"""Find date phrases in text and resolve them against the meeting date.

Pure functions, no I/O. Anything we cannot resolve honestly comes back with
`unresolved_reason` set and `date=None` -- we never guess.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

_WD = {"monday": 0, "tuesday": 1, "tues": 1, "wednesday": 2, "weds": 2,
       "thursday": 3, "thurs": 3, "thur": 3, "friday": 4, "saturday": 5, "sunday": 6}
_MON = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
        "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
        "aug": 8, "sept": 9, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_NUMW = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
         "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _alt(d):
    return "|".join(sorted(d, key=len, reverse=True))


WD, MON, NUMW = _alt(_WD), _alt(_MON), _alt(_NUMW)

# unnamed versions (for the combined finder) ...
_MD_P = rf"(?:the\s+)?(?:{MON})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?"
_DM_P = rf"(?:the\s+)?\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{MON})(?:,?\s+\d{{4}})?"
# ... and named versions (for picking the pieces apart)
_MD_RE = re.compile(rf"(?:the\s+)?(?P<mon>{MON})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<year>\d{{4}}))?", re.I)
_DM_RE = re.compile(rf"(?:the\s+)?(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon>{MON})(?:,?\s+(?P<year>\d{{4}}))?", re.I)

# Order matters: at the same start position the first alternative wins.
PATTERNS = [
    ("iso", r"\d{4}-\d{2}-\d{2}"),
    ("wd_date", rf"(?:{WD}),?\s+(?:{_MD_P}|{_DM_P})"),
    ("month_day", _MD_P),
    ("day_month", _DM_P),
    ("rel_wd", rf"(?:next|this|coming|upcoming)\s+(?:{WD})"),
    ("end_of", r"(?:the\s+)?end\s+of\s+(?:the\s+|this\s+|next\s+)?(?:day|week|month)|eod|eow|cob|close\s+of\s+business"),
    ("in_n", rf"in\s+(?:\d+|{NUMW})\s+(?:day|week|month)s?"),
    ("rel_unit", r"(?:next|this)\s+(?:week|month)"),
    ("weekday", rf"(?:{WD})"),
    ("rel_day", r"tomorrow|today|tonight"),
    ("day_only", r"the\s+\d{1,2}(?:st|nd|rd|th)"),
]
_ANY = re.compile(r"\b(?:" + "|".join(f"(?P<{n}>{p})" for n, p in PATTERNS) + r")\b", re.I)
_KIND_RES = [(n, re.compile(p, re.I)) for n, p in PATTERNS]


@dataclass
class DatePhrase:
    start: int
    end: int
    text: str
    kind: str


@dataclass
class Resolution:
    date: str | None = None            # ISO date
    note: str | None = None            # a judgment call we made, worth showing the user
    unresolved_reason: str | None = None
    ambiguous: bool = False            # resolved, but a human should confirm


def find_date_phrases(text: str) -> list[DatePhrase]:
    out = []
    for m in _ANY.finditer(text):
        kind = next(n for n, _ in PATTERNS if m.group(n) is not None)
        out.append(DatePhrase(m.start(), m.end(), m.group(0), kind))
    return out


def _kind(phrase: str):
    for n, rx in _KIND_RES:
        if rx.fullmatch(phrase):
            return n
    return None


def _next_weekday(ref: date, wd: int) -> date:
    """Next occurrence of weekday `wd` strictly after `ref`."""
    delta = (wd - ref.weekday()) % 7 or 7
    return ref + timedelta(days=delta)


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = y + d.year, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _resolve_calendar(p: str, ref: date | None) -> Resolution:
    m = _MD_RE.search(p) or _DM_RE.search(p)
    mon, day = _MON[m.group("mon").lower()], int(m.group("day"))
    year = int(m.group("year")) if m.group("year") else None
    note = None
    if year is None:
        if ref is None:
            return Resolution(unresolved_reason="no meeting date was provided, so the year can't be inferred")
        year = ref.year
    try:
        d = date(year, mon, day)
    except ValueError:
        return Resolution(unresolved_reason=f"'{p}' is not a valid calendar date")
    if not m.group("year") and d < ref:
        try:
            d = date(year + 1, mon, day)
            note = f"year assumed to be {year + 1} (that date has already passed in {year})"
        except ValueError:
            return Resolution(unresolved_reason=f"'{p}' is not a valid calendar date")
    res = Resolution(d.isoformat(), note)
    wd_word = re.match(rf"({WD})\b", p, re.I)
    if wd_word and _WD[wd_word.group(1).lower()] != d.weekday():
        res.ambiguous = True
        res.note = (f"stated weekday doesn't match the date: {d.isoformat()} is a "
                    f"{calendar.day_name[d.weekday()]}")
    return res


def resolve(phrase: str, ref: date | None) -> Resolution:
    p = " ".join(phrase.split())
    low = p.lower()
    kind = _kind(p)
    if kind is None:
        return Resolution(unresolved_reason="unrecognised date phrase")
    if kind == "iso":
        try:
            return Resolution(date.fromisoformat(p).isoformat())
        except ValueError:
            return Resolution(unresolved_reason=f"'{p}' is not a valid calendar date")
    if kind in ("month_day", "day_month", "wd_date"):
        return _resolve_calendar(p, ref)
    if ref is None:
        return Resolution(unresolved_reason="no meeting date was provided, so a relative date can't be resolved")

    if kind == "rel_day":
        return Resolution((ref + timedelta(days=1) if low == "tomorrow" else ref).isoformat())

    if kind in ("rel_wd", "weekday"):
        mod, name = (low.split(None, 1) if kind == "rel_wd" else ("", low))
        wd, label = _WD[name], calendar.day_name[_WD[name]]
        nearest = _next_weekday(ref, wd)
        if mod == "next":
            monday_next = ref - timedelta(days=ref.weekday()) + timedelta(days=7)
            d = monday_next + timedelta(days=wd)
            note = None
            if d != nearest:
                note = (f"'{p}' read as the {label} of next calendar week; "
                        f"the nearest {label} would be {nearest.isoformat()}")
            return Resolution(d.isoformat(), note)
        res = Resolution(nearest.isoformat())
        if ref.weekday() == wd:
            res.ambiguous = True
            res.note = f"said on a {label}; assumed the {label} one week later"
        return res

    if kind == "end_of":
        nxt = "next" in low
        if "month" in low:
            base = _add_months(ref.replace(day=1), 1) if nxt else ref
            d = date(base.year, base.month, calendar.monthrange(base.year, base.month)[1])
            return Resolution(d.isoformat(), "'end of month' read as the last calendar day")
        if "week" in low or low == "eow":
            fri = ref - timedelta(days=ref.weekday()) + timedelta(days=4)
            if nxt or fri < ref:
                fri += timedelta(days=7)
            return Resolution(fri.isoformat(), "'end of week' read as Friday")
        return Resolution(ref.isoformat(), "'end of day' read as the meeting date")

    if kind == "in_n":
        m = re.match(rf"in\s+(\d+|{NUMW})\s+(day|week|month)s?", low)
        n = int(m.group(1)) if m.group(1).isdigit() else _NUMW[m.group(1)]
        unit = m.group(2)
        d = (ref + timedelta(days=n) if unit == "day"
             else ref + timedelta(weeks=n) if unit == "week" else _add_months(ref, n))
        return Resolution(d.isoformat())

    if kind == "rel_unit":
        return Resolution(unresolved_reason=f"'{p}' names a period, not a specific day")

    if kind == "day_only":
        day = int(re.search(r"\d+", low).group())
        for months_ahead in (0, 1):
            base = _add_months(ref.replace(day=1), months_ahead)
            if day <= calendar.monthrange(base.year, base.month)[1]:
                d = base.replace(day=day)
                if d >= ref:
                    return Resolution(d.isoformat(), "no month stated; took the next such day on/after the meeting")
        return Resolution(unresolved_reason=f"couldn't place '{p}' in a month")

    return Resolution(unresolved_reason="unrecognised date phrase")
