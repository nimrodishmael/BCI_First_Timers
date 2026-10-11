"""Dashboard: follow-up status and trends."""
from collections import Counter
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, request

from ..db import get_db, get_settings
from ..utils import EAT, now_eat, pct, week_start
from .messages import schedule_overview

bp = Blueprint("dashboard", __name__)

RANGES = {"12w": "Last 12 weeks", "6m": "Last 6 months", "12m": "Last 12 months", "all": "All time"}


def _month_start(d, back=0):
    y, m = d.year, d.month - back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def _buckets(rng, today, earliest):
    """Return (start_date, bucket_keys, labels, mode)."""
    if rng == "12w":
        start = week_start(today) - timedelta(weeks=11)
        keys = [start + timedelta(weeks=i) for i in range(12)]
        return start, [k.isoformat() for k in keys], [k.strftime("%d %b").lstrip("0") for k in keys], "week"
    months = {"6m": 6, "12m": 12}.get(rng)
    if months is None:
        first = earliest or today
        months = max(1, (today.year - first.year) * 12 + today.month - first.month + 1)
    start = _month_start(today, months - 1)
    keys = [_month_start(today, months - 1 - i) for i in range(months)]
    return start, [k.strftime("%Y-%m") for k in keys], [k.strftime("%b %y") for k in keys], "month"


@bp.route("/")
def index():
    db = get_db()
    settings = get_settings()
    today = now_eat().date()
    rng = request.args.get("range", "12w")
    if rng not in RANGES:
        rng = "12w"
    earliest_row = db.execute("SELECT MIN(visit_date) FROM first_timers").fetchone()[0]
    earliest = date.fromisoformat(earliest_row) if earliest_row else None
    start, keys, labels, mode = _buckets(rng, today, earliest)
    s = start.isoformat()

    rows = db.execute(
        """SELECT ft.id, ft.visit_date, ft.service, ft.born_again, ft.residence, ft.prayer_categories,
                  ft.sms_status, ft.is_member,
                  EXISTS (SELECT 1 FROM return_visits rv WHERE rv.first_timer_id = ft.id) AS came_back,
                  EXISTS (SELECT 1 FROM sms_messages m WHERE m.first_timer_id = ft.id
                          AND m.campaign IN ('thursday', 'sunday') AND m.status IN ('sent', 'simulated')) AS reached
             FROM first_timers ft WHERE ft.visit_date >= ?""", (s,),
    ).fetchall()

    # Arrivals per bucket, split by service
    series = {k: {"sunday": 0, "thursday": 0, "other": 0} for k in keys}
    for r in rows:
        d = date.fromisoformat(r["visit_date"])
        k = week_start(d).isoformat() if mode == "week" else r["visit_date"][:7]
        if k in series:
            series[k][r["service"] if r["service"] in ("sunday", "thursday") else "other"] += 1
    chart = {
        "labels": labels,
        "mode": mode,
        "sunday": [series[k]["sunday"] for k in keys],
        "thursday": [series[k]["thursday"] for k in keys],
        "other": [series[k]["other"] for k in keys],
    }

    total = len(rows)
    came_back = sum(r["came_back"] for r in rows)
    answered = [r for r in rows if r["born_again"]]
    born = Counter(r["born_again"] or "blank" for r in rows)

    funnel = [
        ("Registered", total),
        ("Received at least one reminder", sum(r["reached"] for r in rows)),
        ("Finished the follow-up weeks", sum(r["sms_status"] == "completed" for r in rows)),
        ("Came back to church", came_back),
        ("Became members", sum(r["is_member"] for r in rows)),
    ]

    places = Counter()
    display = {}
    for r in rows:
        if r["residence"]:
            key = " ".join(r["residence"].lower().split())
            places[key] += 1
            display.setdefault(key, r["residence"].strip().title() if r["residence"].islower() else r["residence"].strip())
    top_places = [(display[k], n) for k, n in places.most_common(8)]
    other_places = sum(places.values()) - sum(n for _, n in top_places)
    no_place = total - sum(places.values())

    prayer = Counter()
    for r in rows:
        for c in (r["prayer_categories"] or "").split(","):
            if c.strip():
                prayer[c.strip()] += 1

    # Headline numbers
    this_month = today.strftime("%Y-%m")
    last_month_start = _month_start(today, 1)
    month_counts = dict(db.execute(
        "SELECT substr(visit_date, 1, 7), COUNT(*) FROM first_timers WHERE visit_date >= ? GROUP BY 1",
        (last_month_start.isoformat(),),
    ).fetchall())
    in_followup = db.execute("SELECT COUNT(*) FROM first_timers WHERE sms_status = 'active'").fetchone()[0]
    sms_month = db.execute(
        "SELECT SUM(status IN ('sent', 'simulated')) AS ok, SUM(status IN ('failed', 'sending')) AS bad "
        "FROM sms_messages WHERE substr(created_at, 1, 7) = ? AND campaign <> 'test'", (this_month,),
    ).fetchone()

    kpis = {
        "this_month": month_counts.get(this_month, 0),
        "last_month": month_counts.get(last_month_start.strftime("%Y-%m"), 0),
        "last_month_name": last_month_start.strftime("%B"),
        "in_followup": in_followup,
        "came_back_pct": pct(came_back, total),
        "came_back": came_back,
        "born_again_pct": pct(born.get("yes", 0), len(answered)),
        "answered": len(answered),
        "sms_ok": sms_month["ok"] or 0,
        "sms_bad": sms_month["bad"] or 0,
    }

    attention = {
        "no_phone": db.execute(
            "SELECT COUNT(*) FROM first_timers WHERE sms_status = 'active' AND (phone IS NULL OR phone = '')"
        ).fetchone()[0],
        "failed": db.execute(
            "SELECT COUNT(DISTINCT first_timer_id) FROM sms_messages WHERE status IN ('failed', 'sending') "
            "AND first_timer_id IS NOT NULL AND run_date >= ?", ((today - timedelta(days=14)).isoformat(),),
        ).fetchone()[0],
        "paused": db.execute("SELECT COUNT(*) FROM first_timers WHERE sms_status = 'paused'").fetchone()[0],
    }

    recent = db.execute(
        "SELECT id, first_name, other_names, residence, visit_date, service, born_again, sms_status "
        "FROM first_timers ORDER BY created_at DESC, id DESC LIMIT 6"
    ).fetchall()

    tick = settings.get("scheduler_last_tick")
    scheduler_ok = False
    if tick:
        try:
            last = datetime.fromisoformat(tick).replace(tzinfo=EAT)
            scheduler_ok = (now_eat() - last) < timedelta(minutes=5)
        except ValueError:
            pass

    return render_template(
        "dashboard.html", rng=rng, ranges=RANGES, chart=chart, total=total, kpis=kpis,
        funnel=funnel, top_places=top_places, other_places=other_places, no_place=no_place,
        prayer=prayer.most_common(10), born=born, schedule=schedule_overview(db, settings),
        attention=attention, recent=recent, scheduler_ok=scheduler_ok, scheduler_tick=tick,
        range_start=start,
    )
