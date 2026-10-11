"""Sign-in for office staff."""
from functools import wraps

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .utils import now_eat

bp = Blueprint("auth", __name__)

MIN_PASSWORD = 8


def load_user():
    g.user = None
    uid = session.get("user_id")
    if uid:
        row = get_db().execute("SELECT * FROM users WHERE id = ? AND active = 1", (uid,)).fetchone()
        g.user = row
        if row is None:
            session.pop("user_id", None)


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None or g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _safe_next(target):
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("dashboard.index")


@bp.route("/login", methods=["GET", "POST"])
def login():
    db = get_db()
    if not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return redirect(url_for("auth.setup"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.execute("SELECT * FROM users WHERE username = ? AND active = 1", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            csrf = session.get("csrf_token")
            session.clear()
            session["csrf_token"] = csrf
            session["user_id"] = user["id"]
            session.permanent = True
            db.execute("UPDATE users SET last_login = ? WHERE id = ?",
                       (now_eat().strftime("%Y-%m-%d %H:%M:%S"), user["id"]))
            db.commit()
            return redirect(_safe_next(request.args.get("next")))
        error = "That username and password don't match. Check them and try again."
    return render_template("auth/login.html", error=error)


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    db = get_db()
    if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return redirect(url_for("auth.login"))
    error = None
    form = request.form
    if request.method == "POST":
        full_name = form.get("full_name", "").strip()
        username = form.get("username", "").strip()
        password = form.get("password", "")
        if not full_name or not username:
            error = "Enter your name and choose a username."
        elif len(password) < MIN_PASSWORD:
            error = f"Choose a password with at least {MIN_PASSWORD} characters."
        elif password != form.get("password2", ""):
            error = "The two passwords don't match."
        else:
            cur = db.execute(
                "INSERT INTO users (username, full_name, password_hash, role) VALUES (?, ?, ?, 'admin')",
                (username, full_name, generate_password_hash(password)),
            )
            db.commit()
            session["user_id"] = cur.lastrowid
            flash("Your admin account is ready. Next, check the service times and messages in Settings.", "ok")
            return redirect(url_for("settings.index"))
    return render_template("auth/setup.html", error=error, form=form)


@bp.route("/logout", methods=["POST"])
def logout():
    session.pop("user_id", None)
    return redirect(url_for("auth.login"))


@bp.route("/account/password", methods=["GET", "POST"])
def change_password():
    error = None
    if request.method == "POST":
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        if not check_password_hash(g.user["password_hash"], current):
            error = "Your current password isn't right."
        elif len(new) < MIN_PASSWORD:
            error = f"Choose a new password with at least {MIN_PASSWORD} characters."
        elif new != request.form.get("new2", ""):
            error = "The two new passwords don't match."
        else:
            db = get_db()
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new), g.user["id"]))
            db.commit()
            flash("Password changed.", "ok")
            return redirect(url_for("dashboard.index"))
    return render_template("auth/password.html", error=error)
