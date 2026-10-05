"""Unit tests for the fuel and maintenance rules (ARCHITECTURE.md 8.1, 8.5, 8.7). No database."""
from datetime import date
from decimal import Decimal

import pytest

from app.config import Config
from app.services import rules

D = date
INTERVALS = Config.SERVICE_INTERVALS


def test_total_cost_is_quantity_times_price_rounded_half_up():
    assert rules.fuel_total_cost(Decimal("33.33"), Decimal("2.18")) == Decimal("72.66")   # 72.6594
    assert rules.fuel_total_cost(Decimal("1.50"), Decimal("1.67")) == Decimal("2.51")     # 2.505 rounds up
    assert rules.fuel_total_cost(50, Decimal("1.66")) == Decimal("83.00")


def test_total_cost_always_satisfies_the_database_check():
    for q in ("0.01", "7.77", "123.45", "199.99"):
        for p in ("0.01", "1.66", "2.18", "9.99"):
            total = rules.fuel_total_cost(Decimal(q), Decimal(p))
            assert abs(total - Decimal(q) * Decimal(p)) <= Decimal("0.005")


@pytest.mark.parametrize("reading, prev, nxt, ok", [
    (150, 100, 200, True), (100, 100, 200, True), (200, 100, 200, True),   # both ends inclusive
    (99, 100, 200, False), (201, 100, 200, False),
    (50, None, 200, True), (500, 100, None, True), (0, None, None, True),
])
def test_odometer_bracket(reading, prev, nxt, ok):
    assert (rules.odometer_bracket_error(Decimal(reading), prev, nxt) is None) is ok


def test_bracket_messages_say_which_side_failed():
    assert "below the previous" in rules.odometer_bracket_error(Decimal(90), Decimal(100), None)
    assert "above the next" in rules.odometer_bracket_error(Decimal(210), None, Decimal(200))


@pytest.mark.parametrize("old, new, ok", [
    ("Scheduled", "In Progress", True), ("In Progress", "Completed", True),
    ("Scheduled", "Completed", False), ("In Progress", "Scheduled", False),
    ("Completed", "In Progress", False), ("Completed", "Completed", False), ("Scheduled", "Scheduled", False),
])
def test_maintenance_state_machine(old, new, ok):
    assert (rules.maintenance_transition_error(old, new) is None) is ok


def test_service_cannot_start_on_inactive_vehicle_or_during_a_trip():
    assert rules.service_start_block("Active", 0) is None
    assert rules.service_start_block("Under Maintenance", 0) is None     # a second service may start
    assert "inactive" in rules.service_start_block("Inactive", 0)
    assert "trip in progress" in rules.service_start_block("Active", 1)


@pytest.mark.parametrize("status, others, expected", [
    ("Under Maintenance", 0, "Active"),
    ("Under Maintenance", 1, "Under Maintenance"),     # another service is still running (R4)
    ("Inactive", 0, "Inactive"),                       # an admin's decision is not undone (R4)
    ("Active", 0, "Active"),
])
def test_vehicle_status_after_completion(status, others, expected):
    assert rules.vehicle_status_after_completion(status, others) == expected


def test_parts_may_equal_but_not_exceed_the_cost():
    assert rules.parts_total_error(Decimal(100), Decimal(100)) is None
    assert "exceeds" in rules.parts_total_error(Decimal("100.01"), Decimal(100))


def test_next_service_is_the_earlier_of_day_and_km_limit():
    start = D(2026, 10, 10)
    assert rules.next_service_date("Oil Change", start, INTERVALS, 100) == D(2027, 1, 18)    # 10000/100 = 100 days
    assert rules.next_service_date("Oil Change", start, INTERVALS, 10) == D(2027, 4, 8)      # 1000 km-days > 180: days
    assert rules.next_service_date("Tyres", start, INTERVALS, 50) == D(2028, 4, 2)           # 800 km-days > 540: days
    assert rules.next_service_date("Tyres", start, INTERVALS, 100) == D(2027, 11, 14)        # 400 km-days < 540: km
    assert rules.next_service_date("Inspection", start, INTERVALS, 1000) == D(2027, 10, 10)  # no km limit: 365 days


def test_next_service_without_trip_history_uses_only_the_day_limit():
    start = D(2026, 10, 10)
    assert rules.next_service_date("Oil Change", start, INTERVALS, None) == D(2027, 4, 8)
    assert rules.next_service_date("Oil Change", start, INTERVALS, 0) == D(2027, 4, 8)


def test_breakdown_repair_has_no_next_service():
    assert rules.next_service_date("Breakdown Repair", D(2026, 10, 10), INTERVALS, 100) is None
