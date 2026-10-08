from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from app import clock

UTC = timezone.utc


@pytest.mark.parametrize(
    ("start", "n", "expected"),
    [
        ("2026-10-07", 0, "2026-10-07"),
        ("2026-10-07", 1, "2026-10-08"),
        ("2026-01-31", 1, "2026-02-01"),  # month boundary
        ("2026-12-31", 1, "2027-01-01"),  # year boundary
        ("2028-02-28", 1, "2028-02-29"),  # leap day exists in 2028
        ("2028-02-29", 1, "2028-03-01"),
        ("2027-02-28", 1, "2027-03-01"),  # no leap day in 2027
        ("2026-03-01", -1, "2026-02-28"),  # negative n
        ("2026-10-07", 30, "2026-11-06"),
        ("2026-12-15", 60, "2027-02-13"),
    ],
)
def test_add_days(start: str, n: int, expected: str) -> None:
    assert clock.add_days(start, n) == expected


def test_iso_is_seconds_precision_with_z_suffix() -> None:
    dt = datetime(2026, 10, 7, 14, 3, 0, 987654, tzinfo=UTC)
    assert clock.iso(dt) == "2026-10-07T14:03:00Z"
    assert clock.iso(dt).endswith("Z")


def test_iso_converts_other_timezones_and_treats_naive_as_utc() -> None:
    eastern = timezone(timedelta(hours=-4))
    assert clock.iso(datetime(2026, 10, 7, 10, 3, tzinfo=eastern)) == "2026-10-07T14:03:00Z"
    assert clock.iso(datetime(2026, 10, 7, 14, 3)) == "2026-10-07T14:03:00Z"


def test_utc_now_is_aware_utc() -> None:
    now = clock.utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_utc_now_iso_format() -> None:
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", clock.utc_now_iso())


def test_parse_iso_round_trip() -> None:
    dt = datetime(2026, 10, 7, 14, 3, 9, tzinfo=UTC)
    parsed = clock.parse_iso(clock.iso(dt))
    assert parsed == dt
    assert parsed.tzinfo is not None


def test_parse_iso_accepts_browser_millis_and_offsets() -> None:
    assert clock.parse_iso("2026-10-07T14:03:00.250Z") == datetime(2026, 10, 7, 14, 3, 0, 250000, tzinfo=UTC)
    assert clock.parse_iso("2026-10-07T10:03:00-04:00") == datetime(2026, 10, 7, 14, 3, tzinfo=UTC)


def test_utc_date() -> None:
    late_evening_eastern = datetime(2026, 10, 7, 23, 30, tzinfo=timezone(timedelta(hours=-4)))
    assert clock.utc_date(late_evening_eastern) == "2026-10-08"
    assert clock.utc_date(datetime(2026, 1, 1, 0, 0, tzinfo=UTC)) == "2026-01-01"
    assert clock.utc_date() == datetime.now(UTC).date().isoformat()


def test_add_seconds_iso() -> None:
    dt = datetime(2026, 10, 7, 23, 59, 30, tzinfo=UTC)
    assert clock.add_seconds_iso(dt, 45) == "2026-10-08T00:00:15Z"
    assert clock.add_seconds_iso(dt, 0.5) == "2026-10-07T23:59:30Z"
    assert clock.add_seconds_iso(dt, 300) == "2026-10-08T00:04:30Z"
