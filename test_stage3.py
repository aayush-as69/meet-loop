"""Stage 3: approve/edit items, confirm & post to GitHub. The HTTP call (github._post) is always mocked."""
import json, os, sqlite3, tempfile, unittest
from unittest import mock

import db, github
from app import create_app

TOKEN = "ghp_SECRET_TOKEN_123"


def item(kind, desc, **kw):
    base = {"kind": kind, "description": desc, "topic": None, "owner": None, "deadline": None, "deadline_raw": None,
            "deadline_note": None, "speaker": "Priya", "source_text": f"quote for {desc}", "needs_review": False,
            "review_reasons": []}
    base.update(kw)
    return base


ITEMS = [
    item("decision", "Use Postgres.", topic="database"),                                          # 1
    item("action_item", "Draft the architecture doc.", owner="Rahul", deadline="2026-10-13"),    # 2
    item("action_item", "Set up staging.", owner="Meera", deadline="2026-10-08"),                # 3
    item("action_item", "Update the onboarding docs.", needs_review=True, source_text="We need to\nupdate the docs.",
         review_reasons=[{"code": "owner_missing", "message": "No owner stated"},
                         {"code": "deadline_missing", "message": "No deadline was stated"},
                         {"code": "tentative", "message": "Wording is tentative"}]),             # 4
    item("action_item", "Book the demo environment.", owner="Meera"),                             # 5
]


class Base(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"GITHUB_TOKEN": TOKEN, "GITHUB_REPO": "acme/site", "MEETLOOP_BASE_URL": ""})
        p.start(); self.addCleanup(p.stop)
        fd, self.path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        self.addCleanup(os.remove, self.path)
        self.app = create_app(self.path)
        self.client = self.app.test_client()
        with self.app.app_context():
            self.mid = db.save_meeting("Launch kickoff", "2026-10-05", "raw", "clean", [], ITEMS)

    def patch(self, item_id, **body):
        return self.client.patch(f"/api/items/{item_id}", json=body)

    def approve(self, *ids):
        for i in ids:
            self.assertEqual(self.patch(i, status="approved").status_code, 200)

    def confirm(self):
        return self.client.post(f"/api/meetings/{self.mid}/confirm")

    def item(self, item_id):
        return next(i for i in self.client.get(f"/api/meetings/{self.mid}").get_json()["items"] if i["id"] == item_id)

    @staticmethod
    def issues():
        """Fake GitHub: numbers issues 101, 102, ..."""
        n = {"n": 100}
        def fake(url, headers, payload, timeout):
            n["n"] += 1
            return 201, {"number": n["n"], "html_url": f"https://github.com/acme/site/issues/{n['n']}"}
        return fake


