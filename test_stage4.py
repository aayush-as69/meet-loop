"""Stage 4: reminders, upcoming buckets, done, digest, now= override, scheduler wiring."""
import os, sqlite3, sys, tempfile, types, unittest
from datetime import datetime
from unittest import mock

import db, reminders, scheduler
from app import create_app


def item(kind, desc, **kw):
    base = {"kind": kind, "description": desc, "topic": None, "owner": None, "deadline": None, "deadline_raw": None,
            "deadline_note": None, "speaker": "", "source_text": "q", "needs_review": False, "review_reasons": []}
    base.update(kw)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        self.addCleanup(os.remove, self.path)
        self.app = create_app(self.path)
        self.client = self.app.test_client()
        self.n = 0

    def meeting(self, items, date="2026-10-30", title="Sync"):
        with self.app.app_context():
            mid = db.save_meeting(title, date, "r", "c", [], items)
            return [i["id"] for i in db.get_meeting(mid)["items"]]

    def live(self, desc, owner, deadline, date="2026-10-30"):
        """An action item that is approved and posted (the only kind reminders/digest care about)."""
        (iid,) = self.meeting([item("action_item", desc, owner=owner, deadline=deadline)], date)
        self.n += 1
        with self.app.app_context():
            db.update_item(iid, review_status="approved")
            db.save_issue(iid, 100 + self.n, f"https://github.com/acme/site/issues/{100 + self.n}")
        return iid

    def run_job(self, now):
        with self.app.app_context():
            return reminders.run(datetime.fromisoformat(now))

    def log(self):
        return self.client.get("/api/reminders").get_json()

    def done(self, iid, now=None):
        return self.client.post(f"/api/items/{iid}/done" + (f"?now={now}" if now else ""))

    def digest(self, now):
        return {p["name"]: p for p in self.client.get(f"/api/digest?now={now}").get_json()["people"]}


class Reminders(Base):
    def test_each_kind_fires_once_even_if_the_job_runs_twice(self):
        iid = self.live("Draft doc", "Rahul", "2026-11-03")
        steps = [("2026-11-02T08:59", None), ("2026-11-02T09:00", "h24"), ("2026-11-02T20:00", None),
                 ("2026-11-03T07:59", None), ("2026-11-03T08:00", "h1"), ("2026-11-03T08:30", None),
                 ("2026-11-03T09:00", None),                        # exactly due: still "due now", not overdue
                 ("2026-11-03T09:01", "overdue"), ("2026-11-04T09:00", None)]
        for now, kind in steps:
            for attempt in (1, 2):                                   # run the job twice every time
                fired = self.run_job(now)["fired"]
                expected = [{"item_id": iid, "kind": kind}] if kind and attempt == 1 else []
                self.assertEqual([{"item_id": f["item_id"], "kind": f["kind"]} for f in fired], expected, (now, attempt))
        self.assertEqual(sorted(r["kind"] for r in self.log()), ["h1", "h24", "overdue"])
        with self.app.app_context():     # the database itself refuses a duplicate
            self.assertFalse(db.log_reminder(iid, "h24", "2030-01-01T00:00:00"))

    def test_not_for_done_unposted_unapproved_or_undated_items(self):
        a = self.live("Done one", "A", "2026-11-03"); self.done(a)
        ids = self.meeting([
            item("action_item", "Approved, never posted", owner="B", deadline="2026-11-03"),
            item("action_item", "Pending, posted", owner="C", deadline="2026-11-03"),
            item("action_item", "Rejected, posted", owner="D", deadline="2026-11-03"),
            item("action_item", "No deadline", owner="E"),
            item("decision", "A decision", topic="x", deadline="2026-11-03")])
        with self.app.app_context():
            db.update_item(ids[0], review_status="approved")
            db.update_item(ids[1], review_status="pending"); db.save_issue(ids[1], 1, "u")
            db.update_item(ids[2], review_status="rejected"); db.save_issue(ids[2], 2, "u")
            db.update_item(ids[3], review_status="approved"); db.save_issue(ids[3], 3, "u")
            db.update_item(ids[4], review_status="approved"); db.save_issue(ids[4], 4, "u")
        r = self.run_job("2026-11-03T09:30")
        self.assertEqual((r["checked"], r["fired"]), (0, []))
        self.assertEqual(self.log(), [])

    def test_marking_done_stops_later_reminders(self):
        iid = self.live("Draft doc", "Rahul", "2026-11-03")
        self.run_job("2026-11-02T09:00")
        self.done(iid)
        self.assertEqual(self.run_job("2026-11-03T09:01")["fired"], [])
        self.assertEqual([r["kind"] for r in self.log()], ["h24"])

    def test_late_start_logs_only_the_overdue_reminder(self):
        self.live("Draft doc", "Rahul", "2026-11-03")
        self.assertEqual([f["kind"] for f in self.run_job("2026-11-10T09:00")["fired"]], ["overdue"])

    def test_log_is_newest_first_with_item_owner_and_issue_link(self):
        a = self.live("First", "Rahul", "2026-11-03"); b = self.live("Second", "Meera", "2026-11-04")
        self.run_job("2026-11-02T09:00")            # h24 for First
        self.run_job("2026-11-03T09:00")            # h24 for Second (and h1 for First: it is due now)
        rows = self.log()
        self.assertEqual([(r["description"], r["kind"], r["fired_at"]) for r in rows],
                         [("Second", "h24", "2026-11-03T09:00:00"), ("First", "h1", "2026-11-03T09:00:00"),
                          ("First", "h24", "2026-11-02T09:00:00")])     # same minute: newest log row first
        rows = [rows[0]]
        self.assertEqual((rows[0]["owner"], rows[0]["issue_number"], rows[0]["issue_url"]),
                         ("Meera", 102, "https://github.com/acme/site/issues/102"))

    def test_run_endpoint_uses_now(self):
        self.live("Draft doc", "Rahul", "2026-11-03")
        r = self.client.post("/api/reminders/run?now=2026-11-02T09:00").get_json()
        self.assertEqual([f["kind"] for f in r["fired"]], ["h24"])
        self.assertEqual(self.client.post("/api/reminders/run?now=2026-11-02T09:00").get_json()["fired"], [])


