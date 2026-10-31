"""Load the three demo meetings in order and print the conflicts found.

    python seed.py                 # LLM extractor + conflict check (needs ANTHROPIC_API_KEY in .env)
    python seed.py --extractor rules   # offline: rule-based extraction, no conflict check
    python seed.py --db my.db --keep   # keep an existing database instead of starting fresh

Meetings are posted through the real /api/extract endpoint, so this exercises exactly the code
path the app uses. Default database is meetloop_demo.db, recreated each run.
"""
from __future__ import annotations

import argparse
import os
import sys

import config

config.load_env()
from app import create_app  # noqa: E402  (after load_env so .env is honoured)

HERE = os.path.dirname(os.path.abspath(__file__))
MEETINGS = [  # (file, title, meeting date) — loaded in this order
    ("01_kickoff.txt", "Launch kickoff", "2026-10-05"),
    ("02_planning.txt", "Planning sync", "2026-10-19"),
    ("03_infra_review.txt", "Infra review", "2026-11-02"),
]
SEV_MARK = {"high": "HIGH  ", "medium": "MEDIUM", "low": "LOW   "}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.path.join(HERE, "meetloop_demo.db"))
    ap.add_argument("--keep", action="store_true", help="don't delete the database first")
    ap.add_argument("--extractor", choices=("auto", "rules"), default=None)
    args = ap.parse_args(argv)

    if not args.keep and os.path.exists(args.db):
        os.remove(args.db)
    client = create_app(args.db).test_client()

    skipped = []
    print(f"Database: {args.db}\n")
    for fname, title, when in MEETINGS:
        with open(os.path.join(HERE, "demo_meetings", fname), encoding="utf-8") as f:
            transcript = f.read()
        body = {"transcript": transcript, "title": title, "meeting_date": when}
        if args.extractor:
            body["extractor"] = args.extractor
        r = client.post("/api/extract", json=body)
        data = r.get_json()
        if r.status_code != 201:
            print(f"!! {title}: HTTP {r.status_code} {data}")
            return 1
        decisions = [i for i in data["items"] if i["kind"] == "decision"]
        actions = [i for i in data["items"] if i["kind"] == "action_item"]
        used = data["extractor"] + (f" (fell back: {data['fallback_reason']})" if data["fallback_reason"] else "")
        print(f"[{data['id']}] {when}  {title}  -  {len(decisions)} decisions, {len(actions)} action items, "
              f"{sum(i['needs_review'] for i in data['items'])} need review  |  extractor: {used}")
        for i in decisions:
            print(f"      decision [{i['topic']}] {i['description']}")
        cc = data["conflict_check"]
        if cc["skipped_reason"]:
            skipped.append(f"{title}: {cc['skipped_reason']}")
            print(f"      ! conflict check: {cc['skipped_reason']}")
        print()

    all_conflicts = client.get("/api/conflicts").get_json()
    print("=" * 72)
    print(f"CONFLICTS: {len(all_conflicts)}")
    print("=" * 72)
    for c in all_conflicts:
        print(f"\n[{SEV_MARK[c['severity']]}] {c['topic']}")
        print(f"  new  ({c['meeting_date']}, {c['meeting_title']}): {c['description']}")
        print(f"  old  ({c['conflicting_meeting_date']}, {c['conflicting_meeting_title']}): {c['conflicting_description']}")
        print(f"  why: {c['reason']}")
    if skipped:
        print("\nNOTE: conflict checking did not complete, so the list above may be incomplete:")
        for line in skipped:
            print(f"  - {line}")
    elif not all_conflicts:
        print("\n(none found)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
