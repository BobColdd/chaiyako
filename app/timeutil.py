"""Time conventions, in one place.

* Everything is STORED as naive UTC (what the database columns hold).
* Everything is SHOWN in East Africa Time (UTC+3, no daylight saving).
* Report "days" are UTC dates. Tea buying happens 03:00-24:00 EAT, where the
  UTC date and the Kenyan date are the same day, so the two agree in practice.
"""
from datetime import datetime, timedelta, timezone

EAT_OFFSET = timedelta(hours=3)


def utcnow():
    """Current time as naive UTC (datetime.utcnow() is deprecated)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def today_utc():
    return utcnow().date()


def to_eat(moment):
    """Convert a stored (naive UTC) datetime to East Africa Time for display."""
    return moment + EAT_OFFSET if moment else None