class Upcoming(Base):
    def test_buckets(self):
        o = self.live("Overdue", "A", "2026-11-02")        # due 11-02 09:00
        h = self.live("Within an hour", "A", "2026-11-03")  # due 11-03 09:00
        d = self.live("Within a day", "A", "2026-11-04")
        l = self.live("Later", "A", "2026-11-20")
        self.meeting([item("action_item", "No deadline")])
        r = self.client.get("/api/upcoming?now=2026-11-03T08:30").get_json()
        self.assertEqual({k: [i["id"] for i in r[k]] for k in ("overdue", "due_1h", "due_24h", "later")},
                         {"overdue": [o], "due_1h": [h], "due_24h": [], "later": [d, l]})
        r = self.client.get("/api/upcoming?now=2026-11-03T09:00").get_json()     # boundary: now == due
        self.assertEqual([i["id"] for i in r["due_1h"]], [h])
        r = self.client.get("/api/upcoming?now=2026-11-03T10:00").get_json()
        self.assertEqual([i["id"] for i in r["overdue"]], [o, h])
        r = self.client.get("/api/upcoming?now=2026-11-03T09:01").get_json()
        self.assertEqual([i["id"] for i in r["due_24h"]], [d])                    # 23h59m left
        self.assertEqual(r["overdue"][0]["due_in_minutes"] < 0, True)

    def test_done_items_leave_the_buckets(self):
        iid = self.live("x", "A", "2026-11-02"); self.done(iid)
        self.assertEqual(self.client.get("/api/upcoming?now=2026-11-05").get_json()["overdue"], [])