class Confirm(Base):
    def test_posts_only_approved_action_items(self):
        self.approve(1, 2, 4)                    # a decision, a clean action item, a needs-review one
        self.patch(3, status="rejected")         # rejected; item 5 stays pending
        with mock.patch.object(github, "_post", side_effect=self.issues()) as post:
            r = self.confirm()
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual([p["item_id"] for p in body["posted"]], [2, 4])
        self.assertEqual((body["skipped"], body["errors"]), ([], []))
        self.assertEqual(post.call_count, 2)

        url, headers, payload, _ = post.call_args_list[0].args
        self.assertEqual(url, "https://api.github.com/repos/acme/site/issues")
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(payload["title"], "Draft the architecture doc.")
        for needle in ("Rahul", "2026-10-13", "> quote for Draft the architecture doc.", "Launch kickoff",
                       f"http://localhost/meetings/{self.mid}"):
            self.assertIn(needle, payload["body"])
        # the unowned, undated item still posts, and says so plainly; multi-line quote stays a quote
        second = post.call_args_list[1].args[2]["body"]
        self.assertIn("Unassigned", second); self.assertIn("No due date", second)
        self.assertIn("> We need to\n> update the docs.", second)

        a = self.item(2)
        self.assertEqual((a["issue_number"], a["issue_url"], a["posted"]), (101, "https://github.com/acme/site/issues/101", True))
        for i in (1, 3, 5):                       # decisions, rejected, pending: never posted
            self.assertIsNone(self.item(i)["issue_number"])

    def test_second_click_posts_nothing(self):
        self.approve(2, 3)
        with mock.patch.object(github, "_post", side_effect=self.issues()) as post:
            first = self.confirm().get_json()
            second = self.confirm().get_json()
        self.assertEqual(post.call_count, 2)
        self.assertEqual(second["posted"], [])
        self.assertEqual(second["errors"], [])
        self.assertEqual([(s["item_id"], s["issue_number"]) for s in second["skipped"]],
                         [(p["item_id"], p["issue_number"]) for p in first["posted"]])
        self.assertEqual(self.item(2)["issue_number"], 101)       # unchanged

    def test_one_failure_does_not_stop_the_others(self):
        self.approve(2, 3, 5)
        good = self.issues()
        def flaky(url, headers, payload, timeout):
            if payload["title"] == "Set up staging.":
                return 422, {"message": "Validation Failed"}
            return good(url, headers, payload, timeout)
        with mock.patch.object(github, "_post", side_effect=flaky):
            body = self.confirm().get_json()
        self.assertEqual([p["item_id"] for p in body["posted"]], [2, 5])
        self.assertEqual([e["item_id"] for e in body["errors"]], [3])
        self.assertIn("Validation Failed", body["errors"][0]["error"])
        self.assertNotIn(TOKEN, json.dumps(body))
        self.assertIsNone(self.item(3)["issue_number"])
        self.assertEqual(self.item(3)["status"], "approved")       # still approved: a retry is one click

        with mock.patch.object(github, "_post", side_effect=self.issues()) as post:     # retry
            body = self.confirm().get_json()
        self.assertEqual(post.call_count, 1)                       # only the failed one is retried
        self.assertEqual([p["item_id"] for p in body["posted"]], [3])
        self.assertEqual(len(body["skipped"]), 2)

    def test_network_error_is_reported_not_raised(self):
        self.approve(2)
        with mock.patch.object(github, "_post", side_effect=TimeoutError("boom")):
            r = self.confirm()
        self.assertEqual(r.status_code, 200)
        self.assertIn("Couldn't reach GitHub", r.get_json()["errors"][0]["error"])
        self.assertIsNone(self.item(2)["issue_number"])

    def test_missing_token_is_a_clear_400_and_posts_nothing(self):
        self.approve(2, 3)
        for var, word in (("GITHUB_TOKEN", "GITHUB_TOKEN"), ("GITHUB_REPO", "GITHUB_REPO")):
            with self.subTest(var), mock.patch.dict(os.environ, {var: ""}), \
                    mock.patch.object(github, "_post") as post:
                r = self.confirm()
            self.assertEqual(r.status_code, 400)
            self.assertIn(word, r.get_json()["error"])
            self.assertIn("nothing was posted", r.get_json()["error"])
            post.assert_not_called()
        self.assertIsNone(self.item(2)["issue_number"])

    def test_malformed_repo_is_a_400(self):
        self.approve(2)
        with mock.patch.dict(os.environ, {"GITHUB_REPO": "https://github.com/acme/site"}), \
                mock.patch.object(github, "_post") as post:
            r = self.confirm()
        self.assertEqual(r.status_code, 400); post.assert_not_called()

    def test_unknown_meeting_is_404(self):
        self.assertEqual(self.client.post("/api/meetings/999/confirm").status_code, 404)

    def test_item_being_posted_elsewhere_is_skipped(self):
        """A concurrent confirm holds the claim: this one must not create a second issue."""
        self.approve(2)
        with self.app.app_context():
            self.assertTrue(db.claim_for_posting(2))
        with mock.patch.object(github, "_post") as post:
            body = self.confirm().get_json()
        post.assert_not_called()
        self.assertEqual([s["item_id"] for s in body["skipped"]], [2])
        # ...but a claim left behind by a crashed post expires
        with self.app.app_context():
            db.get_db().execute("UPDATE items SET post_claimed_at = '2000-01-01T00:00:00Z' WHERE id = 2")
            db.get_db().commit()
        with mock.patch.object(github, "_post", side_effect=self.issues()) as post:
            self.assertEqual(len(self.confirm().get_json()["posted"]), 1)

    def test_token_never_appears_in_errors(self):
        self.approve(2)
        with mock.patch.object(github, "_post", return_value=(401, {"message": f"Bad credentials {TOKEN}"})):
            r = self.confirm()
        self.assertNotIn(TOKEN, r.get_data(as_text=True))
        self.assertIn("rejected the token", r.get_json()["errors"][0]["error"])


class GitHubClient(unittest.TestCase):
    def test_request_shape(self):
        class Resp:
            status = 201
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s): return b'{"number": 7, "html_url": "https://github.com/a/b/issues/7"}'
        with mock.patch("urllib.request.urlopen", return_value=Resp()) as op:
            out = github.create_issue("tok", "a/b", "T", "B")
        self.assertEqual(out, {"number": 7, "url": "https://github.com/a/b/issues/7"})
        req = op.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.github.com/repos/a/b/issues")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Authorization"), "Bearer tok")
        self.assertEqual(json.loads(req.data), {"title": "T", "body": "B"})

    def test_http_errors_become_readable_messages(self):
        for status, text in ((401, "rejected the token"), (404, "not found"), (403, "Issues write access")):
            with mock.patch.object(github, "_post", return_value=(status, {})):
                with self.assertRaises(github.GitHubError) as cm:
                    github.create_issue("tok", "a/b", "T", "B")
            self.assertIn(text, str(cm.exception))


