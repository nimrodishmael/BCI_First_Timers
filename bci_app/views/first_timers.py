"""First timer records: entry, search, details, return visits, import and export."""
import csv
import io
from datetime import timedelta

from flask import (Blueprint, Response, abort, flash, g, jsonify, redirect, render_template,
                   request, url_for)

from ..auth import admin_required
from ..db import get_db, get_settings, prayer_category_list
from ..followup import progress, reopen_if_needed, weeks_setting, window_start
from ..utils import (BORN_AGAIN, SERVICES, SMS_STATUS, clean_text, display_phone,
                     normalize_phone, now_eat, parse_date, tidy_name, today_eat)

bp = Blueprint("first_timers", __name__, url_prefix="/first-timers")

PAGE_SIZE = 50


def last_service_date(today=None):
    """Most recent Sunday or Thursday, the usual day cards are collected."""
    today = today or today_eat()
    for i in range(7):
        d = today - timedelta(days=i)
        if d.weekday() in (3, 6):
            return d
    return today


def service_for(d):
    return {6: "sunday", 3: "thursday"}.get(d.weekday(), "other")


def _form_values(form, categories):
    picked = [c for c in form.getlist("prayer_categories") if c in categories or c]
    return {
        "first_name": tidy_name(form.get("first_name"), 80),
        "other_names": tidy_name(form.get("other_names"), 120),
        "phone_raw": clean_text(form.get("phone"), 40),
        "residence": tidy_name(form.get("residence"), 120),
        "visit_date": form.get("visit_date", ""),
        "service": form.get("service") if form.get("service") in SERVICES else "sunday",
        "born_again": form.get("born_again") if form.get("born_again") in BORN_AGAIN else None,
        "prayer_points": clean_text(form.get("prayer_points"), 3000),
        "prayer_categories": ", ".join(dict.fromkeys(picked)) or None,
        "sms_consent": 1 if form.get("sms_consent") else 0,
        "notes": clean_text(form.get("notes"), 2000),
    }


def _validate(v):
    errors = {}
    if not v["first_name"]:
        errors["first_name"] = "Enter the first name from the card."
    d = parse_date(v["visit_date"])
    if not d:
        errors["visit_date"] = "Enter the date they visited."
    elif d > today_eat():
        errors["visit_date"] = "The visit date can't be in the future."
    else:
        v["visit_date"] = d.isoformat()
    phone = normalize_phone(v["phone_raw"])
    if v["phone_raw"] and not phone:
        errors["phone"] = "This doesn't look like a phone number. Use a format like 0712 345 678."
    v["phone"] = phone
    return errors


def _duplicates(db, phone, exclude_id=None):
    if not phone:
        return []
    return db.execute(
        "SELECT id, first_name, other_names, visit_date FROM first_timers WHERE phone = ? AND id IS NOT ?",
        (phone, exclude_id),
    ).fetchall()


def _get(ft_id):
    row = get_db().execute("SELECT * FROM first_timers WHERE id = ?", (ft_id,)).fetchone()
    if row is None:
        abort(404)
    return row


