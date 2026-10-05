"""Unit tests: no database, no Flask app."""
from datetime import date

import pytest

from app import clock
from app import validation as v
from app.errors import ApiError
from app.security import LoginThrottle
from app.services import rules

D = date


def test_licence_warning_window_is_today_through_thirty_days():
    today = D(2026, 10, 5)
    assert rules.licence_warning(D(2026, 10, 5), today)          # expires today: warn
    assert rules.licence_warning(D(2026, 11, 4), today)          # day 30: warn
    assert not rules.licence_warning(D(2026, 11, 5), today)      # day 31: not yet
    assert not rules.licence_warning(D(2026, 10, 4), today)      # already expired: expired, not "warning"


def test_licence_valid_on_expiry_date_itself():
    assert rules.licence_valid_on(D(2026, 10, 12), D(2026, 10, 12))
    assert not rules.licence_valid_on(D(2026, 10, 11), D(2026, 10, 12))


@pytest.mark.parametrize("a, b, expected", [
    ((D(2026, 10, 1), D(2026, 10, 5)), (D(2026, 10, 5), D(2026, 10, 7)), True),    # share the 5th
    ((D(2026, 10, 1), D(2026, 10, 4)), (D(2026, 10, 5), D(2026, 10, 7)), False),   # back to back
    ((D(2026, 10, 1), D(2026, 10, 9)), (D(2026, 10, 3), D(2026, 10, 4)), True),    # contained
])
def test_periods_overlap(a, b, expected):
    assert rules.periods_overlap(*a, *b) is expected


def test_deactivation_blocks():
    assert rules.vehicle_deactivation_block(0, 0) is None
    assert "trip" in rules.vehicle_deactivation_block(1, 0)
    assert "service" in rules.vehicle_deactivation_block(0, 2)
    assert rules.driver_deactivation_block(0) is None
    assert "trip" in rules.driver_deactivation_block(1)


def test_clock_freeze_and_unfreeze():
    clock.freeze(D(2026, 1, 2))
    try:
        assert clock.today() == D(2026, 1, 2)
    finally:
        clock.unfreeze()
    assert isinstance(clock.today(), date)  # real clock again, in APP_TIMEZONE


class FakeClock:
    now = 1000.0

    def __call__(self):
        return self.now


def test_throttle_locks_after_max_failures_and_expires():
    clk = FakeClock()
    t = LoginThrottle(max_failures=3, window=60, clock=clk)
    key = ("a@x.com", "1.1.1.1")
    for _ in range(2):
        t.record_failure(key)
    assert t.retry_after(key) == 0
    t.record_failure(key)
    assert t.retry_after(key) == 60
    clk.now += 59
    assert t.retry_after(key) == 1
    clk.now += 2
    assert t.retry_after(key) == 0


def test_throttle_ignores_old_failures_and_is_per_key():
    clk = FakeClock()
    t = LoginThrottle(max_failures=3, window=60, clock=clk)
    key, other = ("a@x.com", "1.1.1.1"), ("b@x.com", "1.1.1.1")
    t.record_failure(key)
    t.record_failure(key)
    clk.now += 61                      # those two are outside the window now
    t.record_failure(key)
    assert t.retry_after(key) == 0
    for _ in range(3):
        t.record_failure(other)
    assert t.retry_after(other) > 0 and t.retry_after(key) == 0


def test_throttle_reset_after_success():
    t = LoginThrottle(max_failures=2, window=60, clock=FakeClock())
    key = ("a@x.com", "1.1.1.1")
    t.record_failure(key)
    t.reset(key)
    t.record_failure(key)
    assert t.retry_after(key) == 0


def _err(spec, data, **kw):
    with pytest.raises(ApiError) as e:
        v.parse(data, spec, **kw)
    return e.value


def test_parse_required_unknown_and_bad_values():
    spec = {"name": v.Field(v.text(5)), "year": v.Field(v.integer(2000, 2030)), "note": v.Field(v.text(9), required=False)}
    assert v.parse({"name": " abc ", "year": 2020}, spec) == {"name": "abc", "year": 2020}
    e = _err(spec, {"year": 2020})
    assert (e.status, e.field) == (422, "name")
    e = _err(spec, {"name": "a", "year": 2020, "role": "x"})
    assert (e.status, e.field) == (400, "role")                     # mass assignment is refused, not ignored
    assert _err(spec, {"name": "toolong", "year": 2020}).field == "name"
    assert _err(spec, {"name": "a", "year": True}).field == "year"  # bool is not a whole number
    assert _err(spec, {"name": "a", "year": 1999}).field == "year"
    assert v.parse({"year": 2021}, spec, partial=True) == {"year": 2021}


def test_parse_nullable_and_decimal_and_date():
    spec = {"phone": v.Field(v.text(20), required=False, nullable=True),
            "odo": v.Field(v.decimal(0, 100, 1)), "day": v.Field(v.iso_date)}
    out = v.parse({"phone": "", "odo": "12.5", "day": "2026-10-05"}, spec)
    assert out["phone"] is None and str(out["odo"]) == "12.5" and out["day"] == D(2026, 10, 5)
    assert _err(spec, {"odo": 1.25, "day": "2026-10-05"}).field == "odo"       # too many decimals
    assert _err(spec, {"odo": 5, "day": "2026-02-30"}).field == "day"          # not a real date
    assert _err(spec, {"odo": 5, "day": "20261005"}).field == "day"            # only YYYY-MM-DD
    assert _err(spec, {"odo": "nan", "day": "2026-10-05"}).field == "odo"
    assert _err(spec, {"odo": -1, "day": "2026-10-05"}).field == "odo"


def test_like_pattern_escapes_wildcards():
    assert v.like_pattern("50%_x") == "%50\\%\\_x%"
