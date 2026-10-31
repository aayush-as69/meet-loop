"""Stage 5: the New Meeting page is served and linked. (Its behaviour is browser-side JS.)"""
import os, tempfile, unittest

from app import create_app


class NewMeetingPage(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        self.addCleanup(os.remove, self.path)
        self.client = create_app(self.path).test_client()

    def html(self, path):
        r = self.client.get(path); self.assertEqual(r.status_code, 200, path)
        try:
            return r.get_data(as_text=True)
        finally:
            r.close()

    def test_page_is_served_and_linked_from_nav_and_home(self):
        for path in ("/new", "/", "/reminders", "/digest"):
            self.assertIn('data-nav="/new"', self.html(path))        # nav link on every page
        page = self.html("/new")
        for needle in ("function showNew", "/api/extract", "One speaker per line", ".vtt"):
            self.assertIn(needle, page)
        self.assertIn("href: withNow('/new')", page)                   # home page button + empty-state link

    def test_extract_endpoint_it_calls_still_returns_id_items_and_warnings(self):
        r = self.client.post("/api/extract", json={"transcript": "Priya: Let's go with Postgres for the database.",
                                                   "title": "T", "meeting_date": "2026-10-31", "extractor": "rules"})
        body = r.get_json()
        self.assertEqual(r.status_code, 201)
        self.assertTrue(isinstance(body["id"], int) and isinstance(body["items"], list) and isinstance(body["warnings"], list))


if __name__ == "__main__":
    unittest.main()
