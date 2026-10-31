import os, tempfile, unittest

os.environ["MEETLOOP_EXTRACTOR"] = "rules"   # these tests target the rule-based extractor
from datetime import date

from app import create_app
from extractor import extract

MEETING = date(2026, 10, 28)  # a Wednesday


def items_for(text, d=MEETING):
    return extract(text, d)["items"]


class Extraction(unittest.TestCase):
    def test_final_correction_wins(self):
        (it,) = items_for("Rahul: I'll send the deck by Friday, no, the date is on Thursday.")
        self.assertEqual(it["deadline"], "2026-10-29")
        self.assertEqual(it["owner"], "Rahul")

    def test_owner_correction(self):
        r = extract("Priya: ok\nRahul: ok\nPriya: Priya, no, Rahul will send the deck by Friday.", MEETING)
        (it,) = r["items"]
        self.assertEqual(it["owner"], "Rahul")

    def test_fillers_and_stutter(self):
        r = extract("Rahul: Um, I'll, uh, I'll send the deck.", MEETING)
        self.assertEqual(r["cleaned_transcript"], "Rahul: I'll send the deck.")

    def test_next_friday_resolves_to_next_calendar_week(self):
        (it,) = items_for("Meera: I'll ship it next Friday.")
        self.assertEqual(it["deadline"], "2026-11-06")
        self.assertIn("nearest Friday", it["deadline_note"])

    def test_missing_owner_and_deadline_are_flagged_not_invented(self):
        (it,) = items_for("Priya: We need to update the onboarding docs.")
        self.assertIsNone(it["owner"])
        self.assertIsNone(it["deadline"])
        self.assertTrue(it["needs_review"])
        codes = {r["code"] for r in it["review_reasons"]}
        self.assertEqual(codes, {"owner_missing", "deadline_missing"})

    def test_no_meeting_date_means_unresolved_not_guessed(self):
        (it,) = items_for("Rahul: I'll send it next Friday.", d=None)
        self.assertIsNone(it["deadline"])
        self.assertEqual(it["deadline_raw"], "next Friday")
        self.assertIn("deadline_unresolved", {r["code"] for r in it["review_reasons"]})

    def test_vague_period_is_not_turned_into_a_date(self):
        (it,) = items_for("Rahul: I'll send it next week.")
        self.assertIsNone(it["deadline"])
        self.assertTrue(it["needs_review"])

    def test_conflicting_dates_are_not_picked(self):
        (it,) = items_for("Rahul: I'll draft it Friday and review it Monday.")
        self.assertIsNone(it["deadline"])
        self.assertIn("multiple_dates", {r["code"] for r in it["review_reasons"]})

    def test_addressed_request_then_acceptance_merges(self):
        r = extract("Priya: Meera, can you book the demo room?\nMeera: Sure, I'll do it by Friday.", MEETING)
        (it,) = r["items"]
        self.assertEqual(it["owner"], "Meera")
        self.assertEqual(it["deadline"], "2026-10-30")
        self.assertFalse(it["needs_review"])

    def test_you_without_name_stays_unowned_if_nobody_accepts(self):
        (it,) = items_for("Priya: Can you book the demo room?\nRahul: We should also check pricing.")[:1]
        self.assertIsNone(it["owner"])
        self.assertTrue(it["needs_review"])

    def test_acceptance_without_date_keeps_request_deadline(self):
        for reply in ("Got it.", "Sure."):
            with self.subTest(reply=reply):
                r = extract("Priya: Aman, please prepare the demo video by next Tuesday.\nAman: " + reply, MEETING)
                (it,) = r["items"]
                self.assertEqual(it["owner"], "Aman")
                self.assertEqual(it["deadline"], "2026-11-03")
                self.assertEqual(it["deadline_raw"], "next Tuesday")
                self.assertFalse(it["needs_review"])

    def test_acceptance_with_its_own_date_wins(self):
        r = extract("Priya: Aman, please prepare the demo video by next Tuesday.\nAman: Sure, Wednesday works.", MEETING)
        (it,) = r["items"]
        self.assertEqual(it["owner"], "Aman")
        self.assertEqual(it["deadline_raw"], "Wednesday")
        self.assertEqual(it["deadline"], "2026-11-04")      # meeting is itself a Wednesday -> the one a week later
        self.assertIn("next Tuesday", it["deadline_note"])  # audit trail of what was replaced
        # said on a Wednesday, so "Wednesday" is genuinely ambiguous: flagged, not silently trusted
        self.assertIn("deadline_ambiguous", {x["code"] for x in it["review_reasons"]})
        self.assertNotIn("owner_missing", {x["code"] for x in it["review_reasons"]})

    def test_unrelated_date_remark_does_not_override_deadline(self):
        r = extract("Priya: Aman, please prepare the demo video by next Tuesday.\nAman: Thursday is my birthday.", MEETING)
        (it,) = r["items"]
        self.assertEqual(it["deadline"], "2026-11-03")

    def test_decision_has_no_owner_and_no_review(self):
        (it,) = items_for("Priya: We decided to go with the new pricing page.")
        self.assertEqual(it["kind"], "decision")
        self.assertFalse(it["needs_review"])

    def test_non_action_statement_ignored(self):
        self.assertEqual(items_for("Priya: Stripe will charge two percent per transaction."), [])


class Api(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        self.client = create_app(self.path).test_client()

    def tearDown(self):
        os.remove(self.path)

    def test_extract_persist_and_fetch(self):
        text = open(os.path.join(os.path.dirname(__file__), "sample_transcript.txt")).read()
        r = self.client.post("/api/extract", json={"transcript": text, "meeting_date": "2026-10-28", "title": "Launch sync"})
        self.assertEqual(r.status_code, 201)
        body = r.get_json()
        self.assertEqual(len(body["items"]), 8)
        self.assertEqual(len(body["cleaning_log"]), 1)
        got = self.client.get(f"/api/meetings/{body['id']}").get_json()
        self.assertEqual(got["items"], body["items"])
        listing = self.client.get("/api/meetings").get_json()
        self.assertEqual(listing[0]["decisions"], 2)
        self.assertEqual(listing[0]["action_items"], 6)
        self.assertEqual(listing[0]["needs_review"], 3)

    def test_validation(self):
        self.assertEqual(self.client.post("/api/extract", json={}).status_code, 400)
        self.assertEqual(self.client.post("/api/extract", json={"transcript": "  "}).status_code, 400)
        self.assertEqual(self.client.post("/api/extract", json={"transcript": "A: hi", "meeting_date": "nope"}).status_code, 400)
        self.assertEqual(self.client.get("/api/meetings/999").status_code, 404)

    def test_dry_run_does_not_save(self):
        r = self.client.post("/api/extract", json={"transcript": "Rahul: I'll send it Friday.", "meeting_date": "2026-10-28", "save": False})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/meetings").get_json(), [])


if __name__ == "__main__":
    unittest.main()
