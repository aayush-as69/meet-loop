"""Conflict detection + seed script. The API (llm_extractor._post) is mocked: no network needed."""
import contextlib, io, json, os, re, tempfile, unittest
from unittest import mock

import conflicts, db, llm_extractor, seed
from app import create_app


def text_reply(obj):
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(obj)}]}


def dec(quote, desc, topic, deadline=None, raw=None):
    return {"kind": "decision", "description": desc, "owner": None, "deadline": deadline, "deadline_raw": raw,
            "speaker": "", "source_text": quote, "topic": topic, "review_reasons": []}


def act(quote, desc, owner, deadline=None, raw=None):
    return {"kind": "action_item", "description": desc, "owner": owner, "deadline": deadline, "deadline_raw": raw,
            "speaker": "", "source_text": quote, "topic": None, "review_reasons": []}


# What a good model would extract from the three demo meetings (keyed by a phrase from each).
EXTRACTIONS = {
    "kick off the launch": [
        dec("Postgres it is.", "Use Postgres as the database.", "database"),
        dec("we're launching on November 15th", "Launch on November 15th.", "launch_date", "2026-11-15", "November 15th"),
        dec("Let's go with Stripe for payments.", "Use Stripe for payments.", "payments"),
        dec("We decided to go with the new pricing page for the November release.", "Ship the new pricing page in the November release.", "pricing_page"),
        act("Rahul, can you draft the architecture doc?\nSure.\nBy next Tuesday please.", "Draft the architecture doc.", "Rahul", "2026-10-13", "next Tuesday"),
        act("I'll set up the staging environment by Friday, no wait, Thursday.", "Set up staging.", "Meera", "2026-10-08", "Thursday"),
        act("We need to update the onboarding docs.", "Update the onboarding docs.", None),
    ],
    "security review is running late": [
        dec("let's push launch to December 1st", "Push launch to December 1st.", "launch_date", "2026-12-01", "December 1st"),
        dec("we're sticking with Postgres", "Stay with Postgres.", "database"),
        dec("Fine, let's go with Adyen for payments.", "Use Adyen for payments.", "payments"),
        act("Meera, can you book the demo environment?\nGot it.", "Book the demo environment.", "Meera"),
        act("I'll send the revised timeline to the client by Friday, no, the date is on Thursday.", "Send the revised timeline.", "Rahul", "2026-10-22", "Thursday"),
    ],
    "Ops told me": [
        dec("we're switching to MySQL for the database", "Switch the database to MySQL.", "database"),
        dec("let's launch on November 15th after all", "Launch on November 15th.", "launch_date", "2026-11-15", "November 15th"),
        dec("Let's hold the new pricing page until the December release", "Hold the pricing page until December.", "pricing_page"),
        act("I'll update the invoice template by end of next week.", "Update the invoice template.", "Meera", "2026-11-13", "end of next week"),
        act("Rahul, please migrate the staging data to MySQL.\nOn it.", "Migrate staging data to MySQL.", "Rahul"),
    ],
}

# What a good model would say about each new decision (keyed by its description).
VERDICTS = {
    "Push launch to December 1st.": ("Launch moved from Nov 15 to Dec 1.", "high"),
    "Use Adyen for payments.": ("Replaces Stripe as the payments provider.", "medium"),
    "Switch the database to MySQL.": ("Reverses the earlier choice of Postgres.", "high"),
    "Launch on November 15th.": ("Reverts the Dec 1 delay.", "high"),
    "Hold the pricing page until December.": ("Delays the pricing page that was planned for November.", "low"),
}


def fake_post(payload, key, timeout):
    user = payload["messages"][0]["content"]
    if "Meeting date:" in user:                              # extraction call
        for marker, items in EXTRACTIONS.items():
            if marker in user:
                return text_reply({"items": items})
        raise AssertionError("unexpected transcript")
    m = re.search(r"New decision: (\w+): (.*)", user)        # conflict call
    topic, text = m.group(1), m.group(2)
    past = [l for l in user.splitlines() if l.startswith("- id=") and f"| {topic} |" in l]
    if text in VERDICTS and past:
        reason, sev = VERDICTS[text]
        # names the most recent earlier decision on the same topic
        return text_reply({"conflict": True, "conflicting_id": int(re.search(r"id=(\d+)", past[-1]).group(1)),
                           "reason": reason, "severity": sev})
    return text_reply({"conflict": False, "conflicting_id": None, "reason": "", "severity": "low"})


