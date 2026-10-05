"""Pure business rules (ARCHITECTURE.md 8): rows and values in, a decision out. No database, no request.

The fuel and maintenance rules are added on their days.
"""
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from ..constants import LICENCE_WARNING_DAYS


def licence_warning(expiry, today, days=LICENCE_WARNING_DAYS):
    """8.2: the licence runs out within the next `days` days (today included, already expired excluded)."""
    return today <= expiry <= today + timedelta(days=days)


def licence_valid_on(expiry, day):
    """8.3 check 3: the licence is still valid on `day` (the expiry date itself counts as valid)."""
    return expiry >= day


def periods_overlap(start_a, end_a, start_b, end_b):
    """8.3 check 4/5: whole-day, inclusive periods share at least one calendar day."""
    return start_a <= end_b and end_a >= start_b


def vehicle_deactivation_block(open_trips, open_services):
    """8.4: why a vehicle cannot be deactivated, or None if it can."""
    if open_trips:
        return f"Vehicle has {open_trips} planned or in-progress trip(s); cancel or complete them first"
    if open_services:
        return f"Vehicle has {open_services} open service(s); complete them first"
    return None


def driver_deactivation_block(open_trips):
    """A driver with a planned or in-progress trip cannot be deactivated (same idea as 8.4)."""
    if open_trips:
        return f"Driver has {open_trips} planned or in-progress trip(s); reassign or cancel them first"
    return None


# --- trips (8.3, 8.6) and assignments -------------------------------------------------------------------------

TRIP_TRANSITIONS = {
    "Planned": ("In Progress", "Cancelled"),
    "In Progress": ("Completed", "Cancelled"),
    "Completed": (),
    "Cancelled": (),
}


def trip_transition_error(old, new):
    """8.6: why a trip cannot move from `old` to `new`, or None."""
    if new == old or new in TRIP_TRANSITIONS[old]:
        return None
    allowed = ", ".join(TRIP_TRANSITIONS[old]) or "nothing (final state)"
    return f"A {old} trip cannot become {new}; allowed: {allowed}"


def trip_start_block(vehicle_status, driver_status):
    """Starting a trip needs an Active vehicle and driver, so a vehicle sent to service meanwhile cannot leave."""
    if vehicle_status != "Active":
        return f"Vehicle is {vehicle_status}; a trip can only start on an Active vehicle"
    if driver_status != "Active":
        return "Driver is not active"
    return None


def trip_rejection(*, start, end, today, vehicle_status, driver_status, license_expiry, driver_assigned,
                   vehicle_conflicts, driver_conflicts, blocking_services):
    """8.3: run the six checks in order. Returns (field, reason) for the first failure, or None.

    `vehicle_conflicts` / `driver_conflicts` are ids of other non-cancelled trips that overlap the period;
    `blocking_services` is the number of Scheduled or In Progress services dated inside it.
    """
    if vehicle_status == "Inactive":
        return "vehicle_id", "Vehicle is inactive"
    if start <= today and vehicle_status != "Active":
        return "vehicle_id", f"Vehicle is {vehicle_status}; a trip starting today or earlier needs an Active vehicle"
    if vehicle_status == "Under Maintenance" and blocking_services:
        return "vehicle_id", "Vehicle has a service scheduled or in progress during the trip period"
    if driver_status != "Active":
        return "driver_id", "Driver is not active"
    if not licence_valid_on(license_expiry, end):
        return "driver_id", f"Driver's licence expires on {license_expiry}, before the trip ends on {end}"
    if vehicle_conflicts:
        return "start_date", f"Vehicle already has trip #{vehicle_conflicts[0]} in this period"
    if driver_conflicts:
        return "start_date", f"Driver already has trip #{driver_conflicts[0]} in this period"
    if not driver_assigned:
        return "driver_id", f"Driver is not assigned to this vehicle on {start}"
    return None


def assignment_conflict(start, end, others):
    """Assignments for one vehicle may not overlap. `others` are (assignment_id, start, end-or-None); None is open."""
    far = date.max
    for assignment_id, o_start, o_end in others:
        if periods_overlap(start, end or far, o_start, o_end or far):
            return assignment_id
    return None


# --- fuel (8.1, 8.7) and maintenance (8.5) --------------------------------------------------------------------

MAINTENANCE_TRANSITIONS = {"Scheduled": ("In Progress",), "In Progress": ("Completed",), "Completed": ()}


def fuel_total_cost(quantity, price_per_litre):
    """8.1: total_cost = quantity x price, to the halala. Always computed here, never taken from the client."""
    return (Decimal(quantity) * Decimal(price_per_litre)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def odometer_bracket_error(reading, previous, following):
    """8.7: a reading must be at least the previous one and at most the next one (by date) for that vehicle."""
    if previous is not None and reading < previous:
        return f"Odometer {reading} is below the previous reading {previous}"
    if following is not None and reading > following:
        return f"Odometer {reading} is above the next reading {following}"
    return None


def maintenance_transition_error(old, new):
    """8.5: only Scheduled -> In Progress -> Completed; Completed is read-only."""
    if new in MAINTENANCE_TRANSITIONS[old]:
        return None
    allowed = ", ".join(MAINTENANCE_TRANSITIONS[old]) or "nothing (completed records are read-only)"
    return f"A {old} service cannot become {new}; allowed: {allowed}"


def service_start_block(vehicle_status, trips_in_progress):
    """8.5 / R4: a service cannot start on an inactive vehicle or while the vehicle is out on a trip."""
    if vehicle_status == "Inactive":
        return "Vehicle is inactive"
    if trips_in_progress:
        return "Vehicle has a trip in progress; complete or cancel it before starting a service"
    return None


def vehicle_status_after_completion(vehicle_status, other_services_in_progress):
    """8.5 / R4: back to Active only if no other service is still running and an admin has not made it Inactive."""
    if vehicle_status == "Under Maintenance" and not other_services_in_progress:
        return "Active"
    return vehicle_status


def parts_total_error(parts_total, cost):
    """8.5: parts itemise the invoice, so their total may not exceed it."""
    if parts_total > cost:
        return f"Parts total {parts_total} exceeds the record cost {cost}"
    return None


def next_service_date(service_type, service_date, intervals, avg_daily_km):
    """8.5: the earlier of service_date + day limit and service_date + (km limit / average daily km).

    `intervals` maps type -> (max_days, max_km); None means no limit on that axis. A type that is not listed
    (Breakdown Repair) has no next service. Without trip history (avg_daily_km falsy) only the day limit applies.
    """
    if service_type not in intervals:
        return None
    max_days, max_km = intervals[service_type]
    days = max_days
    if max_km is not None and avg_daily_km and avg_daily_km > 0:
        days = min(days, int(max_km / avg_daily_km))
    return service_date + timedelta(days=days)
