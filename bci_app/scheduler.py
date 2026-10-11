"""Background reminder scheduler.

Every minute it checks the clock (Kenya time). During a reminder's send window,
for example Wednesday from 10:00 until the latest send time, it sends that
reminder to anyone who is due and hasn't had it today. Because it keeps checking
through the window, the reminders still go out if the office computer is switched
on late, and cards entered later in the day are picked up too.

The scheduler only runs while the app is running, so keep the app open on the
office computer on Wednesdays and Saturdays.
"""
import logging
import threading
import time

from .db import connect, get_settings, set_settings
from .followup import FOLLOWUP_CAMPAIGNS, in_send_window, refresh_statuses, run_campaign
from .sms import get_provider
from .utils import now_eat

log = logging.getLogger(__name__)
_started = False


def tick(app):
    conn = connect(app.config["DATABASE"])
    try:
        settings = get_settings(conn)
        now = now_eat()
        refresh_statuses(conn, settings, now.date())
        if settings.get("auto_send") == "1":
            provider = get_provider(app.config)
            for campaign in FOLLOWUP_CAMPAIGNS:
                if in_send_window(settings, campaign, now):
                    run_campaign(conn, provider, campaign, now.date(), triggered_by="auto")
        set_settings({"scheduler_last_tick": now.strftime("%Y-%m-%d %H:%M:%S")}, conn)
    finally:
        conn.close()


def _loop(app, interval):
    time.sleep(3)
    while True:
        try:
            tick(app)
        except Exception:  # noqa: BLE001 - keep the scheduler alive
            log.exception("Scheduler check failed")
        time.sleep(interval)


def start(app, interval=60):
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, args=(app, interval), name="sms-scheduler", daemon=True).start()
    log.info("Reminder scheduler started (checks every %s seconds)", interval)