class DoneEndpoint(Base):
    def test_set_keep_and_undo(self):
        iid = self.live("x", "A", "2026-11-02")
        r = self.done(iid, "2026-11-01T10:00").get_json()
        self.assertEqual((r["done_at"], r["done"]), ("2026-11-01T10:00:00", True))
        self.assertEqual(self.done(iid, "2026-11-02T10:00").get_json()["done_at"], "2026-11-01T10:00:00")   # unchanged
        r = self.client.delete(f"/api/items/{iid}/done").get_json()
        self.assertEqual((r["done_at"], r["done"]), (None, False))

    def test_errors(self):
        (dec,) = self.meeting([item("decision", "d", topic="t")])
        self.assertEqual(self.done(dec).status_code, 400)
        self.assertEqual(self.done(999).status_code, 404)
        self.assertEqual(self.client.delete("/api/items/999/done").status_code, 404)

    def test_posted_items_can_still_be_marked_done(self):      # the stage-3 lock only covers owner/deadline/status
        iid = self.live("x", "A", "2026-11-02")
        self.assertEqual(self.done(iid).status_code, 200)


class Digest(Base):
    NOW = "2026-11-10T12:00"

    def test_grouping_and_windows(self):
        self.live("Overdue A", "Rahul", "2026-11-05")                 # overdue
        self.live("Soon A", "rahul", "2026-11-16")                    # +6 days, owner spelled differently: same person
        self.live("Far A", "Rahul", "2026-11-18")                     # +8 days: not due soon
        self.live("No date A", "Rahul", None)                         # nowhere
        d1 = self.live("Done recently", "Meera", "2026-11-01"); self.done(d1, "2026-11-04T09:00")   # 6 days ago
        d2 = self.live("Done long ago", "Meera", "2026-11-01"); self.done(d2, "2026-11-02T09:00")   # 8 days ago
        self.live("Due today later", "Meera", "2026-11-10")           # 09:00 today already passed -> overdue
        self.live("Nobody's", None, "2026-11-12")
        self.live("Blank owner", "   ", "2026-11-05")
        ids = self.meeting([item("action_item", "Rejected", owner="Zed", deadline="2026-11-12"),
                            item("action_item", "Not posted", owner="Zed", deadline="2026-11-12")])
        with self.app.app_context():
            db.update_item(ids[0], review_status="rejected"); db.save_issue(ids[0], 9, "u")
            db.update_item(ids[1], review_status="approved")

        people = self.digest(self.NOW)
        self.assertEqual(list(people), ["Meera", "Rahul", "Unassigned"])         # alphabetical, Unassigned last
        desc = lambda p, k: [i["description"] for i in people[p][k]]
        self.assertEqual(desc("Rahul", "overdue"), ["Overdue A"])
        self.assertEqual(desc("Rahul", "due_soon"), ["Soon A"])
        self.assertEqual(desc("Meera", "done"), ["Done recently"])
        self.assertEqual(desc("Meera", "overdue"), ["Due today later"])
        self.assertEqual(desc("Meera", "due_soon"), [])
        self.assertEqual(desc("Unassigned", "due_soon"), ["Nobody's"])
        self.assertEqual(desc("Unassigned", "overdue"), ["Blank owner"])
        self.assertNotIn("Zed", people)
        everything = [i["description"] for p in people.values() for k in ("done", "due_soon", "overdue") for i in p[k]]
        for hidden in ("Far A", "No date A", "Done long ago", "Rejected", "Not posted"):
            self.assertNotIn(hidden, everything)

    def test_new_decisions_window_and_audience(self):
        self.live("Task", "Rahul", "2026-11-12"); self.live("Task 2", "Meera", "2026-11-12")
        self.meeting([item("decision", "Recent approved", topic="db")], date="2026-11-04", title="Recent")     # 6 days ago
        self.meeting([item("decision", "Old", topic="db")], date="2026-11-02", title="Old")                   # 8 days ago
        self.meeting([item("decision", "Future", topic="db")], date="2026-11-11", title="Future")
        self.meeting([item("decision", "Unreviewed", topic="db"), item("decision", "Rejected", topic="db")],
                     date="2026-11-05", title="Mixed")
        with self.app.app_context():
            for d in db.approved_decisions():
                pass
            conn = db.get_db()
            conn.execute("UPDATE items SET review_status='approved' WHERE kind='decision' AND description IN ('Recent approved','Old','Future')")
            conn.execute("UPDATE items SET review_status='rejected' WHERE description='Rejected'")
            conn.commit()
        people = self.digest(self.NOW)
        for name in ("Rahul", "Meera"):                                           # shown to everyone
            self.assertEqual([d["description"] for d in people[name]["new_decisions"]], ["Recent approved"])
        self.assertEqual(people["Rahul"]["new_decisions"][0]["meeting_title"], "Recent")

    def test_people_with_nothing_to_say_are_left_out(self):
        iid = self.live("Ancient", "Quiet", "2026-09-01"); self.done(iid, "2026-09-02T09:00")
        self.assertEqual(self.digest(self.NOW), {})

    def test_now_override_changes_the_result(self):
        self.live("Task", "Rahul", "2026-11-12")
        early = self.digest("2026-11-10")["Rahul"]
        late = self.digest("2026-11-13T09:00")["Rahul"]
        self.assertEqual(([i["id"] for i in early["due_soon"]], early["overdue"]), ([1], []))
        self.assertEqual((late["due_soon"], [i["id"] for i in late["overdue"]]), ([], [1]))
        self.assertEqual(self.client.get("/api/digest?now=2026-11-10").get_json()["now"], "2026-11-10T00:00:00")

    def test_bad_now_is_a_400_everywhere(self):
        for url, method in (("/api/digest?now=tomorrow", "get"), ("/api/upcoming?now=2026-13-01", "get"),
                            ("/api/digest?now=2026-11-10T09:00Z", "get"), ("/api/reminders/run?now=x", "post"),
                            ("/api/digest/Rahul/preview?now=x", "get")):
            with self.subTest(url):
                self.assertEqual(getattr(self.client, method)(url).status_code, 400)

    def test_without_now_the_real_clock_is_used(self):
        self.assertTrue(self.client.get("/api/digest").get_json()["now"].startswith(str(datetime.now().year)))


