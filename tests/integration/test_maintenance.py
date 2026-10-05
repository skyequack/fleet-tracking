import threading
from datetime import date

from app import models
from app.extensions import db
from tests.conftest import USERS, Api


def new(vid, **kw):
    return {"vehicle_id": vid, "service_type": "Oil Change", "service_date": "2026-10-10", "cost": 500, **kw}


def vehicle_status(world, vid):
    with world.app_context():
        return db.session.get(models.Vehicle, vid).status


def vehicle_odometer(world, vid):
    with world.app_context():
        return float(db.session.get(models.Vehicle, vid).odometer)


def start(api, mid, **kw):
    return api.put(f"/api/maintenance/{mid}/status", json={"status": "In Progress", **kw})


def finish(api, mid, **kw):
    return api.put(f"/api/maintenance/{mid}/status", json={"status": "Completed", **kw})


# --- creating records and parts -------------------------------------------------------------------------------

def test_operator_creates_a_scheduled_record_manager_cannot(operator, manager, seed):
    vid = seed(models.Vehicle, odometer=4321)
    assert manager.post("/api/maintenance", json=new(vid)).status_code == 403
    r = operator.post("/api/maintenance", json=new(vid))
    assert r.status_code == 201 and r.json["status"] == "Scheduled" and r.json["parts"] == []
    assert r.json["odometer"] == 4321 and r.json["next_service_date"] is None   # placeholder until it starts


def test_status_and_next_date_cannot_be_set_on_create(operator, seed):
    vid = seed(models.Vehicle)
    for name in ("status", "next_service_date", "created_by"):
        r = operator.post("/api/maintenance", json=new(vid, **{name: "x"}))
        assert r.status_code == 400 and r.json["field"] == name


def test_create_validation(operator, seed):
    vid = seed(models.Vehicle)
    for bad, field in [({"service_type": "Wash"}, "service_type"), ({"cost": -1}, "cost"),
                       ({"service_date": "tomorrow"}, "service_date"), ({"vehicle_id": 999}, "vehicle_id")]:
        r = operator.post("/api/maintenance", json=new(vid, **bad))
        assert r.status_code == 422 and r.json["field"] == field, bad
    off = seed(models.Vehicle, registration_no="OFF 0001", status="Inactive")
    assert operator.post("/api/maintenance", json=new(off)).status_code == 422


def test_parts_are_limited_by_the_record_cost(operator, seed):
    vid = seed(models.Vehicle)
    mid = operator.post("/api/maintenance", json=new(vid, cost=100)).json["maintenance_id"]
    ok = operator.post(f"/api/maintenance/{mid}/parts", json={"part_name": "Filter", "quantity": 2, "unit_cost": 30})
    assert ok.status_code == 201 and len(ok.json["parts"]) == 1                       # 60 of 100
    too_much = operator.post(f"/api/maintenance/{mid}/parts", json={"part_name": "Oil", "quantity": 1, "unit_cost": 41})
    assert too_much.status_code == 422 and "exceeds" in too_much.json["error"]
    exact = operator.post(f"/api/maintenance/{mid}/parts", json={"part_name": "Oil", "quantity": 1, "unit_cost": 40})
    assert exact.status_code == 201 and len(exact.json["parts"]) == 2


def test_parts_validation_roles_and_completed_records(operator, manager, seed):
    vid = seed(models.Vehicle)
    mid = seed(models.MaintenanceRecord, vehicle_id=vid, cost=100)
    done = seed(models.MaintenanceRecord, vehicle_id=vid, cost=100, status="Completed")
    part = {"part_name": "Filter", "quantity": 1, "unit_cost": 10}
    assert manager.post(f"/api/maintenance/{mid}/parts", json=part).status_code == 403
    assert operator.post(f"/api/maintenance/{done}/parts", json=part).status_code == 422
    assert operator.post("/api/maintenance/999/parts", json=part).status_code == 404
    for bad, field in [({"quantity": 0}, "quantity"), ({"unit_cost": -1}, "unit_cost"), ({"part_name": ""}, "part_name")]:
        r = operator.post(f"/api/maintenance/{mid}/parts", json={**part, **bad})
        assert r.status_code == 422 and r.json["field"] == field, bad


