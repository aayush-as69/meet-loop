"""Runs the reminder job every 60 s in the background (APScheduler)."""
import logging
import os
from datetime import datetime

import reminders

log = logging.getLogger("meetloop.scheduler")
_scheduler = None          # one per process: a second start() is a no-op


def should_start(debug: bool) -> bool:
    """Flask's debug reloader runs the script twice: a parent that only watches files, and a child
    (WERKZEUG_RUN_MAIN=true) that serves. Only the process that serves may run the job."""
    if os.environ.get("MEETLOOP_SCHEDULER", "on").lower() in ("off", "0", "false", "no"):
        return False
    return not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true"


def tick(app) -> None:
    try:
        with app.app_context():
            result = reminders.run(datetime.now())
        if result["fired"]:
            log.info("fired %d reminder(s)", len(result["fired"]))
    except Exception:                       # never let one bad run kill the schedule
        log.exception("reminder job failed")


def start(app, debug: bool = False, interval: int = 60):
    """Start the background job once. Returns the scheduler, or None if it isn't (or is already) running."""
    global _scheduler
    if _scheduler is not None or not should_start(debug):
        return None
    from apscheduler.schedulers.background import BackgroundScheduler   # imported here so tests/tools don't need it
    sched = BackgroundScheduler(daemon=True)
    sched.add_job(tick, "interval", seconds=interval, args=[app], id="meetloop-reminders",
                  max_instances=1, coalesce=True, replace_existing=True, next_run_time=datetime.now())
    sched.start()
    _scheduler = sched
    return sched


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
