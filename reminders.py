"""When reminders fire, and the job that logs them.

A deadline is a date, so the *due moment* is 09:00 local time on that date:
    h24      from 09:00 the day before
    h1       from 08:00 on the day
    overdue  after 09:00 on the day (strictly after: at exactly 09:00 it is "due now", not late)
All clocks are naive local time. `now` is always passed in, so demos can simulate any moment.
"""
from datetime import date, datetime, time, timedelta

import db

DUE_TIME = time(9, 0)
HOUR = timedelta(hours=1)
DAY = timedelta(hours=24)


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def due_at(deadline: str) -> datetime:
    return datetime.combine(date.fromisoformat(deadline), DUE_TIME)


def bucket(now: datetime, due: datetime) -> str:
    """overdue | due_1h | due_24h | later"""
    left = due - now
    if left < timedelta(0):
        return "overdue"
    if left <= HOUR:
        return "due_1h"
    if left <= DAY:
        return "due_24h"
    return "later"


_KIND = {"due_24h": "h24", "due_1h": "h1", "overdue": "overdue"}


def pending_items(now: datetime) -> list[dict]:
    """Live (approved + posted), not done, with a deadline — each with its due moment and minutes left."""
    out = []
    for it in db.live_action_items():
        if it["done"] or not it["deadline"]:
            continue
        due = due_at(it["deadline"])
        out.append({**it, "due_at": iso(due), "_due": due, "bucket": bucket(now, due),
                    "due_in_minutes": int((due - now).total_seconds() // 60)})
    return out


def brief(it: dict) -> dict:
    """What the API shows about an item (no internals)."""
    keys = ("id", "description", "owner", "deadline", "due_at", "due_in_minutes", "done_at", "issue_number",
            "issue_url", "meeting_id", "meeting_title")
    return {k: it[k] for k in keys if k in it}


def upcoming(now: datetime) -> dict:
    out = {"now": iso(now), "overdue": [], "due_1h": [], "due_24h": [], "later": []}
    for it in sorted(pending_items(now), key=lambda x: (x["_due"], x["id"])):
        out[it["bucket"]].append(brief(it))
    return out


def run(now: datetime) -> dict:
    """Log the reminder each pending item is currently due for. Safe to call as often as you like:
    UNIQUE(item_id, kind) makes a second call a no-op. If the job was down and a deadline has
    already passed, only the 'overdue' reminder is logged — a "due in 1h" notice would be stale."""
    items = pending_items(now)
    fired = []
    for it in items:
        kind = _KIND.get(it["bucket"])
        if kind and db.log_reminder(it["id"], kind, iso(now)):
            fired.append({"item_id": it["id"], "kind": kind, "description": it["description"], "owner": it["owner"]})
    return {"now": iso(now), "checked": len(items), "fired": fired}