# --- the state machine and vehicle status ---------------------------------------------------------------------

def test_tc07_start_makes_the_vehicle_under_maintenance_and_completing_returns_it_to_active(operator, seed, world):
    vid = seed(models.Vehicle)
    mid = operator.post("/api/maintenance", json=new(vid)).json["maintenance_id"]
    r = start(operator, mid)
    assert r.status_code == 200 and r.json["record"]["status"] == "In Progress"
    assert r.json["vehicle_status"] == "Under Maintenance" == vehicle_status(world, vid)
    r = finish(operator, mid)
    assert r.status_code == 200 and r.json["record"]["status"] == "Completed"
    assert r.json["vehicle_status"] == "Active" == vehicle_status(world, vid)


def test_invalid_transitions_are_422(operator, seed):
    vid = seed(models.Vehicle)
    mid = operator.post("/api/maintenance", json=new(vid)).json["maintenance_id"]
    r = finish(operator, mid)                                       # skipping In Progress
    assert r.status_code == 422 and r.json["field"] == "status"
    start(operator, mid)
    assert start(operator, mid).status_code == 422                  # already running
    assert operator.put(f"/api/maintenance/{mid}/status", json={"status": "Scheduled"}).status_code == 422
    finish(operator, mid)
    assert finish(operator, mid).status_code == 422                 # completed records are read-only
    assert operator.put("/api/maintenance/999/status", json={"status": "In Progress"}).status_code == 404


def test_manager_cannot_advance_a_service(manager, seed):
    mid = seed(models.MaintenanceRecord, vehicle_id=seed(models.Vehicle))
    assert start(manager, mid).status_code == 403


def test_a_service_cannot_start_while_the_vehicle_is_on_a_trip(operator, seed, world):
    vid, did = seed(models.Vehicle), seed(models.Driver)
    seed(models.Trip, vehicle_id=vid, driver_id=did, status="In Progress")
    mid = seed(models.MaintenanceRecord, vehicle_id=vid)
    r = start(operator, mid)
    assert r.status_code == 422 and "trip in progress" in r.json["error"]
    assert vehicle_status(world, vid) == "Active"


def test_a_service_cannot_start_on_an_inactive_vehicle(operator, seed):
    mid = seed(models.MaintenanceRecord, vehicle_id=seed(models.Vehicle, status="Inactive"))
    assert start(operator, mid).status_code == 422


def test_planned_trips_in_the_window_become_warnings_not_blockers(operator, seed):
    vid, did = seed(models.Vehicle), seed(models.Driver)
    soon = seed(models.Trip, vehicle_id=vid, driver_id=did, start_date=date(2026, 10, 8), end_date=date(2026, 10, 9))
    seed(models.Trip, vehicle_id=vid, driver_id=did, start_date=date(2026, 10, 20), end_date=date(2026, 10, 21))
    seed(models.Trip, vehicle_id=vid, driver_id=did, start_date=date(2026, 10, 6), end_date=date(2026, 10, 6),
         status="Cancelled")
    mid = seed(models.MaintenanceRecord, vehicle_id=vid)
    r = start(operator, mid)
    assert r.status_code == 200
    assert [w["trip_id"] for w in r.json["warnings"]] == [soon]       # today..today+6 only; cancelled ignored


def test_completing_keeps_the_vehicle_under_maintenance_while_another_service_runs(operator, seed, world):
    vid = seed(models.Vehicle)
    first = seed(models.MaintenanceRecord, vehicle_id=vid)
    second = seed(models.MaintenanceRecord, vehicle_id=vid, service_type="Brakes")
    start(operator, first)
    start(operator, second)
    assert finish(operator, first).json["vehicle_status"] == "Under Maintenance"
    assert finish(operator, second).json["vehicle_status"] == "Active"
    assert vehicle_status(world, vid) == "Active"


