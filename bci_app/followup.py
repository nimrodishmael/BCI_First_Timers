"""Follow-up rules: who gets which reminder, and sending them.

Each first timer receives ``followup_weeks`` (default 4) reminders of each kind:

* the *thursday* reminder, sent on Wednesday, inviting them to the Dominion service
* the *sunday* reminder, sent on Saturday evening, inviting them to Sunday service

A reminder is counted once it has been attempted, whether the network accepted it
or not, so a wrong number never gets retried forever. Failed messages can be resent
from the Messages page. After the last reminder of both kinds the person is marked
"Follow-up complete".
"""
import logging
import threading
from datetime import datetime, timedelta

from .db import get_settings
from .utils import EAT, first_word_title, now_eat, parse_hhmm

log = logging.getLogger(__name__)

CAMPAIGNS = {
    "thursday": "Thursday Dominion reminder",
    "sunday": "Sunday service reminder",
    "broadcast": "Custom message",
    "test": "Test message",
}
FOLLOWUP_CAMPAIGNS = ("thursday", "sunday")

# One send at a time per running app, so a manual "Send now" and the scheduler
# never hand the same message to the gateway twice.
_send_lock = threading.Lock()


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def template_values(settings, first_name="friend"):
    return _SafeDict(
        first_name=first_word_title(first_name),
        church=settings.get("church_name", ""),
        church_short=settings.get("church_short", ""),
        thursday_service=settings.get("thursday_service_name", "Dominion Service"),
        thursday_time=settings.get("thursday_service_time", ""),
        sunday_time=settings.get("sunday_service_time", ""),
        location=settings.get("church_location", ""),
    )


def render_message(template, settings, first_name="friend"):
    text = (template or "").format_map(template_values(settings, first_name))
    return " ".join(text.split())  # tidy double spaces left by empty placeholders


def weeks_setting(settings):
    try:
        return max(1, min(12, int(settings.get("followup_weeks") or 4)))
    except ValueError:
        return 4


def window_start(run_date, weeks):
    """Oldest visit date that still qualifies for reminders.

    Gives the office two extra weeks to load cards late without the visitor
    missing out, while old cards entered for record keeping get no SMS.
    """
    return run_date - timedelta(days=weeks * 7 + 14)


def eligible(conn, campaign, run_date, settings=None):
    settings = settings or get_settings(conn)
    weeks = weeks_setting(settings)
    return conn.execute(
        """
        SELECT * FROM (
            SELECT ft.id, ft.first_name, ft.other_names, ft.phone, ft.visit_date,
                   (SELECT COUNT(*) FROM sms_messages m
                     WHERE m.first_timer_id = ft.id AND m.campaign = :c) AS attempts,
                   (SELECT COUNT(*) FROM sms_messages m
                     WHERE m.first_timer_id = ft.id AND m.campaign = :c AND m.run_date = :d) AS today
              FROM first_timers ft
             WHERE ft.sms_status = 'active' AND ft.sms_consent = 1
               AND ft.phone IS NOT NULL AND ft.phone <> ''
               AND ft.visit_date <= :d AND ft.visit_date >= :start
        )
        WHERE attempts < :w AND today = 0
        ORDER BY visit_date, id
        """,
        {"c": campaign, "d": run_date.isoformat(), "w": weeks,
         "start": window_start(run_date, weeks).isoformat()},
    ).fetchall()


