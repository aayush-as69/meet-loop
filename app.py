"""Meetloop — extraction endpoint, saved meetings, and conflicts between decisions."""
import os
from datetime import date, datetime

from flask import Flask, jsonify, request, send_from_directory

import conflicts
import config
import db
import digest
import poster
import reminders
import scheduler
from extractor import extract

config.load_env()


def create_app(db_path: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1_000_000
    app.config["DB_PATH"] = db_path or os.environ.get("MEETLOOP_DB", "meetloop.db")
    db.init_app(app)

    def err(status, msg):
        return jsonify({"error": msg}), status

    @app.errorhandler(413)
    def _too_big(_e):
        return err(413, "Transcript too large (limit 1 MB).")

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True})

    @app.post("/api/extract")
    def extract_endpoint():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return err(400, "Send a JSON body: {\"transcript\": \"...\", \"meeting_date\": \"YYYY-MM-DD\"}")
        transcript = body.get("transcript")
        if not isinstance(transcript, str) or not transcript.strip():
            return err(400, "'transcript' is required and must be non-empty text.")

        meeting_date = None
        if body.get("meeting_date"):
            try:
                meeting_date = date.fromisoformat(str(body["meeting_date"]))
            except ValueError:
                return err(400, "'meeting_date' must be an ISO date like 2026-10-28.")

        title = str(body.get("title") or "").strip()[:200] or "Untitled meeting"
        mode = body.get("extractor")
        if mode is not None and mode not in ("auto", "rules"):
            return err(400, "'extractor' must be 'auto' or 'rules'.")
        result = extract(transcript, meeting_date, mode)

        warnings = []
        if meeting_date is None:
            warnings.append("No meeting_date given: relative dates ('next Friday') were left unresolved and flagged for review.")
        if result["fallback_reason"] and mode != "rules":
            warnings.append(f"LLM extractor not used ({result['fallback_reason']}); the rule-based extractor ran instead — it may miss informal decisions.")
        if not result["items"]:
            warnings.append("No decisions or action items were found.")

        if body.get("save") is False:       # dry run
            payload = {"id": None, "title": title, "meeting_date": meeting_date and meeting_date.isoformat(),
                       "cleaned_transcript": result["cleaned_transcript"],
                       "cleaning_log": result["cleaning_log"], "items": result["items"],
                       "extractor": result["extractor"], "fallback_reason": result["fallback_reason"]}
            return jsonify({**payload, "warnings": warnings})

        mid = db.save_meeting(title, meeting_date and meeting_date.isoformat(), transcript,
                              result["cleaned_transcript"], result["cleaning_log"], result["items"],
                              result["extractor"], result["fallback_reason"])
        check = conflicts.check_meeting(mid, mode)
        if check["skipped_reason"] and mode != "rules":
            warnings.append(f"Conflicts with earlier decisions were not fully checked ({check['skipped_reason']}).")
        return jsonify({**db.get_meeting(mid), "conflict_check": check, "warnings": warnings}), 201

    @app.get("/api/conflicts")
    def conflicts_list():
        sev = request.args.get("severity")
        if sev not in (None, "low", "medium", "high"):
            return err(400, "'severity' must be low, medium or high.")
        return jsonify(db.list_conflicts(severity=sev))

    @app.get("/api/meetings")
    def meetings():
        return jsonify(db.list_meetings())

    @app.get("/api/meetings/<int:meeting_id>")
    def meeting(meeting_id):
        m = db.get_meeting(meeting_id)
        return jsonify(m) if m else err(404, "Meeting not found.")

    # ---------------------------------------------------------------- review pages
    page_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

    class BadNow(Exception):
        pass

    @app.errorhandler(BadNow)
    def _bad_now(_e):
        return err(400, "'now' must be a local date or time like 2026-11-03 or 2026-11-03T09:00 (no time zone).")

    def get_now() -> datetime:
        """The clock for this request: ?now=... (demo/simulation) or the real local time."""
        raw = request.args.get("now")
        if not raw:
            return datetime.now().replace(microsecond=0)
        try:
            dt = datetime.fromisoformat(raw.strip())
        except ValueError:
            raise BadNow()
        if dt.tzinfo is not None:
            raise BadNow()
        return dt.replace(microsecond=0)

    @app.get("/")
    @app.get("/meetings/<int:meeting_id>")
    @app.get("/reminders")
    @app.get("/digest")
    @app.get("/new")
    def page(meeting_id=None):          # one HTML file; it reads the URL and calls the API below
        return send_from_directory(page_dir, "app.html")

    # ---------------------------------------------------------------- approve / edit
    @app.patch("/api/items/<int:item_id>")
    def patch_item(item_id):
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not body:
            return err(400, "Send a JSON body like {\"owner\": \"Priya\", \"deadline\": \"2026-11-20\", \"status\": \"approved\"}.")
        unknown = set(body) - {"owner", "deadline", "status"}
        if unknown:
            return err(400, f"Unknown field(s): {', '.join(sorted(unknown))}. Allowed: owner, deadline, status.")
        item = db.get_item(item_id)
        if item is None:
            return err(404, "Item not found.")
        if item["posted"]:
            return err(409, f"Already posted as issue #{item['issue_number']}; it can't be changed.")
        if item["posting"]:
            return err(409, "This item is being posted to GitHub right now; try again in a moment.")

        changes = {}
        if "status" in body:
            if body["status"] not in ("pending", "approved", "rejected"):
                return err(400, "'status' must be pending, approved or rejected.")
            changes["review_status"] = body["status"]

        reasons = item["review_reasons"]
        if "owner" in body or "deadline" in body:
            if item["kind"] != "action_item":
                return err(400, "Only action items have an owner and a deadline.")
        if "owner" in body:
            owner = body["owner"]
            if owner is not None and not isinstance(owner, str):
                return err(400, "'owner' must be text (or empty to clear it).")
            owner = (owner or "").strip()
            if len(owner) > 100:
                return err(400, "'owner' is too long (100 characters max).")
            changes["owner"] = owner or None
            if owner:       # a real owner answers every owner_* question; clearing one answers nothing
                reasons = [r for r in reasons if not r["code"].startswith("owner")]
        if "deadline" in body:
            dl = body["deadline"]
            if dl is not None and not isinstance(dl, str):
                return err(400, "'deadline' must be a date like 2026-11-20 (or empty to clear it).")
            dl = (dl or "").strip()
            if dl:
                try:
                    dl = date.fromisoformat(dl).isoformat()
                except ValueError:
                    return err(400, "'deadline' must be a date like 2026-11-20.")
                reasons = [r for r in reasons if not (r["code"].startswith("deadline") or r["code"] == "multiple_dates")]
            changes["deadline"] = dl or None
            changes["deadline_note"] = None     # the old interpretation note no longer describes this date
        if reasons != item["review_reasons"]:
            changes["review_reasons"] = reasons
            changes["needs_review"] = bool(reasons)
        db.update_item(item_id, **changes)
        return jsonify(db.get_item(item_id))

    # ---------------------------------------------------------------- post to GitHub
    @app.post("/api/meetings/<int:meeting_id>/confirm")
    def confirm(meeting_id):
        meeting = db.get_meeting(meeting_id)
        if meeting is None:
            return err(404, "Meeting not found.")
        base = config.base_url() or request.host_url.rstrip("/")
        try:
            return jsonify(poster.post_meeting(meeting, f"{base}/meetings/{meeting_id}"))
        except poster.NotConfigured as e:
            return err(400, str(e))

    # ---------------------------------------------------------------- done / reminders / digest
    @app.route("/api/items/<int:item_id>/done", methods=["POST", "DELETE"])
    def item_done(item_id):
        item = db.get_item(item_id)
        if item is None:
            return err(404, "Item not found.")
        if item["kind"] != "action_item":
            return err(400, "Only action items can be marked done.")
        if request.method == "POST":
            if not item["done"]:        # already done: keep the original time
                db.update_item(item_id, done_at=reminders.iso(get_now()))
        else:
            db.update_item(item_id, done_at=None)
        return jsonify(db.get_item(item_id))

    @app.get("/api/reminders")
    def reminders_list():
        return jsonify(db.list_reminders())

    @app.post("/api/reminders/run")
    def reminders_run():
        return jsonify(reminders.run(get_now()))

    @app.get("/api/upcoming")
    def upcoming():
        return jsonify(reminders.upcoming(get_now()))

    @app.get("/api/digest")
    def digest_all():
        return jsonify(digest.build(get_now()))

    @app.get("/api/digest/<person>/preview")
    def digest_preview(person):
        now = get_now()
        entry = digest.find(now, person)
        if entry is None:
            return err(404, f"No digest for '{person}' at {reminders.iso(now)}.")
        return jsonify(digest.preview(now, entry))

    return app


if __name__ == "__main__":
    app = create_app()
    debug = True
    scheduler.start(app, debug=debug)       # no-op in the reloader's watcher process; one job otherwise
    app.run(debug=debug, port=5000)
