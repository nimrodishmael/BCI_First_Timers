"""SQLite storage: schema, connections and settings."""
import sqlite3
from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name     TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'staff' CHECK (role IN ('admin', 'staff')),
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', '+3 hours')),
    last_login    TEXT
);

CREATE TABLE IF NOT EXISTS first_timers (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name        TEXT NOT NULL,
    other_names       TEXT,
    phone             TEXT,
    residence         TEXT,
    prayer_points     TEXT,
    prayer_categories TEXT,
    born_again        TEXT CHECK (born_again IN ('yes', 'no', 'unsure') OR born_again IS NULL),
    visit_date        TEXT NOT NULL,
    service           TEXT NOT NULL DEFAULT 'sunday',
    sms_consent       INTEGER NOT NULL DEFAULT 1,
    sms_status        TEXT NOT NULL DEFAULT 'active'
                      CHECK (sms_status IN ('active', 'paused', 'opted_out', 'completed')),
    is_member         INTEGER NOT NULL DEFAULT 0,
    notes             TEXT,
    created_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at        TEXT NOT NULL DEFAULT (datetime('now', '+3 hours')),
    updated_at        TEXT
);
CREATE INDEX IF NOT EXISTS ix_ft_visit  ON first_timers(visit_date);
CREATE INDEX IF NOT EXISTS ix_ft_status ON first_timers(sms_status);
CREATE INDEX IF NOT EXISTS ix_ft_phone  ON first_timers(phone);

CREATE TABLE IF NOT EXISTS return_visits (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    first_timer_id INTEGER NOT NULL REFERENCES first_timers(id) ON DELETE CASCADE,
    visit_date     TEXT NOT NULL,
    service        TEXT,
    recorded_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now', '+3 hours')),
    UNIQUE (first_timer_id, visit_date)
);

CREATE TABLE IF NOT EXISTS sms_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign     TEXT NOT NULL,
    run_date     TEXT NOT NULL,
    triggered_by TEXT NOT NULL,
    provider     TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    recipients   INTEGER NOT NULL DEFAULT 0,
    sent         INTEGER NOT NULL DEFAULT 0,
    failed       INTEGER NOT NULL DEFAULT 0,
    note         TEXT,
    created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_runs_date ON sms_runs(run_date);

CREATE TABLE IF NOT EXISTS sms_messages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER REFERENCES sms_runs(id) ON DELETE SET NULL,
    first_timer_id INTEGER REFERENCES first_timers(id) ON DELETE CASCADE,
    campaign       TEXT NOT NULL,
    run_date       TEXT NOT NULL,
    phone          TEXT NOT NULL,
    body           TEXT NOT NULL,
    status         TEXT NOT NULL,
    provider_id    TEXT,
    cost           TEXT,
    error          TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now', '+3 hours')),
    updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS ix_msg_ft  ON sms_messages(first_timer_id, campaign);
CREATE INDEX IF NOT EXISTS ix_msg_run ON sms_messages(run_id);
-- A first timer gets at most one Thursday and one Sunday reminder per day,
-- even if a manual send and the scheduler overlap.
CREATE UNIQUE INDEX IF NOT EXISTS ux_followup_once_per_day
    ON sms_messages(first_timer_id, campaign, run_date)
    WHERE campaign IN ('thursday', 'sunday');

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

DEFAULT_SETTINGS = {
    "church_name": "Breakthrough Chapel International",
    "church_short": "BCI",
    "thursday_service_name": "Dominion Service",
    "thursday_service_time": "5:30 PM",
    "sunday_service_time": "9:00 AM",
    "church_location": "",
    "followup_weeks": "4",
    "auto_send": "1",
    "thursday_reminder_day": "2",   # Wednesday (Monday = 0)
    "thursday_reminder_time": "10:00",
    "sunday_reminder_day": "5",     # Saturday
    "sunday_reminder_time": "17:00",
    "latest_send_time": "20:30",
    # Both fit in one SMS (160 characters) even with a long first name.
    "template_thursday": (
        "Hi {first_name}, you are warmly invited to the {thursday_service} tomorrow, Thursday, "
        "at {thursday_time} at {church}. See you there!"
    ),
    "template_sunday": (
        "Hi {first_name}, join us tomorrow, Sunday, at {sunday_time} for service at {church}. "
        "Come expecting your breakthrough! God bless."
    ),
    "prayer_categories": (
        "Salvation, Healing, Finances, Job or business, Family, Marriage, Children, "
        "Studies, Deliverance, Direction, Other"
    ),
    "scheduler_last_tick": "",
}


def connect(path):
    conn = sqlite3.connect(path, timeout=15, detect_types=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(path):
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()


def get_settings(conn=None):
    conn = conn or get_db()
    values = dict(DEFAULT_SETTINGS)
    values.update({r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")})
    return values


def set_settings(values: dict, conn=None):
    conn = conn or get_db()
    for key, value in values.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, "" if value is None else str(value)),
        )
    conn.commit()


def prayer_category_list(settings):
    return [c.strip() for c in (settings.get("prayer_categories") or "").split(",") if c.strip()]
