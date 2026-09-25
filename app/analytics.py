"""Numbers for dashboards and reports.

Everything is CALCULATED from valid tea transactions — nothing here is stored
or hand-entered — and shared by the manager's Insights page and the receiver's
Trends page, so the two can never disagree about how a figure is worked out.

Aggregates are per buying centre and per day. Only top_farmers() names farmers,
and only the management pages call it (the receiver never sees whose tea it was).
"""
from datetime import datetime, time, timedelta

from app import db
from app.models import BuyingCentre, Employee, Farmer, TeaTransaction
from app.timeutil import today_utc


def _bounds(start, end):
    """The datetimes [start 00:00, end+1 day 00:00) — so an 'end date' includes the whole day."""
    return datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min)


def _valid_between(query, start, end, centre_id=None):
    lo, hi = _bounds(start, end)
    query = query.filter(
        TeaTransaction.status == "VALID",
        TeaTransaction.transaction_time >= lo,
        TeaTransaction.transaction_time < hi,
    )
    if centre_id:
        query = query.filter(TeaTransaction.buying_centre_id == centre_id)
    return query


def active_centres():
    return BuyingCentre.query.filter_by(status="ACTIVE").order_by(BuyingCentre.name).all()


def total_kilos(start, end, centre_id=None):
    """Kilos bought between two dates (inclusive), across all centres or one."""
    query = _valid_between(
        db.session.query(db.func.coalesce(db.func.sum(TeaTransaction.weight_kg), 0.0)), start, end, centre_id)
    return float(query.scalar() or 0)


def kilos_by_centre(start, end):
    """{centre_id: kilos} for the period, in a single query."""
    rows = (
        _valid_between(
            db.session.query(TeaTransaction.buying_centre_id, db.func.sum(TeaTransaction.weight_kg)), start, end)
        .group_by(TeaTransaction.buying_centre_id)
        .all()
    )
    return {row[0]: float(row[1] or 0) for row in rows}


def daily_series_between(start, end, centre_id=None):
    """(labels, values): kilos per day between two dates (inclusive), oldest first.
    Every day gets a value, even 0, so charts have no gaps. Backs both the preset
    "last N days" view and a user-chosen From/To range — same query either way."""
    if end < start:
        start, end = end, start
    days = (end - start).days + 1
    rows = (
        _valid_between(
            db.session.query(db.func.date(TeaTransaction.transaction_time).label("d"),
                             db.func.sum(TeaTransaction.weight_kg)),
            start, end, centre_id)
        .group_by("d")
        .all()
    )
    # SQLite returns the day as text, Postgres as a date object — normalise both.
    totals = {}
    for day_value, kilos in rows:
        key = day_value.isoformat() if hasattr(day_value, "isoformat") else str(day_value)[:10]
        totals[key] = kilos

    labels, values = [], []
    for i in range(days):
        d = start + timedelta(days=i)
        labels.append(d.strftime("%d %b"))
        values.append(round(totals.get(d.isoformat(), 0) or 0, 1))
    return labels, values


def daily_series(days=30, centre_id=None):
    """(labels, values): kilos per day for the last `days` days including today."""
    today = today_utc()
    start = today - timedelta(days=days - 1)
    return daily_series_between(start, today, centre_id=centre_id)


def centre_leaderboard(day):
    """[{centre, kilos}, ...] for one day, highest first (active centres)."""
    by_centre = kilos_by_centre(day, day)
    rows = [{"centre": c, "kilos": by_centre.get(c.id, 0.0)} for c in active_centres()]
    rows.sort(key=lambda r: r["kilos"], reverse=True)
    return rows


def _direction(recent_avg, prev_avg):
    if prev_avg == 0:
        return ("stagnant", 0.0) if recent_avg == 0 else ("improving", 100.0)
    pct_change = ((recent_avg - prev_avg) / prev_avg) * 100
    if pct_change > 8:
        return "improving", pct_change
    if pct_change < -8:
        return "dropping", pct_change
    return "stagnant", pct_change


def centre_trends(window=7):
    """{centre_id: (direction, pct_change)}: average daily kilos over the last
    `window` days against the `window` days before. Within +/-8% counts as
    'stagnant', so one unusually big or small day doesn't raise a false alarm."""
    today = today_utc()
    recent_start = today - timedelta(days=window - 1)
    prev_end = recent_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=window - 1)
    recent = kilos_by_centre(recent_start, today)
    previous = kilos_by_centre(prev_start, prev_end)
    return {
        cid: _direction(recent.get(cid, 0.0) / window, previous.get(cid, 0.0) / window)
        for cid in set(recent) | set(previous)
    }


def compare_periods(mode, a_value, b_value, centre_id=None):
    """Compare two days (mode='days', 'YYYY-MM-DD') or two months (mode='months', 'YYYY-MM').
    Raises ValueError on bad input; callers catch it."""

    def parse_period(value):
        if mode == "months":
            year, month = (int(part) for part in value.split("-"))
            start = datetime(year, month, 1).date()
            end = (datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)).date() - timedelta(days=1)
            label = start.strftime("%B %Y")
        else:
            start = end = datetime.strptime(value, "%Y-%m-%d").date()
            label = start.strftime("%d %b %Y")
        return start, end, label

    a_start, a_end, a_label = parse_period(a_value)
    b_start, b_end, b_label = parse_period(b_value)
    a_total = round(total_kilos(a_start, a_end, centre_id), 1)
    b_total = round(total_kilos(b_start, b_end, centre_id), 1)
    delta = round(b_total - a_total, 1)

    # The percentage is worked out ONCE, here, so the page never has to re-derive it
    # (that re-derivation is what used to show a flat, misleading "100%" any time the
    # left-hand/starting period had zero kilos — division by zero standing in for a
    # real percentage). With no baseline to measure against, there is no percentage:
    # pct is null and the page says so in words instead of a fake number.
    pct = round((delta / a_total) * 100, 1) if a_total > 0 else None

    return {
        "a": {"label": a_label, "total": a_total},
        "b": {"label": b_label, "total": b_total},
        "delta": delta,
        "pct": pct,
    }