class Preview(Base):
    def test_text_of_the_email(self):
        self.live("Draft the architecture doc", "Rahul", "2026-11-05")
        self.live("Ship pricing page", "Rahul", "2026-11-14")
        d = self.live("Set up staging", "Rahul", "2026-11-01"); self.done(d, "2026-11-08T10:00")
        self.meeting([item("decision", "Use Postgres.", topic="database")], date="2026-11-06", title="Kickoff")
        with self.app.app_context():
            db.get_db().execute("UPDATE items SET review_status='approved' WHERE kind='decision'"); db.get_db().commit()
        r = self.client.get("/api/digest/rahul/preview?now=2026-11-10T12:00")       # name match ignores case
        self.assertEqual(r.status_code, 200)
        out = r.get_json()
        self.assertEqual(out["person"], "Rahul")
        self.assertEqual(out["subject"], "Meetloop weekly digest for Rahul: 1 overdue, 1 due soon")
        body = out["body"]
        for needle in ("Hi Rahul,", "OVERDUE (1)", "- Draft the architecture doc (due 2026-11-05) https://github.com/acme/site/issues/101",
                       "DUE IN THE NEXT 7 DAYS (1)", "Ship pricing page (due 2026-11-14)", "DONE IN THE LAST 7 DAYS (1)",
                       "Set up staging (done 2026-11-08)", "NEW DECISIONS THIS WEEK (1)", "Use Postgres. [database] — Kickoff, 2026-11-06"):
            self.assertIn(needle, body)

    def test_unassigned_and_unknown(self):
        self.live("Nobody's", None, "2026-11-12")
        out = self.client.get("/api/digest/Unassigned/preview?now=2026-11-10").get_json()
        self.assertIn("Hi team,", out["body"]); self.assertIn("no owner", out["body"])
        r = self.client.get("/api/digest/Nobody/preview?now=2026-11-10")
        self.assertEqual(r.status_code, 404)

    def test_empty_sections_say_so(self):
        self.live("Only soon", "Rahul", "2026-11-12")
        body = self.client.get("/api/digest/Rahul/preview?now=2026-11-10").get_json()["body"]
        self.assertIn("OVERDUE (0)\n- Nothing.", body)


