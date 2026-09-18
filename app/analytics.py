"""Shared analytics queries used by both the manager's Insights page and the
receiver's Trends page. Kept in one place so the two views can never drift
out of sync on how a number is calculated.

Nothing here ever touches farmer identity — everything is aggregated at the
buying-center / date level, which is exactly the boundary the receiver role
is supposed to see (kilos and quality per center, never whose tea it was).
"""

from datetime import date, datetime, timedelta

from app import db
from app.factory_models import BuyingCenter, Purchase


def total_kilos(factory_id, start, end, center_id=None):
    """Sum of kilos bought between start and end dates (inclusive)."""
    query = (
        db.session.query(db.func.coalesce(db.func.sum(Purchase.kilos), 0.0))
        .join(BuyingCenter)
        .filter(
            BuyingCenter.factory_id == factory_id,
            db.func.date(Purchase.purchased_at) >= start,
            db.func.date(Purchase.purchased_at) <= end,
        )
    )
    if center_id:
        query = query.filter(Purchase.buying_center_id == center_id)
    return float(query.scalar() or 0)


def daily_series(factory_id, days=30, center_id=None):
    """(labels, values) — total kilos bought per day for the last `days`
    days (including today), across all centers or just one. Every day in
    the window gets a value, even if it's zero, so the chart has no gaps."""
    start = date.today() - timedelta(days=days - 1)

    query = (
        db.session.query(db.func.date(Purchase.purchased_at).label("d"), db.func.sum(Purchase.kilos))
        .join(BuyingCenter)
        .filter(BuyingCenter.factory_id == factory_id, db.func.date(Purchase.purchased_at) >= start)
    )
    if center_id:
        query = query.filter(Purchase.buying_center_id == center_id)
    query = query.group_by("d")

    # Normalize keys — SQLite gives back date strings, Postgres gives back
    # date objects, depending on driver. Index by isoformat string either way.
    totals = {}
    for day_value, kilos in query.all():
        key = day_value.isoformat() if hasattr(day_value, "isoformat") else str(day_value)[:10]
        totals[key] = kilos

    labels, values = [], []
    for i in range(days):
        d = start + timedelta(days=i)
        labels.append(d.strftime("%d %b"))
        values.append(round(totals.get(d.isoformat(), 0) or 0, 1))
    return labels, values


def center_leaderboard(factory_id, day):
    """[{center, kilos}, ...] for a single day, sorted highest kilos first."""
    centers = BuyingCenter.query.filter_by(factory_id=factory_id).order_by(BuyingCenter.name).all()
    rows = [{"center": c, "kilos": total_kilos(factory_id, day, day, center_id=c.id)} for c in centers]
    rows.sort(key=lambda r: r["kilos"], reverse=True)
    return rows


def center_trend(factory_id, center_id, window=7):
    """Compare the average daily kilos over the last `window` days against
    the `window` days before that. Returns (direction, pct_change) where
    direction is 'improving' / 'dropping' / 'stagnant' (±8% is the
    stagnant band, to avoid noise from a single unusually big/small day)."""
    today = date.today()
    recent_start = today - timedelta(days=window - 1)
    prev_end = recent_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=window - 1)

    recent_avg = total_kilos(factory_id, recent_start, today, center_id=center_id) / window
    prev_avg = total_kilos(factory_id, prev_start, prev_end, center_id=center_id) / window

    if prev_avg == 0:
        return ("stagnant", 0.0) if recent_avg == 0 else ("improving", 100.0)

    pct_change = ((recent_avg - prev_avg) / prev_avg) * 100
    if pct_change > 8:
        return "improving", pct_change
    if pct_change < -8:
        return "dropping", pct_change
    return "stagnant", pct_change


def compare_periods(factory_id, mode, a_value, b_value, center_id=None):
    """Compare two periods (mode='days' -> single YYYY-MM-DD each, or
    mode='months' -> single YYYY-MM each) and return their totals + labels.
    Raises ValueError on bad input — callers should catch that."""

    def parse_period(value):
        if mode == "months":
            y, m = value.split("-")
            y, m = int(y), int(m)
            start = date(y, m, 1)
            end = (date(y + 1, 1, 1) - timedelta(days=1)) if m == 12 else (date(y, m + 1, 1) - timedelta(days=1))
            label = start.strftime("%B %Y")
        else:
            start = end = datetime.strptime(value, "%Y-%m-%d").date()
            label = start.strftime("%d %b %Y")
        return start, end, label

    a_start, a_end, a_label = parse_period(a_value)
    b_start, b_end, b_label = parse_period(b_value)

    return {
        "a": {"label": a_label, "total": round(total_kilos(factory_id, a_start, a_end, center_id=center_id), 1)},
        "b": {"label": b_label, "total": round(total_kilos(factory_id, b_start, b_end, center_id=center_id), 1)},
    }
