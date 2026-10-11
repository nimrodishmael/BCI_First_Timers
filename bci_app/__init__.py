"""BCI First Timers: follow-up, SMS reminders and trends for Breakthrough Chapel International."""
import logging
import os
import secrets
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, g, redirect, request, session, url_for

from . import db
from .utils import BORN_AGAIN, SERVICES, SMS_STATUS, WEEKDAYS, display_phone, now_eat, sms_parts

BASE_DIR = Path(__file__).resolve().parent.parent


def _secret_key(data_dir: Path) -> str:
    key = os.environ.get("SECRET_KEY", "").strip()
    if key and key != "change-me":
        return key
    key_file = data_dir / ".secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    key_file.write_text(key)
    return key


def create_app(start_scheduler=False, test_config=None):
    load_dotenv(BASE_DIR / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    if not data_dir.is_absolute():
        data_dir = BASE_DIR / data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=_secret_key(data_dir),
        DATABASE=str(data_dir / "bci_first_timers.db"),
        DATA_DIR=str(data_dir),
        SMS_PROVIDER=os.environ.get("SMS_PROVIDER", "test"),
        AT_USERNAME=os.environ.get("AT_USERNAME", ""),
        AT_API_KEY=os.environ.get("AT_API_KEY", ""),
        AT_SENDER_ID=os.environ.get("AT_SENDER_ID", ""),
        AT_ENVIRONMENT=os.environ.get("AT_ENVIRONMENT", "live"),
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    )
    if test_config:
        app.config.update(test_config)

    db.init_db(app.config["DATABASE"])
    app.teardown_appcontext(db.close_db)

    from .auth import bp as auth_bp, load_user
    from .views.dashboard import bp as dashboard_bp
    from .views.first_timers import bp as ft_bp
    from .views.messages import bp as messages_bp
    from .views.prayer import bp as prayer_bp
    from .views.settings import bp as settings_bp

    for bp in (auth_bp, dashboard_bp, ft_bp, messages_bp, prayer_bp, settings_bp):
        app.register_blueprint(bp)

    @app.before_request
    def guard():
        load_user()
        if request.endpoint in (None, "static"):
            return None
        if request.method == "POST":
            token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not token or token != session.get("csrf_token"):
                abort(400, "This form has expired. Go back, refresh the page and try again.")
        public = {"auth.login", "auth.setup"}
        if request.endpoint in public:
            return None
        if g.user is None:
            has_users = db.get_db().execute("SELECT 1 FROM users LIMIT 1").fetchone()
            return redirect(url_for("auth.login" if has_users else "auth.setup", next=request.full_path))
        return None

    @app.context_processor
    def inject():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(24)
        settings = db.get_settings()
        return {
            "csrf_token": session["csrf_token"],
            "settings": settings,
            "SERVICES": SERVICES,
            "BORN_AGAIN": BORN_AGAIN,
            "SMS_STATUS": SMS_STATUS,
            "WEEKDAYS": WEEKDAYS,
            "sms_mode": app.config["SMS_PROVIDER"],
            "now": now_eat(),
        }

    # Fixed lookup tables, available everywhere including imported macros.
    app.jinja_env.globals.update(SERVICES=SERVICES, BORN_AGAIN=BORN_AGAIN, SMS_STATUS=SMS_STATUS, WEEKDAYS=WEEKDAYS)

    @app.template_filter("phone")
    def _phone(value):
        return display_phone(value)

    @app.template_filter("nicedate")
    def _nicedate(value, fmt="%a %d %b %Y"):
        if not value:
            return ""
        try:
            d = datetime.fromisoformat(str(value)[:19]) if len(str(value)) > 10 else datetime.fromisoformat(str(value))
            out = d.strftime(fmt).replace(" 0", " ")
            return out[1:] if out[:1] == "0" else out
        except ValueError:
            return value

    @app.template_filter("nicetime")
    def _nicetime(value):
        if not value:
            return ""
        try:
            return datetime.fromisoformat(str(value)[:19]).strftime("%a %d %b, %H:%M").replace(" 0", " ", 1)
        except ValueError:
            return value

    @app.template_filter("smsparts")
    def _smsparts(value):
        return sms_parts(value)

    @app.errorhandler(400)
    def bad_request(e):
        from flask import render_template
        return render_template("error.html", code=400, message=e.description), 400

    @app.errorhandler(403)
    def forbidden(e):
        from flask import render_template
        return render_template("error.html", code=403, message="Only admins can open this page."), 403

    @app.errorhandler(404)
    def not_found(e):
        from flask import render_template
        return render_template("error.html", code=404, message="That page or record doesn't exist."), 404

    if start_scheduler:
        from . import scheduler
        scheduler.start(app)

    return app