class Base(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MEETLOOP_EXTRACTOR": "auto"})
        p.start(); self.addCleanup(p.stop)
        fd, self.path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        self.addCleanup(os.remove, self.path)
        self.app = create_app(self.path)
        self.client = self.app.test_client()

    def post_meeting(self, transcript, date, title="m"):
        return self.client.post("/api/extract", json={"transcript": transcript, "meeting_date": date, "title": title}).get_json()


class Prompt(unittest.TestCase):
    def test_render_fills_the_supplied_template(self):
        past = [{"id": 4, "meeting_date": "2026-10-05", "topic": "database", "description": "Use Postgres."},
                {"id": 7, "meeting_date": None, "topic": None, "description": "Use {topic} {text}."}]
        out = conflicts._render(past, "database", "Switch to MySQL.")
        self.assertIn("Earlier decisions (id, meeting date, topic, text): \n- id=4 | 2026-10-05 | database | Use Postgres.", out)
        self.assertIn("- id=7 | unknown date | general | Use {topic} {text}.", out)   # not substituted twice
        self.assertIn("New decision: database: Switch to MySQL.", out)
        self.assertIn('{"conflict": true|false, "conflicting_id": null, "reason": "", "severity": "low|medium|high"}', out)


class Parse(unittest.TestCase):
    def test_no_conflict(self):
        self.assertIsNone(conflicts._parse({"conflict": False, "conflicting_id": None, "reason": "", "severity": "low"}, {1}))

    def test_conflict(self):
        self.assertEqual(conflicts._parse({"conflict": True, "conflicting_id": "1", "reason": "x", "severity": "HIGH"}, {1}), (1, "x", "high"))

    def test_bad_answers_raise(self):
        for bad in ({"conflict": True, "conflicting_id": 99, "reason": "x", "severity": "low"},
                    {"conflict": True, "conflicting_id": 1, "reason": "", "severity": "low"},
                    {"conflict": True, "conflicting_id": 1, "reason": "x", "severity": "urgent"},
                    {"conflict": "yes", "conflicting_id": 1, "reason": "x", "severity": "low"}):
            with self.assertRaises(llm_extractor.LLMExtractionError):
                conflicts._parse(bad, {1})


class Pipeline(Base):
    def run_three(self):
        out = []
        with mock.patch.object(llm_extractor, "_post", side_effect=fake_post) as post:
            self.post = post
            for fname, title, when in seed.MEETINGS:
                with open(os.path.join(seed.HERE, "demo_meetings", fname), encoding="utf-8") as f:
                    out.append(self.post_meeting(f.read(), when, title))
        return out

    def test_conflicts_are_stored_with_reason_and_severity(self):
        m1, m2, m3 = self.run_three()
        self.assertEqual([m["extractor"] for m in (m1, m2, m3)], ["llm"] * 3)
        self.assertEqual(m1["conflicts"], [])                                  # nothing earlier to conflict with
        self.assertEqual({c["topic"]: c["severity"] for c in m2["conflicts"]}, {"launch_date": "high", "payments": "medium"})
        self.assertEqual({c["topic"]: c["severity"] for c in m3["conflicts"]},
                         {"database": "high", "launch_date": "high", "pricing_page": "low"})
        self.assertEqual([c["severity"] for c in m3["conflicts"]], ["high", "high", "low"])   # worst first
        c = next(c for c in m3["conflicts"] if c["topic"] == "database")
        self.assertEqual(c["conflicting_meeting_title"], "Planning sync")      # the most recent Postgres decision
        self.assertIn("Postgres", c["conflicting_description"])
        self.assertTrue(c["reason"])
        self.assertEqual(len(self.client.get("/api/conflicts").get_json()), 5)
        self.assertEqual(len(self.client.get("/api/conflicts?severity=high").get_json()), 3)
        self.assertEqual(self.client.get("/api/conflicts?severity=bad").status_code, 400)
        self.assertEqual({m["id"]: m["conflicts"] for m in self.client.get("/api/meetings").get_json()}, {3: 3, 2: 2, 1: 0})

    def test_each_new_decision_sees_all_earlier_decisions(self):
        self.run_three()
        conflict_calls = [c.args[0]["messages"][0]["content"] for c in self.post.call_args_list
                          if "Earlier decisions" in c.args[0]["messages"][0]["content"]]
        # meeting 1 has 4 decisions: first has no history (no call), the others see everything before them
        self.assertEqual(len(conflict_calls[0].split("\n- id=")) - 1, 1)
        last = conflict_calls[-1]
        self.assertEqual(len(last.split("\n- id=")) - 1, 9)        # the 10th decision sees the 9 before it
        self.assertIn("2026-10-05 | database | Use Postgres as the database.", last)
        self.assertIn("2026-10-19 | payments | Use Adyen for payments.", last)

    def test_conflicts_cascade_with_meeting_deletion_schema(self):
        self.run_three()
        with self.app.app_context():
            conn = db.get_db()
            conn.execute("DELETE FROM meetings WHERE id = 3")
            conn.commit()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM conflicts WHERE item_id IN (SELECT id FROM items)").fetchone()[0], 2)

    def test_api_failure_stops_checking_and_is_reported_not_stored(self):
        calls = []

        def flaky(payload, key, timeout):
            if "Meeting date:" in payload["messages"][0]["content"]:
                return fake_post(payload, key, timeout)
            calls.append(1)
            raise llm_extractor.LLMExtractionError("API returned HTTP 529")
        with mock.patch.object(llm_extractor, "_post", side_effect=flaky):
            m1 = self.post_meeting("Priya: kick off the launch. Postgres it is. We're going with Stripe for payments.", "2026-10-05")
            self.assertEqual(len(calls), 1)         # meeting 1 has 4 decisions, but the run stopped at the first failure
            m2 = self.post_meeting("Priya: security review is running late, so let's push launch to December 1st.", "2026-10-19")
        self.assertEqual(m2["conflicts"], [])
        self.assertEqual(len(calls), 2)
        self.assertIn("HTTP 529", m2["conflict_check"]["skipped_reason"])
        self.assertTrue(any("not fully checked" in w for w in m2["warnings"]))

    def test_garbage_conflict_answer_is_not_stored(self):
        def post(payload, key, timeout):
            if "Meeting date:" in payload["messages"][0]["content"]:
                return fake_post(payload, key, timeout)
            return text_reply({"conflict": True, "conflicting_id": 12345, "reason": "x", "severity": "high"})
        with mock.patch.object(llm_extractor, "_post", side_effect=post):
            self.post_meeting("Priya: kick off the launch. Postgres it is.", "2026-10-05")
            m = self.post_meeting("Priya: security review is running late, so let's push launch to December 1st.", "2026-10-19")
        self.assertEqual(m["conflicts"], [])

    def test_rules_mode_and_missing_key_skip_without_calling_api(self):
        with mock.patch.object(llm_extractor, "_post") as post:
            r = self.client.post("/api/extract", json={"transcript": "Priya: Let's go with Stripe for payments.", "extractor": "rules"})
            self.assertEqual(r.get_json()["conflict_check"]["skipped_reason"], "rules mode never calls the API")
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}):
                r = self.client.post("/api/extract", json={"transcript": "Priya: Let's go with Stripe for payments."})
            self.assertIn("ANTHROPIC_API_KEY", r.get_json()["conflict_check"]["skipped_reason"])
        post.assert_not_called()


