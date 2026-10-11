"""SMS: reminder schedule, history, manual sends and custom messages."""
from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template,
                   request, url_for)

from ..db import get_db, get_settings
from ..followup import (CAMPAIGNS, FOLLOWUP_CAMPAIGNS, broadcast_audience, eligible, next_send_at,
                        render_message, retry_failed, run_campaign, send_broadcast)
from ..sms import get_provider
from ..utils import WEEKDAYS, now_eat, sms_parts

bp = Blueprint("messages", __name__, url_prefix="/messages")


def schedule_overview(db, settings):
    now = now_eat()
    out = []
    for c in FOLLOWUP_CAMPAIGNS:
        at, window_open = next_send_at(settings, c, now)
        today_run = db.execute(
            "SELECT COALESCE(SUM(recipients), 0) AS n, MIN(started_at) AS first FROM sms_runs "
            "WHERE campaign = ? AND run_date = ?", (c, now.date().isoformat()),
        ).fetchone()
        last = db.execute(
            "SELECT run_date, SUM(recipients) AS recipients, SUM(sent) AS sent, SUM(failed) AS failed "
            "FROM sms_runs WHERE campaign = ? GROUP BY run_date ORDER BY run_date DESC LIMIT 1", (c,),
        ).fetchone()
        due = len(eligible(db, c, at.date(), settings)) if at else 0
        day = int(settings.get(f"{c}_reminder_day") or 0)
        out.append({
            "campaign": c, "label": CAMPAIGNS[c], "at": at, "window_open": window_open,
            "today_is_day": now.weekday() == day, "day_name": WEEKDAYS[day],
            "due": due, "sent_today": today_run["n"], "last": last,
            "template": settings.get(f"template_{c}", ""),
            "preview": render_message(settings.get(f"template_{c}", ""), settings, "Grace"),
        })
    return out


@bp.route("/")
def index():
    db = get_db()
    settings = get_settings()
    provider = get_provider(current_app.config)
    runs = db.execute(
        "SELECT r.*, u.full_name AS by_name FROM sms_runs r LEFT JOIN users u ON u.id = r.created_by "
        "ORDER BY r.started_at DESC, r.id DESC LIMIT 40"
    ).fetchall()
    month = now_eat().strftime("%Y-%m")
    totals = db.execute(
        "SELECT SUM(status IN ('sent', 'simulated')) AS ok, SUM(status IN ('failed', 'sending')) AS bad "
        "FROM sms_messages WHERE substr(created_at, 1, 7) = ?", (month,),
    ).fetchone()
    return render_template(
        "messages/index.html", schedule=schedule_overview(db, settings), runs=runs, provider=provider,
        balance=provider.balance() if request.args.get("balance") else None, totals=totals,
        campaigns=CAMPAIGNS,
    )


@bp.route("/send/<campaign>", methods=["POST"])
def send_now(campaign):
    if campaign not in FOLLOWUP_CAMPAIGNS:
        abort(404)
    db = get_db()
    settings = get_settings()
    day = int(settings.get(f"{campaign}_reminder_day") or 0)
    if now_eat().weekday() != day:
        # The wording says "tomorrow", so it can only go out on its own day.
        flash(f"This reminder says \"tomorrow\", so it can only be sent on a {WEEKDAYS[day]}. "
              "Use \"Write a message to a group\" for anything else.", "error")
        return redirect(url_for(".index"))
    run = run_campaign(db, get_provider(current_app.config), campaign, triggered_by="manual", user_id=g.user["id"])
    if run is None:
        flash(f"Nobody is due a {CAMPAIGNS[campaign].lower()} today. Everyone in follow-up has had today's message.", "info")
        return redirect(url_for(".index"))
    verb = "Simulated" if run["provider"] == "test" else "Sent"
    flash(f"{verb} {run['sent']} messages. {run['failed']} failed." if run["failed"] else
          f"{verb} {run['sent']} messages.", "ok" if not run["failed"] else "error")
    return redirect(url_for(".run_detail", run_id=run["id"]))


@bp.route("/runs/<int:run_id>")
def run_detail(run_id):
    db = get_db()
    run = db.execute(
        "SELECT r.*, u.full_name AS by_name FROM sms_runs r LEFT JOIN users u ON u.id = r.created_by WHERE r.id = ?",
        (run_id,),
    ).fetchone()
    if run is None:
        abort(404)
    msgs = db.execute(
        "SELECT m.*, ft.first_name, ft.other_names FROM sms_messages m "
        "LEFT JOIN first_timers ft ON ft.id = m.first_timer_id WHERE m.run_id = ? "
        "ORDER BY m.status = 'failed' DESC, m.id", (run_id,),
    ).fetchall()
    return render_template("messages/run.html", run=run, msgs=msgs, campaigns=CAMPAIGNS)


@bp.route("/runs/<int:run_id>/retry", methods=["POST"])
def retry(run_id):
    db = get_db()
    tried, fixed = retry_failed(db, get_provider(current_app.config), run_id)
    if not tried:
        flash("There were no failed messages to resend.", "info")
    else:
        flash(f"Resent {tried} messages. {fixed} went through.", "ok" if fixed == tried else "error")
    return redirect(url_for(".run_detail", run_id=run_id))


def _audience_from(args):
    return broadcast_audience(
        get_db(), args.get("audience", "followup"), args.get("born_again", ""),
        args.get("days", 30, type=int) or 30,
        args.get("date_from") or None, args.get("date_to") or None, args.get("service", ""),
    )


@bp.route("/audience")
def audience_count():
    people = _audience_from(request.args)
    return jsonify({"count": len(people), "sample": [p["first_name"] for p in people[:6]]})


@bp.route("/compose", methods=["GET", "POST"])
def compose():
    settings = get_settings()
    form = request.form if request.method == "POST" else request.args
    if request.method == "POST":
        body = (request.form.get("message") or "").strip()
        people = _audience_from(request.form)
        if not body:
            flash("Write the message first.", "error")
        elif not people:
            flash("Nobody matches that group. Change the filters and try again.", "error")
        elif request.form.get("confirm") != str(len(people)):
            flash("The number of recipients changed. Check the group and send again.", "error")
        else:
            run = send_broadcast(get_db(), get_provider(current_app.config), people, body,
                                 user_id=g.user["id"], note=request.form.get("note") or None)
            if run:
                flash(f"Message sent to {run['sent']} people." + (f" {run['failed']} failed." if run["failed"] else ""),
                      "ok" if not run["failed"] else "error")
                return redirect(url_for(".run_detail", run_id=run["id"]))
    return render_template("messages/compose.html", form=form,
                           preview=render_message(form.get("message", ""), settings, "Grace"),
                           parts=sms_parts(form.get("message", "")))