class Pages(Base):
    def test_pages_and_nav(self):
        for path in ("/reminders", "/digest", "/", "/reminders?now=2026-11-03T09:00"):
            r = self.client.get(path); html = r.get_data(as_text=True); r.close()
            self.assertEqual(r.status_code, 200, path)
            for label in ("Meetings", "Reminders", "Digest"):
                self.assertIn(f">{label}</a>", html, path)

    def test_old_database_gets_done_at_and_reminders(self):
        fd, old = tempfile.mkstemp(suffix=".db"); os.close(fd)
        try:
            c = sqlite3.connect(old)
            c.executescript("""CREATE TABLE meetings (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL DEFAULT 'x',
                meeting_date TEXT, raw_transcript TEXT NOT NULL, cleaned_transcript TEXT NOT NULL,
                cleaning_log TEXT NOT NULL DEFAULT '[]', extractor TEXT NOT NULL DEFAULT 'rules', fallback_reason TEXT,
                created_at TEXT NOT NULL DEFAULT '');
                CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id INTEGER NOT NULL, position INTEGER NOT NULL,
                kind TEXT NOT NULL, description TEXT NOT NULL, topic TEXT, owner TEXT, deadline TEXT, deadline_raw TEXT,
                deadline_note TEXT, speaker TEXT, source_text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
                needs_review INTEGER NOT NULL DEFAULT 0, review_reasons TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL DEFAULT '',
                review_status TEXT NOT NULL DEFAULT 'pending', issue_number INTEGER, issue_url TEXT, post_claimed_at TEXT);
                INSERT INTO meetings (raw_transcript, cleaned_transcript) VALUES ('a','a');
                INSERT INTO items (meeting_id, position, kind, description, source_text) VALUES (1, 0, 'action_item', 'Old', 'q');""")
            c.commit(); c.close()
            client = create_app(old).test_client()
            self.assertEqual(client.post("/api/items/1/done").status_code, 200)
            self.assertEqual(client.get("/api/reminders").get_json(), [])
        finally:
            os.remove(old)


class Scheduler(unittest.TestCase):
    """APScheduler itself is stood in for; what matters here is when, how often and with what it's started."""
    def setUp(self):
        scheduler.stop()
        self.addCleanup(scheduler.stop)
        self.sched = mock.MagicMock()
        mod = types.ModuleType("apscheduler.schedulers.background"); mod.BackgroundScheduler = mock.MagicMock(return_value=self.sched)
        p = mock.patch.dict(sys.modules, {"apscheduler": types.ModuleType("apscheduler"),
                                           "apscheduler.schedulers": types.ModuleType("apscheduler.schedulers"),
                                           "apscheduler.schedulers.background": mod})
        p.start(); self.addCleanup(p.stop)
        e = mock.patch.dict(os.environ, {"WERKZEUG_RUN_MAIN": "", "MEETLOOP_SCHEDULER": "on"}); e.start(); self.addCleanup(e.stop)

    def test_started_once_every_60_seconds(self):
        app = object()
        self.assertIs(scheduler.start(app), self.sched)
        self.assertIsNone(scheduler.start(app))                      # a second call does nothing
        self.sched.start.assert_called_once()
        self.sched.add_job.assert_called_once()
        args, kw = self.sched.add_job.call_args
        self.assertEqual((args[0], args[1], kw["seconds"], kw["max_instances"]), (scheduler.tick, "interval", 60, 1))

    def test_debug_reloader_watcher_process_does_not_start_it(self):
        self.assertIsNone(scheduler.start(object(), debug=True))     # the parent that only watches files
        self.sched.start.assert_not_called()
        with mock.patch.dict(os.environ, {"WERKZEUG_RUN_MAIN": "true"}):    # the child that serves requests
            self.assertIs(scheduler.start(object(), debug=True), self.sched)

    def test_can_be_switched_off(self):
        with mock.patch.dict(os.environ, {"MEETLOOP_SCHEDULER": "off"}):
            self.assertIsNone(scheduler.start(object()))

    def test_create_app_never_starts_it(self):
        fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        try:
            create_app(path)
        finally:
            os.remove(path)
        self.sched.start.assert_not_called()

    def test_tick_runs_the_job_and_survives_errors(self):
        fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        try:
            app = create_app(path)
            scheduler.tick(app)                                      # empty database: fine
            with mock.patch.object(reminders, "run", side_effect=RuntimeError("boom")):
                scheduler.tick(app)                                  # must not raise
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