def test_completing_never_reactivates_an_inactive_vehicle(operator, seed, world):
    vid = seed(models.Vehicle, status="Inactive")
    mid = seed(models.MaintenanceRecord, vehicle_id=vid, status="In Progress")
    assert finish(operator, mid).json["vehicle_status"] == "Inactive"
    assert vehicle_status(world, vid) == "Inactive"


# --- readings, cost and next service date ---------------------------------------------------------------------

def test_starting_stamps_the_vehicle_reading_unless_one_is_supplied(operator, seed, world):
    vid = seed(models.Vehicle, odometer=5000)
    plain = seed(models.MaintenanceRecord, vehicle_id=vid, odometer=1)
    assert start(operator, plain).json["record"]["odometer"] == 5000
    finish(operator, plain)
    other = seed(models.MaintenanceRecord, vehicle_id=vid, service_type="Brakes", service_date=date(2026, 10, 12))
    r = start(operator, other, odometer=5100)
    assert r.json["record"]["odometer"] == 5100 and vehicle_odometer(world, vid) == 5100


def test_a_supplied_reading_is_bracketed_among_real_readings(operator, seed, world):
    vid = seed(models.Vehicle, odometer=5000)
    seed(models.MaintenanceRecord, vehicle_id=vid, status="Completed", service_date=date(2026, 9, 1), odometer=4000)
    seed(models.MaintenanceRecord, vehicle_id=vid, status="Completed", service_date=date(2026, 11, 1), odometer=6000)
    seed(models.MaintenanceRecord, vehicle_id=vid, status="Scheduled", service_date=date(2026, 9, 5), odometer=99999)
    mid = seed(models.MaintenanceRecord, vehicle_id=vid, service_date=date(2026, 10, 10))
    low = start(operator, mid, odometer=3999)
    high = start(operator, mid, odometer=6001)
    assert low.status_code == high.status_code == 422 and low.json["field"] == "odometer"
    assert vehicle_status(world, vid) == "Active"                      # the failed attempts changed nothing
    assert start(operator, mid, odometer=5500).status_code == 200      # the Scheduled placeholder is ignored


def test_cost_can_be_updated_but_not_below_the_parts_already_listed(operator, seed):
    vid = seed(models.Vehicle)
    mid = operator.post("/api/maintenance", json=new(vid, cost=100)).json["maintenance_id"]
    operator.post(f"/api/maintenance/{mid}/parts", json={"part_name": "Filter", "quantity": 1, "unit_cost": 80})
    start(operator, mid)
    r = finish(operator, mid, cost=50)
    assert r.status_code == 422 and r.json["field"] == "cost"
    r = finish(operator, mid, cost=250.5, technician="Khalid")
    assert r.json["record"]["cost"] == 250.5 and r.json["record"]["technician"] == "Khalid"


def test_next_service_date_uses_the_day_limit_without_trip_history(operator, seed):
    mid = seed(models.MaintenanceRecord, vehicle_id=seed(models.Vehicle), service_date=date(2026, 10, 10))
    start(operator, mid)
    assert finish(operator, mid).json["record"]["next_service_date"] == "2027-04-08"      # +180 days


def test_next_service_date_projects_the_km_limit_from_the_last_90_days_of_trips(operator, seed):
    vid, did = seed(models.Vehicle), seed(models.Driver)
    # 9,000 km completed inside the window = 100 km/day; an old trip and a planned one must not count
    seed(models.Trip, vehicle_id=vid, driver_id=did, status="Completed", distance=9000,
         start_date=date(2026, 8, 1), end_date=date(2026, 8, 2))
    seed(models.Trip, vehicle_id=vid, driver_id=did, status="Completed", distance=50000,
         start_date=date(2026, 5, 1), end_date=date(2026, 5, 2))
    seed(models.Trip, vehicle_id=vid, driver_id=did, status="Planned", distance=50000,
         start_date=date(2026, 10, 20), end_date=date(2026, 10, 21))
    mid = seed(models.MaintenanceRecord, vehicle_id=vid, service_date=date(2026, 10, 10))
    start(operator, mid)
    assert finish(operator, mid).json["record"]["next_service_date"] == "2027-01-18"      # 10000 km / 100 = 100 days