def refresh_statuses(conn, settings=None, today=None):
    """Mark people complete once both reminder series are done or their window has passed."""
    settings = settings or get_settings(conn)
    weeks = weeks_setting(settings)
    today = today or now_eat().date()
    stamp = now_eat().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        UPDATE first_timers SET sms_status = 'completed', updated_at = :now
         WHERE sms_status = 'active' AND (
               visit_date < :start
            OR ((SELECT COUNT(*) FROM sms_messages m
                  WHERE m.first_timer_id = first_timers.id AND m.campaign = 'thursday') >= :w
            AND (SELECT COUNT(*) FROM sms_messages m
                  WHERE m.first_timer_id = first_timers.id AND m.campaign = 'sunday') >= :w))
        """,
        {"now": stamp, "start": window_start(today, weeks).isoformat(), "w": weeks},
    )
    conn.commit()


def reopen_if_needed(conn, ft_id, settings=None):
    """After a visit date is corrected, put a 'completed' person back into follow-up if they qualify."""
    settings = settings or get_settings(conn)
    weeks = weeks_setting(settings)
    today = now_eat().date()
    row = conn.execute(
        """SELECT visit_date,
                  (SELECT COUNT(*) FROM sms_messages WHERE first_timer_id = ? AND campaign = 'thursday') AS th,
                  (SELECT COUNT(*) FROM sms_messages WHERE first_timer_id = ? AND campaign = 'sunday') AS su,
                  sms_status
             FROM first_timers WHERE id = ?""",
        (ft_id, ft_id, ft_id),
    ).fetchone()
    if row and row["sms_status"] == "completed" and row["visit_date"] >= window_start(today, weeks).isoformat() \
            and (row["th"] < weeks or row["su"] < weeks):
        conn.execute("UPDATE first_timers SET sms_status = 'active' WHERE id = ?", (ft_id,))
        conn.commit()


def progress(conn, ft_id, settings=None):
    settings = settings or get_settings(conn)
    weeks = weeks_setting(settings)
    counts = {c: 0 for c in FOLLOWUP_CAMPAIGNS}
    for r in conn.execute(
        "SELECT campaign, COUNT(*) AS n FROM sms_messages WHERE first_timer_id = ? "
        "AND campaign IN ('thursday', 'sunday') GROUP BY campaign", (ft_id,)
    ):
        counts[r["campaign"]] = r["n"]
    return {"weeks": weeks, **counts}


def _stamp():
    return now_eat().strftime("%Y-%m-%d %H:%M:%S")


def _deliver(conn, provider, run_id, campaign, run_date, recipients, body_for):
    """Send to each recipient and record the outcome. Returns (attempted, sent, failed)."""
    attempted = sent = failed = 0
    for person in recipients:
        body = body_for(person)
        cur = conn.execute(
            "INSERT OR IGNORE INTO sms_messages "
            "(run_id, first_timer_id, campaign, run_date, phone, body, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'sending', ?)",
            (run_id, person["id"], campaign, run_date.isoformat(), person["phone"], body, _stamp()),
        )
        conn.commit()
        if cur.rowcount == 0:
            continue  # already sent today by another run
        attempted += 1
        try:
            result = provider.send(person["phone"], body)
        except Exception as exc:  # noqa: BLE001 - never let one message stop the batch
            log.exception("SMS send crashed")
            from .sms import SendResult
            result = SendResult("failed", error=str(exc)[:200])
        conn.execute(
            "UPDATE sms_messages SET status = ?, provider_id = ?, cost = ?, error = ?, updated_at = ? WHERE id = ?",
            (result.status, result.provider_id, result.cost, result.error, _stamp(), cur.lastrowid),
        )
        conn.commit()
        if result.status == "failed":
            failed += 1
        else:
            sent += 1
    return attempted, sent, failed


def _open_run(conn, provider, campaign, run_date, triggered_by, recipients, user_id=None, note=None):
    cur = conn.execute(
        "INSERT INTO sms_runs (campaign, run_date, triggered_by, provider, started_at, recipients, note, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (campaign, run_date.isoformat(), triggered_by, provider.name, _stamp(), recipients, note, user_id),
    )
    conn.commit()
    return cur.lastrowid


def _close_run(conn, run_id, attempted, sent, failed):
    if attempted == 0:
        conn.execute("DELETE FROM sms_runs WHERE id = ?", (run_id,))
        conn.commit()
        return None
    conn.execute(
        "UPDATE sms_runs SET finished_at = ?, recipients = ?, sent = ?, failed = ? WHERE id = ?",
        (_stamp(), attempted, sent, failed, run_id),
    )
    conn.commit()
    return conn.execute("SELECT * FROM sms_runs WHERE id = ?", (run_id,)).fetchone()


def run_campaign(conn, provider, campaign, run_date=None, triggered_by="auto", user_id=None):
    """Send today's Thursday or Sunday reminder to everyone who is due one.

    Safe to call repeatedly: people already messaged today are skipped. Returns the
    run row, or None if nobody was due.
    """
    if campaign not in FOLLOWUP_CAMPAIGNS:
        raise ValueError(campaign)
    run_date = run_date or now_eat().date()
    with _send_lock:
        settings = get_settings(conn)
        people = eligible(conn, campaign, run_date, settings)
        if not people:
            refresh_statuses(conn, settings, run_date)
            return None
        template = settings.get(f"template_{campaign}", "")
        run_id = _open_run(conn, provider, campaign, run_date, triggered_by, len(people), user_id)
        counts = _deliver(conn, provider, run_id, campaign, run_date, people,
                          lambda p: render_message(template, settings, p["first_name"]))
        run = _close_run(conn, run_id, *counts)
        refresh_statuses(conn, settings, run_date)
        log.info("%s run (%s): %s", campaign, triggered_by, counts)
        return run


def send_broadcast(conn, provider, people, template, user_id=None, note=None):
    run_date = now_eat().date()
    with _send_lock:
        settings = get_settings(conn)
        run_id = _open_run(conn, provider, "broadcast", run_date, "manual", len(people), user_id, note)
        counts = _deliver(conn, provider, run_id, "broadcast", run_date, people,
                          lambda p: render_message(template, settings, p["first_name"]))
        return _close_run(conn, run_id, *counts)


def send_test(conn, provider, phone, body, user_id=None):
    run_date = now_eat().date()
    with _send_lock:
        run_id = _open_run(conn, provider, "test", run_date, "manual", 1, user_id)
        counts = _deliver(conn, provider, run_id, "test", run_date,
                          [{"id": None, "phone": phone, "first_name": ""}], lambda p: body)
        _close_run(conn, run_id, *counts)
        return conn.execute(
            "SELECT * FROM sms_messages WHERE run_id = ? ORDER BY id DESC LIMIT 1", (run_id,)
        ).fetchone()


def retry_failed(conn, provider, run_id):
    with _send_lock:
        rows = conn.execute(
            "SELECT * FROM sms_messages WHERE run_id = ? AND status IN ('failed', 'sending')", (run_id,)
        ).fetchall()
        fixed = 0
        for m in rows:
            # Use the person's current number in case the office corrected it.
            phone = m["phone"]
            if m["first_timer_id"]:
                ft = conn.execute("SELECT phone FROM first_timers WHERE id = ?", (m["first_timer_id"],)).fetchone()
                if ft and ft["phone"]:
                    phone = ft["phone"]
            result = provider.send(phone, m["body"])
            conn.execute(
                "UPDATE sms_messages SET phone = ?, status = ?, provider_id = ?, cost = ?, error = ?, updated_at = ? "
                "WHERE id = ?",
                (phone, result.status, result.provider_id, result.cost, result.error, _stamp(), m["id"]),
            )
            conn.commit()
            fixed += result.status != "failed"
        totals = conn.execute(
            "SELECT COUNT(*) AS n, SUM(status = 'failed' OR status = 'sending') AS f FROM sms_messages WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        conn.execute("UPDATE sms_runs SET sent = ?, failed = ? WHERE id = ?",
                     (totals["n"] - (totals["f"] or 0), totals["f"] or 0, run_id))
        conn.commit()
        return len(rows), fixed


def next_send_at(settings, campaign, now=None):
    """When this reminder next goes out. Returns (datetime, is_today_window_open)."""
    now = now or now_eat()
    day = int(settings.get(f"{campaign}_reminder_day") or (2 if campaign == "thursday" else 5))
    start = parse_hhmm(settings.get(f"{campaign}_reminder_time"), "10:00")
    latest = parse_hhmm(settings.get("latest_send_time"), "20:30")
    for i in range(8):
        d = now.date() + timedelta(days=i)
        if d.weekday() != day:
            continue
        if i == 0 and now.time() > latest:
            continue
        at = datetime.combine(d, start, EAT)
        return at, (i == 0 and start <= now.time() <= latest)
    return None, False


def in_send_window(settings, campaign, now):
    day = int(settings.get(f"{campaign}_reminder_day") or (2 if campaign == "thursday" else 5))
    start = parse_hhmm(settings.get(f"{campaign}_reminder_time"), "10:00")
    latest = parse_hhmm(settings.get("latest_send_time"), "20:30")
    return now.weekday() == day and start <= now.time() <= latest


def broadcast_audience(conn, audience, born_again="", days=30, date_from=None, date_to=None, service=""):
    where = ["sms_consent = 1", "sms_status <> 'opted_out'", "phone IS NOT NULL", "phone <> ''"]
    params = []
    today = now_eat().date()
    if audience == "followup":
        where.append("sms_status = 'active'")
    elif audience == "recent":
        where.append("visit_date >= ?")
        params.append((today - timedelta(days=max(1, int(days or 30)))).isoformat())
    elif audience == "range":
        if date_from:
            where.append("visit_date >= ?")
            params.append(date_from)
        if date_to:
            where.append("visit_date <= ?")
            params.append(date_to)
    if born_again in ("yes", "no", "unsure"):
        where.append("born_again = ?")
        params.append(born_again)
    if service in ("sunday", "thursday", "other"):
        where.append("service = ?")
        params.append(service)
    return conn.execute(
        f"SELECT id, first_name, other_names, phone FROM first_timers WHERE {' AND '.join(where)} "
        "ORDER BY visit_date DESC", params,
    ).fetchall()