class Patch(Base):
    def test_status_round_trip(self):
        for s in ("approved", "rejected", "pending"):
            self.assertEqual(self.patch(2, status=s).get_json()["status"], s)
        self.assertEqual(self.item(2)["status"], "pending")

    def test_editing_owner_clears_only_owner_reasons(self):
        r = self.patch(4, owner="  Priya ").get_json()
        self.assertEqual(r["owner"], "Priya")
        self.assertEqual([x["code"] for x in r["review_reasons"]], ["deadline_missing", "tentative"])
        self.assertTrue(r["needs_review"])

    def test_editing_deadline_clears_deadline_reasons_and_flag_drops_when_none_left(self):
        self.patch(4, owner="Priya")
        r = self.patch(4, deadline="2026-11-20").get_json()
        self.assertEqual((r["deadline"], [x["code"] for x in r["review_reasons"]]), ("2026-11-20", ["tentative"]))
        self.assertTrue(r["needs_review"])           # "tentative" is about the wording; an edit doesn't answer it
        with self.app.app_context():                 # a card whose only reason was the deadline
            mid = db.save_meeting("m2", None, "r", "c", [], [item("action_item", "x", owner="A", needs_review=True,
                                  review_reasons=[{"code": "deadline_unresolved", "message": "?"}])])
            iid = db.get_meeting(mid)["items"][0]["id"]
        r = self.patch(iid, deadline="2026-12-01").get_json()
        self.assertEqual((r["needs_review"], r["review_reasons"]), (False, []))

    def test_clearing_a_field_does_not_clear_its_reasons(self):
        r = self.patch(4, owner="").get_json()
        self.assertIsNone(r["owner"]); self.assertEqual(len(r["review_reasons"]), 3)

    def test_validation(self):
        bad = [(2, {"status": "done"}), (2, {"deadline": "next friday"}), (2, {"deadline": 5}), (2, {"owner": 7}),
               (2, {"owner": "x" * 101}), (2, {"colour": "red"}), (1, {"owner": "Priya"}), (1, {"deadline": "2026-11-20"}), (2, {})]
        for iid, body in bad:
            with self.subTest(body):
                self.assertEqual(self.patch(iid, **body).status_code, 400)
        self.assertEqual(self.patch(999, status="approved").status_code, 404)
        self.assertEqual(self.client.patch("/api/items/2", data="nope").status_code, 400)

    def test_posted_items_are_locked(self):
        self.approve(2)
        with mock.patch.object(github, "_post", side_effect=self.issues()):
            self.confirm()
        for body in ({"status": "rejected"}, {"owner": "Someone else"}, {"deadline": "2027-01-01"}):
            r = self.patch(2, **body)
            self.assertEqual(r.status_code, 409)
            self.assertIn("#101", r.get_json()["error"])
        self.assertEqual(self.item(2)["owner"], "Rahul")

    def test_meeting_api_exposes_review_fields(self):
        it = self.item(2)
        self.assertEqual((it["status"], it["issue_number"], it["issue_url"], it["posted"]), ("pending", None, None, False))
        self.assertEqual(it["lifecycle_status"], "open")
        self.assertNotIn("post_claimed_at", it)


class Pages(Base):
    def test_pages_are_served(self):
        for path in ("/", f"/meetings/{self.mid}"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200)
            self.assertIn("cdn.tailwindcss.com", r.get_data(as_text=True))
            r.close()

    def test_home_counts(self):
        row = self.client.get("/api/meetings").get_json()[0]
        self.assertEqual((row["decisions"], row["action_items"], row["needs_review"]), (1, 4, 1))

    def test_old_database_gets_the_new_columns(self):
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
                needs_review INTEGER NOT NULL DEFAULT 0, review_reasons TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL DEFAULT '');
                INSERT INTO meetings (raw_transcript, cleaned_transcript) VALUES ('a','a');
                INSERT INTO items (meeting_id, position, kind, description, source_text) VALUES (1, 0, 'action_item', 'Old item', 'q');""")
            c.commit(); c.close()
            client = create_app(old).test_client()
            it = client.get("/api/meetings/1").get_json()["items"][0]
            self.assertEqual((it["status"], it["issue_number"]), ("pending", None))
            self.assertEqual(client.patch("/api/items/1", json={"status": "approved"}).status_code, 200)
        finally:
            os.remove(old)


if __name__ == "__main__":
    unittest.main()