def test_next_service_date_uses_only_this_vehicles_trips(operator, seed):
    """Regression: the km projection once used the whole fleet's distance, so a vehicle without trips got a date days away."""
    busy, idle, d = seed(models.Vehicle), seed(models.Vehicle, registration_no="IDL 0001"), seed(models.Driver)
    seed(models.Trip, vehicle_id=busy, driver_id=d, status="Completed", distance=90000,       # 1,000 km/day for the busy one
         start_date=date(2026, 8, 1), end_date=date(2026, 8, 2))
    mid = seed(models.MaintenanceRecord, vehicle_id=idle, service_date=date(2026, 10, 10))
    start(operator, mid)
    assert finish(operator, mid).json["record"]["next_service_date"] == "2027-04-08"             # no history: +180 days
    busy_mid = seed(models.MaintenanceRecord, vehicle_id=busy, service_date=date(2026, 10, 10))
    start(operator, busy_mid)
    assert finish(operator, busy_mid).json["record"]["next_service_date"] == "2026-10-20"        # 10,000 km / 1,000 per day = 10 days


def test_breakdown_repair_sets_no_next_service_date(operator, seed):
    mid = seed(models.MaintenanceRecord, vehicle_id=seed(models.Vehicle), service_type="Breakdown Repair")
    start(operator, mid)
    assert finish(operator, mid).json["record"]["next_service_date"] is None


# --- listing and upcoming -------------------------------------------------------------------------------------

def test_operators_see_open_records_only_but_managers_and_admins_see_history(operator, manager, admin, seed):
    vid = seed(models.Vehicle)
    seed(models.MaintenanceRecord, vehicle_id=vid, status="Completed", service_date=date(2026, 9, 1))
    seed(models.MaintenanceRecord, vehicle_id=vid, status="Scheduled")
    seed(models.MaintenanceRecord, vehicle_id=vid, status="In Progress", service_date=date(2026, 10, 3))
    assert operator.get("/api/maintenance").json["total"] == 2
    assert operator.get("/api/maintenance?status=Completed").json["total"] == 0
    assert manager.get("/api/maintenance").json["total"] == 3
    assert admin.get("/api/maintenance?status=Completed").json["total"] == 1


def test_list_filters_and_parts_are_included(manager, seed):
    a, b = seed(models.Vehicle), seed(models.Vehicle, registration_no="DEF 5678")
    m = seed(models.MaintenanceRecord, vehicle_id=a, service_type="Brakes", cost=100)
    seed(models.MaintenancePart, maintenance_id=m, part_name="Pads", quantity=2, unit_cost=20)
    seed(models.MaintenanceRecord, vehicle_id=b)
    assert manager.get(f"/api/maintenance?vehicle_id={a}").json["total"] == 1
    assert manager.get("/api/maintenance?service_type=Brakes").json["items"][0]["parts"][0]["part_name"] == "Pads"
    assert manager.get("/api/maintenance?status=Bogus").status_code == 400
    assert manager.get("/api/maintenance?vehicle_id=x").status_code == 400


def test_maintenance_needs_a_session(anon):
    assert anon.get("/api/maintenance").status_code == 401
    assert anon.get("/api/maintenance/upcoming").status_code == 401


