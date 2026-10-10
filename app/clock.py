"""Time helpers. The only place that produces UTC timestamps ("...Z") and does local-date arithmetic."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone


def _as_utc(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC; convert aware ones to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """UTC ISO-8601 with seconds precision and a Z suffix, e.g. "2026-10-07T14:03:00Z"."""
    return _as_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_now_iso() -> str:
    return iso(utc_now())


_FRACTION = re.compile(r"(T\d{2}:\d{2}:\d{2})\.(\d{1,6})(?=[+-]|$)")


def parse_iso(s: str) -> datetime:
    """Parse iso() output (also accepts 1–6 fractional-second digits and +HH:MM offsets). Returns aware UTC."""
    text = s.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    # Python 3.10's fromisoformat takes exactly 3 or 6 fraction digits; the learner API accepts 1–6.
    text = _FRACTION.sub(lambda m: f"{m.group(1)}.{m.group(2).ljust(6, '0')}", text)
    return _as_utc(datetime.fromisoformat(text))


def utc_date(dt: datetime | None = None) -> str:
    """The UTC calendar date of dt (default: now) as "YYYY-MM-DD"."""
    return _as_utc(dt if dt is not None else utc_now()).date().isoformat()


def add_days(local_date: str, n: int) -> str:
    """Calendar arithmetic on a "YYYY-MM-DD" string."""
    return (date.fromisoformat(local_date) + timedelta(days=n)).isoformat()


def add_seconds_iso(dt: datetime, seconds: float) -> str:
    return iso(_as_utc(dt) + timedelta(seconds=seconds))
