"""Fill a SEPARATE demo database with made-up first timers so you can explore the app.

Used by start_demo.bat. It never touches the real database: it writes to the
folder named in DATA_DIR (data_demo by default).

    set DATA_DIR=data_demo
    python demo_data.py
"""
import os
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("DATA_DIR", "data_demo")
if os.environ["DATA_DIR"].rstrip("/\\") in ("data", ""):
    sys.exit("Refusing to write demo data into the real data folder. Set DATA_DIR=data_demo.")

from werkzeug.security import generate_password_hash  # noqa: E402

from bci_app import create_app  # noqa: E402
from bci_app.db import connect  # noqa: E402
from bci_app.followup import refresh_statuses, render_message  # noqa: E402
from bci_app.db import get_settings  # noqa: E402
from bci_app.utils import now_eat  # noqa: E402

FIRST = ["Grace", "Brian", "Faith", "Kevin", "Mercy", "Dennis", "Esther", "Collins", "Purity", "Victor",
         "Mary", "Joseph", "Halima", "Peter", "Neema", "Samuel", "Rehema", "Daniel", "Joy", "Elijah",
         "Winnie", "Moses", "Lilian", "Stephen", "Zawadi", "Hassan", "Caroline", "Ian", "Mwanaisha", "Felix",
         "Sharon", "Kelvin", "Diana", "Allan", "Mwende", "Tom", "Rose", "George", "Baraka", "Ann"]
LAST = ["Otieno", "Mwangi", "Wanjiru", "Kamau", "Achieng", "Mutua", "Njeri", "Omondi", "Kahindi", "Chengo",
        "Karisa", "Mwakio", "Baya", "Kazungu", "Wekesa", "Nyambura", "Onyango", "Mbogo", "Kiprono", "Charo",
        "Juma", "Katana", "Mwasi", "Nduta", "Kalama"]
PLACES = ["Bamburi"] * 6 + ["Kisauni"] * 5 + ["Nyali"] * 4 + ["Likoni"] * 4 + ["Mtwapa"] * 3 + \
         ["Changamwe"] * 3 + ["Mikindani", "Bombolulu", "Magongo", "Tudor", "Shanzu", "Kongowea",
                              "Mishomoroni", "Utange", "Mombasa CBD", "Miritini", "Junda"]
PRAYERS = [
    ("I need a job. I finished college last year.", "Job or business"),
    ("Healing for my mother who is sick in hospital.", "Healing"),
    ("Pray for my family to be united again.", "Family"),
    ("School fees for my children this term.", "Finances, Children"),
    ("I want to give my life to Christ.", "Salvation"),
    ("My business has been slow. Pray for customers.", "Job or business, Finances"),
    ("Pray for a godly spouse.", "Marriage"),
    ("I am sitting my KCSE exams.", "Studies"),
    ("Freedom from bad dreams and fear.", "Deliverance"),
    ("Direction on whether to move to Nairobi for work.", "Direction"),
    ("Peace in my marriage.", "Marriage, Family"),
    ("Healing from back pain.", "Healing"),
    ("Rent arrears. I need a breakthrough.", "Finances"),
    ("", ""),
]