def test_upcoming_lists_scheduled_and_due_services_for_every_role(operator, seed):
    vid = seed(models.Vehicle)
    seed(models.MaintenanceRecord, vehicle_id=vid, status="Scheduled", service_date=date(2026, 10, 20))
    seed(models.MaintenanceRecord, vehicle_id=vid, status="Completed", service_type="Brakes",
         service_date=date(2026, 4, 1), next_service_date=date(2026, 10, 20))          # due in 15 days
    seed(models.MaintenanceRecord, vehicle_id=vid, status="Completed", service_type="Tyres",
         service_date=date(2025, 4, 1), next_service_date=date(2026, 10, 1))           # overdue
    seed(models.MaintenanceRecord, vehicle_id=vid, status="Completed", service_type="Inspection",
         service_date=date(2026, 6, 1), next_service_date=date(2027, 6, 1))            # far away
    r = operator.get("/api/maintenance/upcoming").json
    assert [s["service_type"] for s in r["scheduled"]] == ["Oil Change"]
    due = {d["service_type"]: d for d in r["due"]}
    assert set(due) == {"Brakes", "Tyres"} and due["Tyres"]["overdue"] and not due["Brakes"]["overdue"]
    assert [d["service_type"] for d in r["due"]] == ["Tyres", "Brakes"]                # most overdue first
    assert {d["service_type"] for d in operator.get("/api/maintenance/upcoming?days=300").json["due"]} == \
        {"Brakes", "Tyres", "Inspection"}


def test_a_service_is_not_due_if_a_newer_one_or_a_booking_exists(operator, seed):
    vid = seed(models.Vehicle)
    seed(models.MaintenanceRecord, vehicle_id=vid, status="Completed", service_type="Brakes",
         service_date=date(2026, 1, 1), next_service_date=date(2026, 9, 1))
    seed(models.MaintenanceRecord, vehicle_id=vid, status="Completed", service_type="Brakes",
         service_date=date(2026, 9, 15), next_service_date=date(2027, 9, 15))          # newer, done: not due
    seed(models.MaintenanceRecord, vehicle_id=vid, status="Completed", service_type="Tyres",
         service_date=date(2025, 4, 1), next_service_date=date(2026, 10, 1))
    seed(models.MaintenanceRecord, vehicle_id=vid, status="Scheduled", service_type="Tyres",
         service_date=date(2026, 10, 8))                                               # already booked: not due
    assert operator.get("/api/maintenance/upcoming").json["due"] == []


def test_inactive_vehicles_are_never_due(operator, seed):
    vid = seed(models.Vehicle, status="Inactive")
    seed(models.MaintenanceRecord, vehicle_id=vid, status="Completed", next_service_date=date(2026, 10, 1))
    assert operator.get("/api/maintenance/upcoming").json["due"] == []


def test_upcoming_rejects_a_bad_days_value(operator):
    assert operator.get("/api/maintenance/upcoming?days=x").status_code == 400
    assert operator.get("/api/maintenance/upcoming?days=-1").status_code == 400


# --- concurrency (8.8) ----------------------------------------------------------------------------------------

def test_starting_a_trip_and_starting_a_service_on_one_vehicle_cannot_both_win(world, seed):
    for n in range(3):  # repeat: a missing lock would pass most of the time, not always
        vid = seed(models.Vehicle, registration_no=f"RACE {n}00")
        did = seed(models.Driver, license_no=f"10555555{n}0")
        seed(models.VehicleAssignment, vehicle_id=vid, driver_id=did)
        tid = seed(models.Trip, vehicle_id=vid, driver_id=did, status="Planned",
                   start_date=date(2026, 10, 5), end_date=date(2026, 10, 6))
        mid = seed(models.MaintenanceRecord, vehicle_id=vid)
        manager, operator = Api(world.test_client()), Api(world.test_client())
        assert manager.login(USERS["Fleet Manager"]).status_code == 200
        assert operator.login(USERS["Operator"]).status_code == 200
        barrier, codes = threading.Barrier(2), {}

        def trip_start():
            barrier.wait()
            codes["trip"] = manager.put(f"/api/trips/{tid}", json={"status": "In Progress"}).status_code

        def service_start():
            barrier.wait()
            codes["service"] = start(operator, mid).status_code

        threads = [threading.Thread(target=trip_start), threading.Thread(target=service_start)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(codes.values()) == [200, 422], codes
