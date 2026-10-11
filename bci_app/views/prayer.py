"""Prayer list for the intercessory team."""
from collections import Counter, OrderedDict
from datetime import timedelta

from flask import Blueprint, render_template, request

from ..db import get_db, get_settings, prayer_category_list
from ..utils import parse_date, today_eat

bp = Blueprint("prayer", __name__, url_prefix="/prayer")


@bp.route("/")
def index():
    db = get_db()
    today = today_eat()
    date_from = parse_date(request.args.get("from")) or (today - timedelta(days=13))
    date_to = parse_date(request.args.get("to")) or today
    category = request.args.get("category", "")
    params = [date_from.isoformat(), date_to.isoformat()]
    where = "visit_date BETWEEN ? AND ? AND (prayer_points IS NOT NULL OR prayer_categories IS NOT NULL)"
    if category:
        where += " AND (',' || replace(prayer_categories, ', ', ',') || ',') LIKE ?"
        params.append(f"%,{category},%")
    rows = db.execute(
        f"SELECT id, first_name, other_names, residence, visit_date, service, born_again, prayer_points, "
        f"prayer_categories FROM first_timers WHERE {where} ORDER BY visit_date DESC, first_name", params,
    ).fetchall()
    grouped = OrderedDict()
    tally = Counter()
    for r in rows:
        grouped.setdefault(r["visit_date"], []).append(r)
        for c in (r["prayer_categories"] or "").split(","):
            if c.strip():
                tally[c.strip()] += 1
    return render_template(
        "prayer/index.html", grouped=grouped, total=len(rows), tally=tally.most_common(),
        categories=prayer_category_list(get_settings()), category=category,
        date_from=date_from, date_to=date_to,
    )
