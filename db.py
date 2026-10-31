"""SQLite access. Plain sqlite3, no ORM."""
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import current_app, g

_SCHEMA = os.path.join(os.path.dirname(__file__), "schema.sql")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DB_PATH"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def init_app(app):
    @app.teardown_appcontext
    def _close(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    with app.app_context():
        conn = get_db()
        with open(_SCHEMA, encoding="utf-8") as f:
            conn.executescript(f.read())
        _migrate(conn)


def _migrate(conn):
    """Add columns introduced after Stage 1 to databases created earlier."""
    def cols(table):
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if "topic" not in cols("items"):
        conn.execute("ALTER TABLE items ADD COLUMN topic TEXT")
    for col, ddl in (("review_status", "TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','rejected'))"),
                     ("issue_number", "INTEGER"), ("issue_url", "TEXT"), ("post_claimed_at", "TEXT"),
                     ("done_at", "TEXT")):
        if col not in cols("items"):
            conn.execute(f"ALTER TABLE items ADD COLUMN {col} {ddl}")
    if "extractor" not in cols("meetings"):
        conn.execute("ALTER TABLE meetings ADD COLUMN extractor TEXT NOT NULL DEFAULT 'rules'")
    if "fallback_reason" not in cols("meetings"):
        conn.execute("ALTER TABLE meetings ADD COLUMN fallback_reason TEXT")
    conn.commit()


def save_meeting(title, meeting_date, raw, cleaned, cleaning_log, items, extractor="rules", fallback_reason=None) -> int:
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO meetings (title, meeting_date, raw_transcript, cleaned_transcript, cleaning_log,"
            " extractor, fallback_reason) VALUES (?,?,?,?,?,?,?)",
            (title, meeting_date, raw, cleaned, json.dumps(cleaning_log), extractor, fallback_reason),
        )
        mid = cur.lastrowid
        for pos, it in enumerate(items):
            conn.execute(
                "INSERT INTO items (meeting_id, position, kind, description, topic, owner, deadline,"
                " deadline_raw, deadline_note, speaker, source_text, needs_review, review_reasons)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mid, pos, it["kind"], it["description"], it.get("topic"), it["owner"], it["deadline"],
                 it["deadline_raw"], it["deadline_note"], it["speaker"], it["source_text"],
                 int(it["needs_review"]), json.dumps(it["review_reasons"])),
            )
        conn.commit()
        return mid
    except Exception:
        conn.rollback()
        raise


def _item_row(r) -> dict:
    d = dict(r)
    d["needs_review"] = bool(d["needs_review"])
    d["review_reasons"] = json.loads(d["review_reasons"])
    # `status` in the API is the approve/reject decision; the old lifecycle column keeps its value
    # under another name rather than being silently dropped.
    d["lifecycle_status"] = d.pop("status")
    d["status"] = d.pop("review_status")
    d["posted"] = d["issue_number"] is not None
    d["done"] = d.get("done_at") is not None
    d["posting"] = _claim_is_fresh(d.pop("post_claimed_at")) and not d["posted"]
    return d


def get_meeting(meeting_id: int):
    conn = get_db()
    m = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
    if m is None:
        return None
    meeting = dict(m)
    meeting["cleaning_log"] = json.loads(meeting["cleaning_log"])
    rows = conn.execute(
        "SELECT * FROM items WHERE meeting_id = ? ORDER BY position", (meeting_id,)
    ).fetchall()
    meeting["items"] = [_item_row(r) for r in rows]
    meeting["conflicts"] = list_conflicts(meeting_id=meeting_id)
    return meeting


# ------------------------------------------------------------- conflicts
def meeting_decisions(meeting_id: int) -> list[dict]:
    rows = get_db().execute(
        "SELECT id, topic, description FROM items WHERE meeting_id = ? AND kind = 'decision' ORDER BY position",
        (meeting_id,)).fetchall()
    return [dict(r) for r in rows]