@bp.route("/")
def index():
    db = get_db()
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    born = request.args.get("born_again", "")
    service = request.args.get("service", "")
    month = request.args.get("month", "")
    page = max(1, request.args.get("page", 1, type=int))

    where, params = _filters(q, status, born, service, month)
    total = db.execute(f"SELECT COUNT(*) FROM first_timers ft WHERE {where}", params).fetchone()[0]
    rows = db.execute(
        f"""SELECT ft.*,
                   (SELECT COUNT(*) FROM return_visits rv WHERE rv.first_timer_id = ft.id) AS returns,
                   (SELECT COUNT(*) FROM sms_messages m WHERE m.first_timer_id = ft.id
                      AND m.campaign IN ('thursday', 'sunday')) AS reminders
              FROM first_timers ft WHERE {where}
             ORDER BY ft.visit_date DESC, ft.id DESC LIMIT ? OFFSET ?""",
        params + [PAGE_SIZE, (page - 1) * PAGE_SIZE],
    ).fetchall()
    months = [r[0] for r in db.execute(
        "SELECT DISTINCT substr(visit_date, 1, 7) FROM first_timers ORDER BY 1 DESC LIMIT 36")]
    weeks = weeks_setting(get_settings())
    return render_template(
        "first_timers/list.html", rows=rows, total=total, page=page,
        pages=max(1, -(-total // PAGE_SIZE)), months=months, weeks=weeks,
        filters={"q": q, "status": status, "born_again": born, "service": service, "month": month},
    )


def _filters(q, status, born, service, month):
    where, params = ["1 = 1"], []
    if q:
        like = f"%{q}%"
        digits = "".join(ch for ch in q if ch.isdigit())
        clause = "(ft.first_name LIKE ? OR ft.other_names LIKE ? OR (ft.first_name || ' ' || ifnull(ft.other_names, '')) LIKE ? OR ft.residence LIKE ?"
        params += [like, like, like, like]
        if len(digits) >= 3:
            clause += " OR ft.phone LIKE ?"
            params.append(f"%{digits.lstrip('0')}%")
        where.append(clause + ")")
    if status == "returned":
        where.append("EXISTS (SELECT 1 FROM return_visits rv WHERE rv.first_timer_id = ft.id)")
    elif status == "member":
        where.append("ft.is_member = 1")
    elif status == "no_phone":
        where.append("(ft.phone IS NULL OR ft.phone = '')")
    elif status in SMS_STATUS:
        where.append("ft.sms_status = ?")
        params.append(status)
    if born in BORN_AGAIN:
        where.append("ft.born_again = ?")
        params.append(born)
    elif born == "blank":
        where.append("ft.born_again IS NULL")
    if service in SERVICES:
        where.append("ft.service = ?")
        params.append(service)
    if month:
        where.append("substr(ft.visit_date, 1, 7) = ?")
        params.append(month)
    return " AND ".join(where), params


@bp.route("/new", methods=["GET", "POST"])
def new():
    db = get_db()
    settings = get_settings()
    categories = prayer_category_list(settings)
    errors, dupes = {}, []
    if request.method == "POST":
        v = _form_values(request.form, categories)
        errors = _validate(v)
        if not errors and not request.form.get("confirm_duplicate"):
            dupes = _duplicates(db, v["phone"])
        if not errors and not dupes:
            status = "active"
            weeks = weeks_setting(settings)
            if v["visit_date"] < window_start(today_eat(), weeks).isoformat():
                status = "completed"
            cur = db.execute(
                """INSERT INTO first_timers (first_name, other_names, phone, residence, visit_date, service,
                       born_again, prayer_points, prayer_categories, sms_consent, sms_status, notes, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (v["first_name"], v["other_names"], v["phone"], v["residence"], v["visit_date"], v["service"],
                 v["born_again"], v["prayer_points"], v["prayer_categories"], v["sms_consent"], status,
                 v["notes"], g.user["id"]),
            )
            db.commit()
            name = f"{v['first_name']} {v['other_names'] or ''}".strip()
            note = "" if v["phone"] else " No phone number, so they won't get SMS reminders."
            if request.form.get("action") == "another":
                flash(f"Saved {name}.{note} Ready for the next card.", "ok")
                return redirect(url_for(".new", visit_date=v["visit_date"], service=v["service"]))
            flash(f"Saved {name}.{note}", "ok")
            return redirect(url_for(".detail", ft_id=cur.lastrowid))
        values = dict(v, phone=v.get("phone_raw"))
        values["categories_list"] = request.form.getlist("prayer_categories")
    else:
        d = parse_date(request.args.get("visit_date")) or last_service_date()
        values = {"visit_date": d.isoformat(), "service": request.args.get("service") or service_for(d),
                  "sms_consent": 1, "categories_list": []}
    return render_template("first_timers/form.html", values=values, errors=errors, dupes=dupes,
                           categories=categories, editing=None)


@bp.route("/places.json")
def places():
    rows = get_db().execute(
        "SELECT MIN(trim(residence)), COUNT(*) AS n FROM first_timers "
        "WHERE residence IS NOT NULL AND trim(residence) <> '' "
        "GROUP BY lower(trim(residence)) ORDER BY n DESC LIMIT 300"
    ).fetchall()
    return jsonify([r[0] for r in rows])


@bp.route("/<int:ft_id>")
def detail(ft_id):
    db = get_db()
    ft = _get(ft_id)
    settings = get_settings()
    messages = db.execute(
        "SELECT * FROM sms_messages WHERE first_timer_id = ? ORDER BY created_at DESC, id DESC", (ft_id,)
    ).fetchall()
    visits = db.execute(
        "SELECT rv.*, u.full_name AS recorded_by_name FROM return_visits rv "
        "LEFT JOIN users u ON u.id = rv.recorded_by WHERE first_timer_id = ? ORDER BY visit_date DESC", (ft_id,)
    ).fetchall()
    creator = db.execute("SELECT full_name FROM users WHERE id = ?", (ft["created_by"],)).fetchone()
    return render_template(
        "first_timers/detail.html", ft=ft, messages=messages, visits=visits,
        prog=progress(db, ft_id, settings), creator=creator,
        default_return=last_service_date().isoformat(),
    )


@bp.route("/<int:ft_id>/edit", methods=["GET", "POST"])
def edit(ft_id):
    db = get_db()
    ft = _get(ft_id)
    settings = get_settings()
    categories = prayer_category_list(settings)
    errors, dupes = {}, []
    if request.method == "POST":
        v = _form_values(request.form, categories)
        errors = _validate(v)
        if not errors and not request.form.get("confirm_duplicate") and v["phone"] != ft["phone"]:
            dupes = _duplicates(db, v["phone"], exclude_id=ft_id)
        if not errors and not dupes:
            db.execute(
                """UPDATE first_timers SET first_name = ?, other_names = ?, phone = ?, residence = ?,
                       visit_date = ?, service = ?, born_again = ?, prayer_points = ?, prayer_categories = ?,
                       sms_consent = ?, notes = ?, updated_at = ? WHERE id = ?""",
                (v["first_name"], v["other_names"], v["phone"], v["residence"], v["visit_date"], v["service"],
                 v["born_again"], v["prayer_points"], v["prayer_categories"], v["sms_consent"], v["notes"],
                 now_eat().strftime("%Y-%m-%d %H:%M:%S"), ft_id),
            )
            db.commit()
            reopen_if_needed(db, ft_id, settings)
            flash("Changes saved.", "ok")
            return redirect(url_for(".detail", ft_id=ft_id))
        values = dict(v, phone=v.get("phone_raw"))
        values["categories_list"] = request.form.getlist("prayer_categories")
    else:
        values = dict(ft)
        values["phone"] = display_phone(ft["phone"])
        values["categories_list"] = [c.strip() for c in (ft["prayer_categories"] or "").split(",") if c.strip()]
    return render_template("first_timers/form.html", values=values, errors=errors, dupes=dupes,
                           categories=categories, editing=ft)


@bp.route("/<int:ft_id>/delete", methods=["POST"])
@admin_required
def delete(ft_id):
    ft = _get(ft_id)
    db = get_db()
    db.execute("DELETE FROM first_timers WHERE id = ?", (ft_id,))
    db.commit()
    flash(f"Deleted {ft['first_name']} {ft['other_names'] or ''} and their message history.".replace("  ", " "), "ok")
    return redirect(url_for(".index"))


@bp.route("/<int:ft_id>/sms", methods=["POST"])
def set_sms_status(ft_id):
    ft = _get(ft_id)
    action = request.form.get("action")
    db = get_db()
    new_status = {"pause": "paused", "resume": "active", "opt_out": "opted_out"}.get(action)
    if not new_status:
        abort(400)
    db.execute("UPDATE first_timers SET sms_status = ?, updated_at = ? WHERE id = ?",
               (new_status, now_eat().strftime("%Y-%m-%d %H:%M:%S"), ft_id))
    if action == "opt_out":
        db.execute("UPDATE first_timers SET sms_consent = 0 WHERE id = ?", (ft_id,))
    if action == "resume":
        db.execute("UPDATE first_timers SET sms_consent = 1 WHERE id = ?", (ft_id,))
    db.commit()
    if action == "resume":
        from ..followup import refresh_statuses
        refresh_statuses(db)
    messages = {"paused": "Reminders paused.", "active": "Reminders resumed.",
                "opted_out": f"{ft['first_name']} won't receive any more SMS."}
    flash(messages[new_status], "ok")
    return redirect(url_for(".detail", ft_id=ft_id))


@bp.route("/<int:ft_id>/member", methods=["POST"])
def toggle_member(ft_id):
    ft = _get(ft_id)
    db = get_db()
    db.execute("UPDATE first_timers SET is_member = ? WHERE id = ?", (0 if ft["is_member"] else 1, ft_id))
    db.commit()
    flash("Marked as a member." if not ft["is_member"] else "No longer marked as a member.", "ok")
    return redirect(url_for(".detail", ft_id=ft_id))


@bp.route("/<int:ft_id>/visits", methods=["POST"])
def add_visit(ft_id):
    ft = _get(ft_id)
    d = parse_date(request.form.get("visit_date"))
    service = request.form.get("service") if request.form.get("service") in SERVICES else None
    if not d or d > today_eat():
        flash("Enter the date they came back. It can't be in the future.", "error")
    elif d.isoformat() <= ft["visit_date"]:
        flash("A return visit has to be after their first visit.", "error")
    else:
        db = get_db()
        db.execute(
            "INSERT OR IGNORE INTO return_visits (first_timer_id, visit_date, service, recorded_by) VALUES (?, ?, ?, ?)",
            (ft_id, d.isoformat(), service or service_for(d), g.user["id"]),
        )
        db.commit()
        flash(f"Recorded that {ft['first_name']} came back.", "ok")
    return redirect(url_for(".detail", ft_id=ft_id))


@bp.route("/<int:ft_id>/visits/<int:visit_id>/delete", methods=["POST"])
def delete_visit(ft_id, visit_id):
    db = get_db()
    db.execute("DELETE FROM return_visits WHERE id = ? AND first_timer_id = ?", (visit_id, ft_id))
    db.commit()
    flash("Return visit removed.", "ok")
    return redirect(url_for(".detail", ft_id=ft_id))


# ---------------------------------------------------------------- import / export

EXPORT_COLUMNS = [
    ("id", "ID"), ("first_name", "First name"), ("other_names", "Other names"), ("phone", "Phone"),
    ("residence", "Where they stay"), ("visit_date", "Visit date"), ("service", "Service"),
    ("born_again", "Born again"), ("prayer_categories", "Prayer categories"),
    ("prayer_points", "Prayer points"), ("sms_status", "Follow-up status"), ("sms_consent", "SMS consent"),
    ("returns", "Return visits"), ("is_member", "Member"), ("notes", "Notes"), ("created_at", "Entered on"),
]


@bp.route("/export.csv")
def export_csv():
    db = get_db()
    where, params = _filters(request.args.get("q", "").strip(), request.args.get("status", ""),
                             request.args.get("born_again", ""), request.args.get("service", ""),
                             request.args.get("month", ""))
    rows = db.execute(
        f"""SELECT ft.*, (SELECT COUNT(*) FROM return_visits rv WHERE rv.first_timer_id = ft.id) AS returns
              FROM first_timers ft WHERE {where} ORDER BY ft.visit_date DESC, ft.id DESC""", params,
    ).fetchall()
    buf = io.StringIO()
    buf.write("﻿")  # lets Excel open the file with the right characters
    w = csv.writer(buf)
    w.writerow([label for _, label in EXPORT_COLUMNS])
    for r in rows:
        out = []
        for key, _ in EXPORT_COLUMNS:
            val = r[key]
            if key == "phone":
                val = display_phone(val)
            elif key == "service":
                val = SERVICES.get(val, val)
            elif key == "born_again":
                val = BORN_AGAIN.get(val, "")
            elif key == "sms_status":
                val = SMS_STATUS.get(val, val)
            elif key in ("sms_consent", "is_member"):
                val = "Yes" if val else "No"
            out.append("" if val is None else val)
        w.writerow(out)
    stamp = today_eat().isoformat()
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=bci_first_timers_{stamp}.csv"})


IMPORT_FIELDS = ["first_name", "other_names", "phone", "residence", "visit_date", "service",
                 "born_again", "prayer_points", "prayer_categories", "sms_consent", "notes"]
HEADER_ALIASES = {
    "first name": "first_name", "firstname": "first_name", "name": "full_name", "full name": "full_name",
    "names": "full_name", "other names": "other_names", "surname": "other_names", "last name": "other_names",
    "phone number": "phone", "mobile": "phone", "telephone": "phone", "tel": "phone", "phone no": "phone",
    "where they stay": "residence", "where do you stay": "residence", "residence": "residence",
    "location": "residence", "estate": "residence", "area": "residence",
    "date": "visit_date", "visit date": "visit_date", "date of visit": "visit_date",
    "born again": "born_again", "are you born again": "born_again", "saved": "born_again",
    "prayer points": "prayer_points", "prayer request": "prayer_points", "prayer requests": "prayer_points",
    "prayer categories": "prayer_categories", "sms consent": "sms_consent", "notes": "notes",
}
YES = {"yes", "y", "true", "1", "ndio", "born again", "saved"}
NO = {"no", "n", "false", "0", "hapana", "not born again"}


def _norm_header(h):
    key = (h or "").strip().lower().replace("_", " ").rstrip("?:").strip()
    return HEADER_ALIASES.get(key, key.replace(" ", "_"))


def _read_csv(file_storage):
    raw = file_storage.read()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))


@bp.route("/import", methods=["GET", "POST"])
def import_csv():
    result = None
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Choose a CSV file to import.", "error")
            return redirect(url_for(".import_csv"))
        try:
            rows = _read_csv(f)
        except Exception:  # noqa: BLE001
            flash("That file couldn't be read. Save it from Excel as 'CSV UTF-8' and try again.", "error")
            return redirect(url_for(".import_csv"))
        result = _import_rows(rows)
        if result["imported"]:
            flash(f"Imported {result['imported']} first timers.", "ok")
    return render_template("first_timers/import.html", result=result)


def _import_rows(rows):
    db = get_db()
    settings = get_settings()
    weeks = weeks_setting(settings)
    start = window_start(today_eat(), weeks).isoformat()
    imported, problems = 0, []
    for n, raw in enumerate(rows, start=2):
        r = {_norm_header(k): (v or "").strip() for k, v in raw.items() if k}
        if not any(r.values()):
            continue
        if not r.get("first_name") and r.get("full_name"):
            parts = r["full_name"].split()
            r["first_name"], r["other_names"] = parts[0], " ".join(parts[1:])
        if not r.get("first_name"):
            problems.append((n, "No first name"))
            continue
        d = parse_date(r.get("visit_date"))
        if not d:
            problems.append((n, f"Visit date '{r.get('visit_date', '')}' isn't a date (use YYYY-MM-DD or DD/MM/YYYY)"))
            continue
        phone = normalize_phone(r.get("phone"))
        if r.get("phone") and not phone:
            problems.append((n, f"Phone '{r['phone']}' isn't a valid number; saved without it"))
        if phone and db.execute("SELECT 1 FROM first_timers WHERE phone = ? AND visit_date = ?",
                                (phone, d.isoformat())).fetchone():
            problems.append((n, f"{r['first_name']} is already in the system for {d.isoformat()}; skipped"))
            continue
        ba = r.get("born_again", "").lower()
        born = "yes" if ba in YES else "no" if ba in NO else ("unsure" if ba else None)
        service = r.get("service", "").lower()
        service = "thursday" if "thu" in service or "dominion" in service else \
            "sunday" if "sun" in service else ("other" if service else service_for(d))
        consent = 0 if r.get("sms_consent", "").lower() in NO else 1
        db.execute(
            """INSERT INTO first_timers (first_name, other_names, phone, residence, visit_date, service,
                   born_again, prayer_points, prayer_categories, sms_consent, sms_status, notes, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tidy_name(r["first_name"], 80), tidy_name(r.get("other_names")), phone, tidy_name(r.get("residence")),
             d.isoformat(), service, born, r.get("prayer_points") or None, r.get("prayer_categories") or None,
             consent, "active" if d.isoformat() >= start else "completed", r.get("notes") or None, g.user["id"]),
        )
        imported += 1
    db.commit()
    return {"imported": imported, "problems": problems, "rows": len(rows)}


@bp.route("/import/template.csv")
def import_template():
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf)
    w.writerow(IMPORT_FIELDS)
    w.writerow(["Grace", "Achieng Otieno", "0712345678", "Bamburi", last_service_date().isoformat(), "sunday",
                "yes", "Job opportunity and family", "Job or business, Family", "yes", ""])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=first_timers_template.csv"})