def main():
    app = create_app()
    db_path = Path(app.config["DATABASE"])
    conn = connect(db_path)
    if conn.execute("SELECT COUNT(*) FROM first_timers").fetchone()[0]:
        print("Demo data is already there.")
        return

    random.seed(7)
    conn.execute("INSERT OR IGNORE INTO users (username, full_name, password_hash, role) VALUES (?, ?, ?, 'admin')",
                 ("demo", "Demo Admin", generate_password_hash("demo1234")))
    uid = conn.execute("SELECT id FROM users WHERE username = 'demo'").fetchone()[0]
    settings = get_settings(conn)
    today = now_eat().date()
    start = today - timedelta(days=182)

    people = []
    d = start
    while d <= today:
        if d.weekday() in (6, 3):
            n = random.randint(2, 7) if d.weekday() == 6 else random.randint(0, 3)
            if d.month == 12 and d.day > 15:
                n += 4  # December crusade season
            for _ in range(n):
                people.append((d, "sunday" if d.weekday() == 6 else "thursday"))
        elif d.weekday() == 5 and random.random() < 0.05:
            for _ in range(random.randint(3, 6)):
                people.append((d, "other"))
        d += timedelta(days=1)

    for visit, service in people:
        fn, ln = random.choice(FIRST), random.choice(LAST) + (" " + random.choice(LAST) if random.random() < 0.3 else "")
        phone = None if random.random() < 0.06 else "+2547" + "".join(random.choice("0123456789") for _ in range(8))
        prayer, cats = random.choice(PRAYERS)
        born = random.choices(["yes", "no", "unsure", None], [52, 26, 12, 10])[0]
        cur = conn.execute(
            """INSERT INTO first_timers (first_name, other_names, phone, residence, visit_date, service, born_again,
                   prayer_points, prayer_categories, sms_consent, sms_status, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'active', ?, ?)""",
            (fn, ln, phone, random.choice(PLACES) if random.random() > 0.08 else None, visit.isoformat(), service,
             born, prayer or None, cats or None, uid,
             datetime.combine(visit + timedelta(days=1), datetime.min.time()).strftime("%Y-%m-%d 10:%M:00")),
        )
        ft = cur.lastrowid
        # They came back?
        if random.random() < 0.38:
            back = sorted({visit + timedelta(days=7 * k + (0 if service == "sunday" else 3)) for k in
                           random.sample(range(1, 9), random.randint(1, 4))})
            for b in back:
                if b <= today:
                    conn.execute("INSERT OR IGNORE INTO return_visits (first_timer_id, visit_date, service, recorded_by) "
                                 "VALUES (?, ?, 'sunday', ?)", (ft, b.isoformat(), uid))
            if len(back) >= 3 and random.random() < 0.4:
                conn.execute("UPDATE first_timers SET is_member = 1 WHERE id = ?", (ft,))

    # Message history, as the scheduler would have produced it
    runs = {}
    rows = conn.execute("SELECT id, first_name, phone, visit_date FROM first_timers WHERE phone IS NOT NULL").fetchall()
    for r in rows:
        v = date.fromisoformat(r["visit_date"])
        for campaign, weekday, hour in (("thursday", 2, "10"), ("sunday", 5, "17")):
            sent = 0
            day = v + timedelta(days=1)
            while sent < 4 and day < today:
                if day.weekday() == weekday:
                    key = (campaign, day)
                    if key not in runs:
                        runs[key] = conn.execute(
                            "INSERT INTO sms_runs (campaign, run_date, triggered_by, provider, started_at, finished_at) "
                            "VALUES (?, ?, 'auto', 'test', ?, ?)",
                            (campaign, day.isoformat(), f"{day} {hour}:00:05", f"{day} {hour}:00:40"),
                        ).lastrowid
                    status = "failed" if random.random() < 0.025 else "simulated"
                    conn.execute(
                        "INSERT INTO sms_messages (run_id, first_timer_id, campaign, run_date, phone, body, status, error, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (runs[key], r["id"], campaign, day.isoformat(), r["phone"],
                         render_message(settings[f"template_{campaign}"], settings, r["first_name"]), status,
                         "Invalid phone number" if status == "failed" else None, f"{day} {hour}:00:10"),
                    )
                    sent += 1
                day += timedelta(days=1)
    for run_id in runs.values():
        t = conn.execute("SELECT COUNT(*) n, SUM(status = 'failed') f FROM sms_messages WHERE run_id = ?", (run_id,)).fetchone()
        conn.execute("UPDATE sms_runs SET recipients = ?, sent = ?, failed = ? WHERE id = ?",
                     (t["n"], t["n"] - (t["f"] or 0), t["f"] or 0, run_id))
    conn.commit()
    refresh_statuses(conn, settings, today)
    print(f"Added {len(people)} made-up first timers to {db_path}.")
    print("Sign in with username: demo  password: demo1234")


if __name__ == "__main__":
    main()
