"""Unit tests for the trip rules (ARCHITECTURE.md 8.3, 8.6) and assignment overlap. No database."""
from datetime import date

import pytest

from app.services import rules

D = date
TODAY = D(2026, 10, 5)


def check(**kw):
    base = dict(start=D(2026, 10, 10), end=D(2026, 10, 12), today=TODAY, vehicle_status="Active",
                driver_status="Active", license_expiry=D(2027, 1, 1), driver_assigned=True,
                vehicle_conflicts=[], driver_conflicts=[], blocking_services=0)
    return rules.trip_rejection(**{**base, **kw})


def test_a_clean_trip_is_accepted():
    assert check() is None


def test_check1_inactive_vehicle_is_always_refused():
    assert check(vehicle_status="Inactive", start=D(2026, 12, 1), end=D(2026, 12, 2))[0] == "vehicle_id"


def test_check1_trip_starting_today_or_earlier_needs_an_active_vehicle():
    assert check(vehicle_status="Under Maintenance", start=TODAY)[0] == "vehicle_id"
    assert check(vehicle_status="Under Maintenance", start=D(2026, 10, 1), end=D(2026, 10, 2))[0] == "vehicle_id"


def test_check1_future_trip_may_be_planned_during_a_service():
    assert check(vehicle_status="Under Maintenance", start=D(2026, 10, 6)) is None


def test_check1_unless_a_service_falls_inside_the_trip_period():
    field, reason = check(vehicle_status="Under Maintenance", start=D(2026, 10, 6), blocking_services=1)
    assert field == "vehicle_id" and "service" in reason


def test_check2_inactive_driver():
    assert check(driver_status="Inactive") == ("driver_id", "Driver is not active")


def test_check3_licence_must_be_valid_on_the_end_date_not_the_start():
    assert check(license_expiry=D(2026, 10, 12)) is None                 # last valid day is the end date
    field, reason = check(license_expiry=D(2026, 10, 11))                # expires mid-trip
    assert field == "driver_id" and "2026-10-11" in reason and "2026-10-12" in reason


def test_check4_and_5_overlaps_name_the_other_trip():
    assert check(vehicle_conflicts=[7, 9]) == ("start_date", "Vehicle already has trip #7 in this period")
    assert check(driver_conflicts=[4]) == ("start_date", "Driver already has trip #4 in this period")


def test_check6_driver_must_be_assigned():
    assert check(driver_assigned=False)[0] == "driver_id"


def test_checks_run_in_the_documented_order():
    everything_wrong = dict(vehicle_status="Inactive", driver_status="Inactive", license_expiry=D(2020, 1, 1),
                            vehicle_conflicts=[1], driver_conflicts=[2], driver_assigned=False)
    assert "inactive" in check(**everything_wrong)[1]
    everything_wrong["vehicle_status"] = "Active"
    assert check(**everything_wrong)[1] == "Driver is not active"
    everything_wrong["driver_status"] = "Active"
    assert "licence" in check(**everything_wrong)[1]
    everything_wrong["license_expiry"] = D(2030, 1, 1)
    assert "Vehicle already" in check(**everything_wrong)[1]
    everything_wrong["vehicle_conflicts"] = []
    assert "Driver already" in check(**everything_wrong)[1]
    everything_wrong["driver_conflicts"] = []
    assert "not assigned" in check(**everything_wrong)[1]


@pytest.mark.parametrize("old, new, ok", [
    ("Planned", "In Progress", True), ("Planned", "Cancelled", True), ("In Progress", "Completed", True),
    ("In Progress", "Cancelled", True), ("Planned", "Completed", False), ("In Progress", "Planned", False),
    ("Completed", "Cancelled", False), ("Completed", "In Progress", False), ("Cancelled", "Planned", False),
    ("Planned", "Planned", True),
])
def test_trip_state_machine(old, new, ok):
    assert (rules.trip_transition_error(old, new) is None) is ok


def test_starting_a_trip_needs_active_vehicle_and_driver():
    assert rules.trip_start_block("Active", "Active") is None
    assert "Under Maintenance" in rules.trip_start_block("Under Maintenance", "Active")
    assert "Driver" in rules.trip_start_block("Active", "Inactive")


def test_assignment_conflict_treats_no_end_date_as_open():
    others = [(1, D(2026, 1, 1), D(2026, 3, 31)), (2, D(2026, 6, 1), None)]
    assert rules.assignment_conflict(D(2026, 4, 1), D(2026, 5, 31), others) is None
    assert rules.assignment_conflict(D(2026, 3, 31), D(2026, 4, 2), others) == 1      # shares the last day
    assert rules.assignment_conflict(D(2027, 1, 1), None, others) == 2                # open versus open