def top_farmers(limit=15):
    """[(farmer, kilos), ...] — the farmers who have delivered the most valid tea."""
    rows = (
        db.session.query(Farmer, db.func.sum(TeaTransaction.weight_kg).label("kg"))
        .join(TeaTransaction, TeaTransaction.farmer_id == Farmer.id)
        .filter(TeaTransaction.status == "VALID")
        .group_by(Farmer.id)
        .order_by(db.desc("kg"))
        .limit(limit)
        .all()
    )
    return [(row[0], float(row[1] or 0)) for row in rows]


def today_by_centre():
    """{centre_id: {kilos, count, last}} for today, in a single query."""
    today = today_utc()
    rows = (
        _valid_between(
            db.session.query(
                TeaTransaction.buying_centre_id,
                db.func.sum(TeaTransaction.weight_kg),
                db.func.count(TeaTransaction.id),
                db.func.max(TeaTransaction.transaction_time)),
            today, today)
        .group_by(TeaTransaction.buying_centre_id)
        .all()
    )
    return {row[0]: {"kilos": float(row[1] or 0), "count": row[2], "last": row[3]} for row in rows}


def report_clerks():
    """Employees who have at least one valid tea purchase recorded against them —
    for the Insights & Trends report builder's clerk filter."""
    return (
        db.session.query(Employee)
        .join(TeaTransaction, TeaTransaction.clerk_employee_id == Employee.id)
        .filter(TeaTransaction.status == "VALID")
        .distinct()
        .order_by(Employee.first_name, Employee.last_name)
        .all()
    )


def report_data(start, end, centre_id=None, clerk_id=None):
    """Everything the Insights & Trends report needs for one From/To window,
    optionally narrowed to one buying centre and/or one clerk: the matching
    transactions, the running total, and a breakdown per centre and per clerk."""
    query = _valid_between(TeaTransaction.query, start, end, centre_id)
    if clerk_id:
        query = query.filter(TeaTransaction.clerk_employee_id == clerk_id)
    rows = query.order_by(TeaTransaction.transaction_time.asc()).all()

    by_centre, by_clerk = {}, {}
    for t in rows:
        c = by_centre.setdefault(t.buying_centre_id, {"centre": t.buying_centre, "kg": 0.0, "count": 0})
        c["kg"] += t.weight_kg
        c["count"] += 1
        k = by_clerk.setdefault(t.clerk_employee_id, {"clerk": t.clerk, "kg": 0.0, "count": 0})
        k["kg"] += t.weight_kg
        k["count"] += 1

    return {
        "transactions": rows,
        "total_kg": float(sum(t.weight_kg for t in rows)),
        "count": len(rows),
        "centre_rows": sorted(by_centre.values(), key=lambda r: r["kg"], reverse=True),
        "clerk_rows": sorted(by_clerk.values(), key=lambda r: r["kg"], reverse=True),
    }


def centre_analysis():
    """One row per buying centre: farmers, and kilos over several periods (all calculated)."""
    today = today_utc()
    month_start = today.replace(day=1)
    prev_end = month_start - timedelta(days=1)
    windows = {
        "month": (month_start, today),
        "prev_month": (prev_end.replace(day=1), prev_end),
        "three_month": (today - timedelta(days=89), today),    # last 90 days
        "six_month": (today - timedelta(days=179), today),     # last 180 days
    }
    kilos = {name: kilos_by_centre(*span) for name, span in windows.items()}

    registered = {
        row[0]: row[1]
        for row in db.session.query(Farmer.buying_centre_id, db.func.count(Farmer.id))
        .filter(Farmer.status == "ACTIVE").group_by(Farmer.buying_centre_id).all()
    }
    delivering = {
        row[0]: row[1]
        for row in _valid_between(
            db.session.query(TeaTransaction.buying_centre_id, db.func.count(db.distinct(TeaTransaction.farmer_id))),
            month_start, today).group_by(TeaTransaction.buying_centre_id).all()
    }

    rows = []
    for centre in BuyingCentre.query.order_by(BuyingCentre.name).all():
        month_kg = kilos["month"].get(centre.id, 0.0)
        active_farmers = delivering.get(centre.id, 0)
        rows.append({
            "centre": centre,
            "registered_farmers": registered.get(centre.id, 0),
            "active_farmers": active_farmers,
            "month_kg": month_kg,
            "prev_month_kg": kilos["prev_month"].get(centre.id, 0.0),
            "three_month_kg": kilos["three_month"].get(centre.id, 0.0),
            "six_month_kg": kilos["six_month"].get(centre.id, 0.0),
            "avg_kg_per_farmer": (month_kg / active_farmers) if active_farmers else 0.0,
        })
    return rows
