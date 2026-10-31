"""Weekly digest: per person, what's done, due soon, overdue — plus recent decisions for everyone."""
from datetime import date, datetime, timedelta

import db
import reminders

WINDOW = timedelta(days=7)
UNASSIGNED = "Unassigned"


def _key(name: str) -> str:
    return " ".join(name.split()).casefold()


def build(now: datetime) -> dict:
    today = now.date()
    decisions = [d for d in db.approved_decisions()
                 if d["meeting_date"] and today - WINDOW <= date.fromisoformat(d["meeting_date"][:10]) <= today]

    people: dict[str, dict] = {}       # casefolded owner -> entry (first spelling seen is displayed)

    def entry(owner):
        name = " ".join(owner.split()) if owner and owner.strip() else UNASSIGNED
        return people.setdefault(_key(name), {"name": name, "done": [], "due_soon": [], "overdue": [], "new_decisions": decisions})

    for it in db.live_action_items():
        e = entry(it["owner"])
        if it["done"]:
            done = datetime.fromisoformat(it["done_at"])
            if now - WINDOW <= done <= now:
                e["done"].append(reminders.brief(it))
            continue
        if not it["deadline"]:
            continue
        due = reminders.due_at(it["deadline"])
        it = {**it, "due_at": reminders.iso(due), "due_in_minutes": int((due - now).total_seconds() // 60)}
        if due < now:
            e["overdue"].append(reminders.brief(it))
        elif due <= now + WINDOW:
            e["due_soon"].append(reminders.brief(it))

    out = []
    for e in people.values():
        if not (e["done"] or e["due_soon"] or e["overdue"] or decisions):
            continue                    # nothing to tell this person
        e["done"].sort(key=lambda x: x["done_at"], reverse=True)
        e["due_soon"].sort(key=lambda x: (x["due_at"], x["id"]))
        e["overdue"].sort(key=lambda x: (x["due_at"], x["id"]))
        out.append(e)
    out.sort(key=lambda e: (e["name"] == UNASSIGNED, e["name"].casefold()))
    return {"now": reminders.iso(now), "people": out}


def find(now: datetime, person: str):
    return next((p for p in build(now)["people"] if _key(p["name"]) == _key(person)), None)


def preview(now: datetime, entry: dict) -> dict:
    name = entry["name"]
    n_over, n_soon = len(entry["overdue"]), len(entry["due_soon"])
    subject = f"Meetloop weekly digest for {name}: {n_over} overdue, {n_soon} due soon"

    def items(rows, fmt):
        return [f"- {fmt(r)}" for r in rows] or ["- Nothing."]

    def line(r):
        return f"{r['description']} (due {r['deadline']}) {r['issue_url']}"

    greeting = "Hi team," if name == UNASSIGNED else f"Hi {name},"
    intro = ("These action items have no owner yet. Please pick them up." if name == UNASSIGNED
             else f"Here is where your action items stand as of {now:%Y-%m-%d %H:%M}.")
    parts = [greeting, "", intro, "",
             f"OVERDUE ({n_over})", *items(entry["overdue"], line), "",
             f"DUE IN THE NEXT 7 DAYS ({n_soon})", *items(entry["due_soon"], line), "",
             f"DONE IN THE LAST 7 DAYS ({len(entry['done'])})",
             *items(entry["done"], lambda r: f"{r['description']} (done {r['done_at'][:10]}) {r['issue_url']}"), "",
             f"NEW DECISIONS THIS WEEK ({len(entry['new_decisions'])})",
             *items(entry["new_decisions"], lambda d: f"{d['description']} [{d['topic'] or 'general'}] — {d['meeting_title']}, {d['meeting_date']}"),
             "", "— Meetloop"]
    return {"person": name, "subject": subject, "body": "\n".join(parts)}
