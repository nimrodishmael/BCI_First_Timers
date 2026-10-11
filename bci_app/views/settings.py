"""Settings: services, reminder schedule, message wording, users and backups."""
import sqlite3
import tempfile
from pathlib import Path

from flask import (Blueprint, current_app, flash, g, redirect, render_template, request, send_file,
                   url_for)
from werkzeug.security import generate_password_hash

from ..auth import MIN_PASSWORD, admin_required
from ..db import get_db, get_settings, set_settings
from ..followup import render_message, send_test
from ..sms import get_provider
from ..utils import normalize_phone, parse_hhmm, sms_parts, today_eat

bp = Blueprint("settings", __name__, url_prefix="/settings")

TEXT_FIELDS = ["church_name", "church_short", "thursday_service_name", "thursday_service_time",
               "sunday_service_time", "church_location", "template_thursday", "template_sunday",
               "prayer_categories"]
TIME_FIELDS = ["thursday_reminder_time", "sunday_reminder_time", "latest_send_time"]
DAY_FIELDS = ["thursday_reminder_day", "sunday_reminder_day"]


@bp.route("/", methods=["GET", "POST"])
@admin_required
def index():
    settings = get_settings()
    if request.method == "POST":
        f = request.form
        values = {}
        for key in TEXT_FIELDS:
            if key in f:
                values[key] = " ".join(f.get(key, "").split()) if key.startswith("template") else f.get(key, "").strip()
        for key in TIME_FIELDS:
            if key in f:
                t = parse_hhmm(f.get(key), settings.get(key) or "10:00")
                values[key] = t.strftime("%H:%M")
        for key in DAY_FIELDS:
            if f.get(key, "").isdigit() and 0 <= int(f[key]) <= 6:
                values[key] = f[key]
        if "followup_weeks" in f and f["followup_weeks"].isdigit():
            values["followup_weeks"] = str(max(1, min(12, int(f["followup_weeks"]))))
        if "schedule_form" in f:
            values["auto_send"] = "1" if f.get("auto_send") else "0"
        if not values.get("church_name", settings["church_name"]):
            flash("The church name can't be empty.", "error")
        else:
            set_settings(values)
            flash("Settings saved.", "ok")
        return redirect(url_for(".index") + (f"#{f.get('section')}" if f.get("section") else ""))

    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY active DESC, full_name").fetchall()
    provider = get_provider(current_app.config)
    previews = {c: render_message(settings[f"template_{c}"], settings, "Grace") for c in ("thursday", "sunday")}
    return render_template("settings/index.html", users=users, provider=provider, previews=previews,
                           parts={c: sms_parts(p) for c, p in previews.items()},
                           config=current_app.config)


@bp.route("/test-sms", methods=["POST"])
@admin_required
def test_sms():
    phone = normalize_phone(request.form.get("phone"))
    if not phone:
        flash("Enter a valid phone number for the test message.", "error")
        return redirect(url_for(".index") + "#sms")
    settings = get_settings()
    which = request.form.get("template", "thursday")
    body = render_message(settings.get(f"template_{which}", ""), settings, g.user["full_name"])
    msg = send_test(get_db(), get_provider(current_app.config), phone, body, g.user["id"])
    if msg["status"] == "sent":
        flash(f"Test message sent to {request.form.get('phone')}. It should arrive within a minute.", "ok")
    elif msg["status"] == "simulated":
        flash("Test mode is on, so the message was recorded but not sent. "
              "Set SMS_PROVIDER=africastalking in the .env file to send for real.", "info")
    else:
        flash(f"The test message failed: {msg['error']}", "error")
    return redirect(url_for(".index") + "#sms")


@bp.route("/users", methods=["POST"])
@admin_required
def add_user():
    f = request.form
    full_name, username, password = f.get("full_name", "").strip(), f.get("username", "").strip(), f.get("password", "")
    role = "admin" if f.get("role") == "admin" else "staff"
    db = get_db()
    if not full_name or not username:
        flash("Enter the person's name and a username.", "error")
    elif len(password) < MIN_PASSWORD:
        flash(f"Give them a password of at least {MIN_PASSWORD} characters.", "error")
    elif db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        flash(f"The username '{username}' is taken. Choose another.", "error")
    else:
        db.execute("INSERT INTO users (username, full_name, password_hash, role) VALUES (?, ?, ?, ?)",
                   (username, full_name, generate_password_hash(password), role))
        db.commit()
        flash(f"Added {full_name}. They can sign in as '{username}'.", "ok")
    return redirect(url_for(".index") + "#users")


@bp.route("/users/<int:user_id>", methods=["POST"])
@admin_required
def update_user(user_id):
    db = get_db()
    action = request.form.get("action")
    if user_id == g.user["id"] and action in ("deactivate", "make_staff"):
        flash("You can't remove your own admin access. Ask another admin to do it.", "error")
        return redirect(url_for(".index") + "#users")
    if action == "reset":
        pw = request.form.get("password", "")
        if len(pw) < MIN_PASSWORD:
            flash(f"New password needs at least {MIN_PASSWORD} characters.", "error")
        else:
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(pw), user_id))
            flash("Password reset. Share the new password with them.", "ok")
    elif action in ("deactivate", "activate"):
        db.execute("UPDATE users SET active = ? WHERE id = ?", (1 if action == "activate" else 0, user_id))
        flash("Account updated.", "ok")
    elif action in ("make_admin", "make_staff"):
        db.execute("UPDATE users SET role = ? WHERE id = ?", ("admin" if action == "make_admin" else "staff", user_id))
        flash("Role updated.", "ok")
    db.commit()
    return redirect(url_for(".index") + "#users")


@bp.route("/backup")
@admin_required
def backup():
    """Download a consistent copy of the database, safe to take while the app runs."""
    src = sqlite3.connect(current_app.config["DATABASE"])
    tmp = Path(tempfile.mkdtemp()) / f"bci_first_timers_backup_{today_eat().isoformat()}.db"
    dst = sqlite3.connect(tmp)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    return send_file(tmp, as_attachment=True, download_name=tmp.name)


@bp.route("/clear-simulated", methods=["POST"])
@admin_required
def clear_simulated():
    db = get_db()
    n = db.execute("SELECT COUNT(*) FROM sms_messages WHERE status = 'simulated'").fetchone()[0]
    db.execute("DELETE FROM sms_messages WHERE status = 'simulated'")
    db.execute("DELETE FROM sms_runs WHERE id NOT IN (SELECT DISTINCT run_id FROM sms_messages WHERE run_id IS NOT NULL)")
    db.commit()
    for row in db.execute("SELECT id FROM first_timers WHERE sms_status = 'completed'").fetchall():
        from ..followup import reopen_if_needed
        reopen_if_needed(db, row["id"])
    flash(f"Removed {n} test-mode messages. Follow-up counts start fresh for real sending.", "ok")
    return redirect(url_for(".index") + "#maintenance")