class Seed(Base):
    def run_seed(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = seed.main(["--db", self.path, *argv])
        return code, buf.getvalue()

    def test_seed_loads_three_meetings_in_order_and_prints_conflicts(self):
        with mock.patch.object(llm_extractor, "_post", side_effect=fake_post):
            code, out = self.run_seed()
        self.assertEqual(code, 0)
        self.assertLess(out.index("Launch kickoff"), out.index("Planning sync"))
        self.assertLess(out.index("Planning sync"), out.index("Infra review"))
        self.assertIn("CONFLICTS: 5", out)
        self.assertIn("[HIGH  ] database", out)
        self.assertIn("Reverses the earlier choice of Postgres.", out)
        self.assertNotIn("did not complete", out)
        self.assertEqual(out.count("\n  why:"), 5)

    def test_seed_is_repeatable(self):
        with mock.patch.object(llm_extractor, "_post", side_effect=fake_post):
            self.run_seed(); _, out = self.run_seed()
        self.assertIn("CONFLICTS: 5", out)                        # fresh db each run, not 10

    def test_seed_offline_says_the_check_did_not_run(self):
        code, out = self.run_seed("--extractor", "rules")
        self.assertEqual(code, 0)
        self.assertIn("conflict checking did not complete", out)
        self.assertNotIn("(none found)", out)


if __name__ == "__main__":
    unittest.main()