def earlier_decisions(item_id: int, limit: int = 300) -> list[dict]:
    """Every decision saved before `item_id` (earlier meetings, plus earlier lines of the same
    meeting), oldest first. Ids grow in the order meetings were loaded and in transcript order."""
    rows = get_db().execute(
        """SELECT i.id, i.topic, i.description, m.meeting_date, m.title AS meeting_title
           FROM items i JOIN meetings m ON m.id = i.meeting_id
           WHERE i.kind = 'decision' AND i.id < ? ORDER BY i.id DESC LIMIT ?""", (item_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def save_conflict(item_id: int, conflicting_item_id: int, reason: str, severity: str) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT OR REPLACE INTO conflicts (item_id, conflicting_item_id, reason, severity) VALUES (?,?,?,?)",
        (item_id, conflicting_item_id, reason, severity))
    conn.commit()
    return cur.lastrowid


_SEVERITY_ORDER = "CASE c.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END"


def list_conflicts(meeting_id: int | None = None, severity: str | None = None) -> list[dict]:
    """Conflicts raised by a meeting's decisions (or all of them), worst first."""
    where, args = [], []
    if meeting_id is not None:
        where.append("n.meeting_id = ?"); args.append(meeting_id)
    if severity:
        where.append("c.severity = ?"); args.append(severity)
    rows = get_db().execute(
        f"""SELECT c.id, c.severity, c.reason, c.created_at,
                   n.id AS item_id, n.topic, n.description AS description, n.meeting_id,
                   nm.title AS meeting_title, nm.meeting_date AS meeting_date,
                   o.id AS conflicting_item_id, o.topic AS conflicting_topic,
                   o.description AS conflicting_description, o.meeting_id AS conflicting_meeting_id,
                   om.title AS conflicting_meeting_title, om.meeting_date AS conflicting_meeting_date
            FROM conflicts c
            JOIN items n ON n.id = c.item_id          JOIN meetings nm ON nm.id = n.meeting_id
            JOIN items o ON o.id = c.conflicting_item_id JOIN meetings om ON om.id = o.meeting_id
            {'WHERE ' + ' AND '.join(where) if where else ''}
            ORDER BY {_SEVERITY_ORDER}, c.id""", args).fetchall()
    return [dict(r) for r in rows]


def list_meetings():
    rows = get_db().execute(
        """SELECT m.id, m.title, m.meeting_date, m.created_at,
                  COALESCE(SUM(i.kind = 'decision'), 0)    AS decisions,
                  COALESCE(SUM(i.kind = 'action_item'), 0) AS action_items,
                  COALESCE(SUM(i.needs_review), 0)         AS needs_review,
                  (SELECT COUNT(*) FROM conflicts c JOIN items n ON n.id = c.item_id
                    WHERE n.meeting_id = m.id)                     AS conflicts
           FROM meetings m LEFT JOIN items i ON i.meeting_id = m.id
           GROUP BY m.id ORDER BY m.id DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------- review + GitHub posting
CLAIM_TTL = timedelta(minutes=10)   # far longer than the GitHub call's timeout; only a crashed post outlives it


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _claim_is_fresh(claimed_at) -> bool:
    return bool(claimed_at) and claimed_at > (datetime.now(timezone.utc) - CLAIM_TTL).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_item(item_id: int):
    r = get_db().execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return _item_row(r) if r else None


def update_item(item_id: int, **fields) -> None:
    """Set owner / deadline / deadline_note / review_status / needs_review / review_reasons."""
    allowed = {"owner", "deadline", "deadline_note", "review_status", "needs_review", "review_reasons", "done_at"}
    assert fields and set(fields) <= allowed, fields
    if "review_reasons" in fields:
        fields["review_reasons"] = json.dumps(fields["review_reasons"])
    if "needs_review" in fields:
        fields["needs_review"] = int(fields["needs_review"])
    conn = get_db()
    conn.execute(f"UPDATE items SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), item_id))
    conn.commit()


def approved_action_items(meeting_id: int) -> list[dict]:
    rows = get_db().execute(
        "SELECT * FROM items WHERE meeting_id = ? AND kind = 'action_item' AND review_status = 'approved'"
        " ORDER BY position", (meeting_id,)).fetchall()
    return [_item_row(r) for r in rows]


def claim_for_posting(item_id: int) -> bool:
    """Atomically take the right to post this item. False if it already has an issue, is no longer
    approved, or another confirm is posting it right now. This is what makes double-clicks safe."""
    conn = get_db()
    stale = (datetime.now(timezone.utc) - CLAIM_TTL).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute(
        "UPDATE items SET post_claimed_at = ? WHERE id = ? AND issue_number IS NULL AND review_status = 'approved'"
        " AND (post_claimed_at IS NULL OR post_claimed_at < ?)", (_now(), item_id, stale))
    conn.commit()
    return cur.rowcount == 1


def release_claim(item_id: int) -> None:
    conn = get_db()
    conn.execute("UPDATE items SET post_claimed_at = NULL WHERE id = ? AND issue_number IS NULL", (item_id,))
    conn.commit()


def save_issue(item_id: int, number: int, url: str) -> None:
    conn = get_db()
    conn.execute("UPDATE items SET issue_number = ?, issue_url = ?, post_claimed_at = NULL WHERE id = ?",
                 (number, url, item_id))
    conn.commit()


# ------------------------------------------------------------ reminders + digest
def live_action_items() -> list[dict]:
    """Action items that are approved AND posted to GitHub (done or not): the ones the team is on the hook for."""
    rows = get_db().execute(
        """SELECT i.*, m.title AS meeting_title, m.meeting_date AS meeting_date
           FROM items i JOIN meetings m ON m.id = i.meeting_id
           WHERE i.kind = 'action_item' AND i.review_status = 'approved' AND i.issue_number IS NOT NULL
           ORDER BY i.id""").fetchall()
    return [_item_row(r) for r in rows]


def approved_decisions() -> list[dict]:
    rows = get_db().execute(
        """SELECT i.id, i.topic, i.description, i.meeting_id, m.title AS meeting_title,
                  COALESCE(m.meeting_date, substr(m.created_at, 1, 10)) AS meeting_date
           FROM items i JOIN meetings m ON m.id = i.meeting_id
           WHERE i.kind = 'decision' AND i.review_status = 'approved' ORDER BY i.meeting_id, i.position""").fetchall()
    return [dict(r) for r in rows]


def log_reminder(item_id: int, kind: str, fired_at: str) -> bool:
    """True if this (item, kind) reminder was newly logged, False if it had already fired."""
    conn = get_db()
    cur = conn.execute("INSERT OR IGNORE INTO reminders (item_id, kind, fired_at) VALUES (?,?,?)",
                       (item_id, kind, fired_at))
    conn.commit()
    return cur.rowcount == 1


def list_reminders(limit: int = 500) -> list[dict]:
    rows = get_db().execute(
        """SELECT r.id, r.kind, r.fired_at, i.id AS item_id, i.description, i.owner, i.deadline, i.done_at,
                  i.issue_number, i.issue_url, i.meeting_id, m.title AS meeting_title
           FROM reminders r JOIN items i ON i.id = r.item_id JOIN meetings m ON m.id = i.meeting_id
           ORDER BY r.fired_at DESC, r.id DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]
