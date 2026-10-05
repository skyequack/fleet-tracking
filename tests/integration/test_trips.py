import threading
from datetime import date

from app import models
from app.extensions import db
from tests.conftest import USERS, Api


def fleet(seed, **driver):
    """One vehicle and its assigned driver (assignment open since 1 Jan 2026)."""
    vid, did = seed(models.Vehicle), seed(models.Driver, **driver)
    seed(models.VehicleAssignment, vehicle_id=vid, driver_id=did)
    return vid, did


def body(vid, did, start="2026-10-10", end="2026-10-11", **kw):
    return {"vehicle_id": vid, "driver_id": did, "origin": "Riyadh", "destination": "Jeddah", "distance": 120.5,
            "start_date": start, "end_date": end, **kw}


def test_every_role_can_create_a_trip(operator, manager, admin, seed):
    vid, did = fleet(seed)
    for i, api in enumerate((operator, manager, admin)):
        day = 10 + 2 * i
        r = api.post("/api/trips", json=body(vid, did, f"2026-10-{day}", f"2026-10-{day + 1}"))
        assert r.status_code == 201 and r.json["status"] == "Planned"
    assert r.json["registration_no"] == "ABC 1234" and r.json["driver_name"] == "Test Driver"


def test_trip_records_who_created_it(operator, seed, world):
    vid, did = fleet(seed)
    tid = operator.post("/api/trips", json=body(vid, did)).json["trip_id"]
    with world.app_context():
        row = db.session.get(models.Trip, tid)
        assert row.created_by == models.User.query.filter_by(email=USERS["Operator"]).one().user_id


def test_trip_needs_a_session(anon, seed):
    vid, did = fleet(seed)
    assert anon.post("/api/trips", json=body(vid, did)).status_code == 401


def test_status_cannot_be_chosen_on_create(operator, seed):
    vid, did = fleet(seed)
    r = operator.post("/api/trips", json=body(vid, did, status="Completed"))
    assert r.status_code == 400 and r.json["field"] == "status"


def test_field_validation(operator, seed):
    vid, did = fleet(seed)
    for bad, field in [({"distance": -1}, "distance"), ({"origin": ""}, "origin"), ({"start_date": "10/10/2026"},
                       "start_date"), ({"vehicle_id": "x"}, "vehicle_id"), ({"end_date": "2026-10-09"}, "end_date")]:
        r = operator.post("/api/trips", json=body(vid, did, **bad))
        assert r.status_code == 422 and r.json["field"] == field, bad


def test_unknown_vehicle_or_driver_is_422_not_500(operator, seed):
    vid, did = fleet(seed)
    assert operator.post("/api/trips", json=body(999, did)).json["field"] == "vehicle_id"
    assert operator.post("/api/trips", json=body(vid, 999)).json["field"] == "driver_id"


# --- the six checks, end to end -------------------------------------------------------------------------------

def test_check1_inactive_vehicle(operator, seed):
    vid, did = seed(models.Vehicle, status="Inactive"), seed(models.Driver)
    seed(models.VehicleAssignment, vehicle_id=vid, driver_id=did)
    r = operator.post("/api/trips", json=body(vid, did))
    assert r.status_code == 422 and r.json["field"] == "vehicle_id" and "inactive" in r.json["error"]


def test_check1_future_trip_on_a_vehicle_in_service_is_allowed_unless_the_service_is_inside_it(operator, seed):
    vid, did = seed(models.Vehicle, status="Under Maintenance"), seed(models.Driver)
    seed(models.VehicleAssignment, vehicle_id=vid, driver_id=did)
    seed(models.MaintenanceRecord, vehicle_id=vid, status="In Progress", service_date=date(2026, 10, 3))
    assert operator.post("/api/trips", json=body(vid, did, "2026-10-10", "2026-10-11")).status_code == 201
    seed(models.MaintenanceRecord, vehicle_id=vid, status="Scheduled", service_date=date(2026, 10, 21))
    r = operator.post("/api/trips", json=body(vid, did, "2026-10-20", "2026-10-22"))
    assert r.status_code == 422 and "service" in r.json["error"]
    assert operator.post("/api/trips", json=body(vid, did, "2026-10-05", "2026-10-06")).status_code == 422  # today


def test_check2_inactive_driver(operator, seed):
    vid, did = fleet(seed, status="Inactive")
    r = operator.post("/api/trips", json=body(vid, did))
    assert r.status_code == 422 and r.json["error"] == "Driver is not active"


def test_check3_expired_licence_is_rejected_with_a_reason(operator, seed):
    vid, did = fleet(seed, license_expiry=date(2026, 10, 10))
    r = operator.post("/api/trips", json=body(vid, did, "2026-10-09", "2026-10-11"))
    assert r.status_code == 422 and "licence expires on 2026-10-10" in r.json["error"]
    assert operator.post("/api/trips", json=body(vid, did, "2026-10-09", "2026-10-10")).status_code == 201


def test_check4_vehicle_overlap_is_inclusive_and_names_the_trip(operator, seed):
    vid, did = fleet(seed)
    first = operator.post("/api/trips", json=body(vid, did, "2026-10-10", "2026-10-12")).json["trip_id"]
    r = operator.post("/api/trips", json=body(vid, did, "2026-10-12", "2026-10-13"))   # shares the 12th
    assert r.status_code == 422 and f"#{first}" in r.json["error"]
    assert operator.post("/api/trips", json=body(vid, did, "2026-10-13", "2026-10-14")).status_code == 201


def test_check4_cancelled_trips_do_not_block(operator, manager, seed):
    vid, did = fleet(seed)
    first = operator.post("/api/trips", json=body(vid, did)).json["trip_id"]
    assert manager.put(f"/api/trips/{first}", json={"status": "Cancelled"}).status_code == 200
    assert operator.post("/api/trips", json=body(vid, did)).status_code == 201


def test_check5_driver_cannot_be_on_two_vehicles_at_once(operator, seed):
    v1, did = fleet(seed)
    v2 = seed(models.Vehicle, registration_no="DEF 5678")
    seed(models.VehicleAssignment, vehicle_id=v2, driver_id=did)   # one driver assigned to both vehicles
    assert operator.post("/api/trips", json=body(v1, did)).status_code == 201
    r = operator.post("/api/trips", json=body(v2, did))
    assert r.status_code == 422 and r.json["error"].startswith("Driver already has trip")


def test_check6_driver_must_be_assigned_to_the_vehicle_on_the_start_date(operator, seed):
    vid, did = seed(models.Vehicle), seed(models.Driver)
    r = operator.post("/api/trips", json=body(vid, did))                      # never assigned
    assert r.status_code == 422 and "not assigned" in r.json["error"]
    seed(models.VehicleAssignment, vehicle_id=vid, driver_id=did, start_date=date(2026, 6, 1),
         end_date=date(2026, 10, 9))                                          # ended the day before the trip
    assert operator.post("/api/trips", json=body(vid, did, "2026-10-10", "2026-10-11")).status_code == 422
    assert operator.post("/api/trips", json=body(vid, did, "2026-10-09", "2026-10-11")).status_code == 201


# --- listing --------------------------------------------------------------------------------------------------

def test_listing_filters(operator, seed):
    vid, did = fleet(seed)
    other = seed(models.Vehicle, registration_no="DEF 5678")
    seed(models.Trip, vehicle_id=vid, driver_id=did, status="Completed", start_date=date(2026, 9, 1),
         end_date=date(2026, 9, 2))
    seed(models.Trip, vehicle_id=other, driver_id=did, status="Planned", start_date=date(2026, 10, 10),
         end_date=date(2026, 10, 12))
    assert operator.get("/api/trips").json["total"] == 2
    assert operator.get("/api/trips?status=Planned").json["total"] == 1
    assert operator.get(f"/api/trips?vehicle_id={vid}").json["total"] == 1
    assert operator.get("/api/trips?from=2026-10-12").json["total"] == 1       # overlaps the window
    assert operator.get("/api/trips?from=2026-10-13").json["total"] == 0
    assert operator.get("/api/trips?to=2026-09-01").json["total"] == 1
    assert operator.get("/api/trips").json["items"][0]["start_date"] == "2026-10-10"   # newest first
    assert operator.get("/api/trips?status=Bogus").status_code == 400
    assert operator.get("/api/trips?from=yesterday").status_code == 400
    assert operator.get("/api/trips?vehicle_id=x").status_code == 400


# --- editing and status ---------------------------------------------------------------------------------------

def test_operator_cannot_edit_trips(operator, seed):
    vid, did = fleet(seed)
    tid = operator.post("/api/trips", json=body(vid, did)).json["trip_id"]
    assert operator.put(f"/api/trips/{tid}", json={"status": "Cancelled"}).status_code == 403


def test_manager_walks_a_trip_through_its_life(operator, manager, seed):
    vid, did = fleet(seed)
    tid = operator.post("/api/trips", json=body(vid, did)).json["trip_id"]
    assert manager.put(f"/api/trips/{tid}", json={"status": "In Progress"}).json["status"] == "In Progress"
    assert manager.put(f"/api/trips/{tid}", json={"status": "Completed"}).json["status"] == "Completed"


def test_invalid_transitions_are_422(operator, manager, seed):
    vid, did = fleet(seed)
    tid = operator.post("/api/trips", json=body(vid, did)).json["trip_id"]
    r = manager.put(f"/api/trips/{tid}", json={"status": "Completed"})          # skipping In Progress
    assert r.status_code == 422 and r.json["field"] == "status"
    manager.put(f"/api/trips/{tid}", json={"status": "Cancelled"})
    assert manager.put(f"/api/trips/{tid}", json={"status": "Planned"}).status_code == 422


def test_dates_change_only_while_planned_and_checks_rerun(operator, manager, seed):
    vid, did = fleet(seed)
    a = operator.post("/api/trips", json=body(vid, did, "2026-10-10", "2026-10-11")).json["trip_id"]
    operator.post("/api/trips", json=body(vid, did, "2026-10-14", "2026-10-15"))
    assert manager.put(f"/api/trips/{a}", json={"start_date": "2026-10-08"}).status_code == 200
    r = manager.put(f"/api/trips/{a}", json={"end_date": "2026-10-14"})         # runs into the second trip
    assert r.status_code == 422 and "Vehicle already has trip" in r.json["error"]
    manager.put(f"/api/trips/{a}", json={"status": "In Progress"})
    r = manager.put(f"/api/trips/{a}", json={"start_date": "2026-10-09"})
    assert r.status_code == 422 and "can no longer be edited" in r.json["error"]


def test_a_trip_does_not_clash_with_itself_when_edited(operator, manager, seed):
    vid, did = fleet(seed)
    tid = operator.post("/api/trips", json=body(vid, did)).json["trip_id"]
    assert manager.put(f"/api/trips/{tid}", json={"end_date": "2026-10-12", "distance": 99}).status_code == 200


def test_changing_the_driver_reruns_the_checks(operator, manager, seed):
    vid, did = fleet(seed)
    stranger = seed(models.Driver, license_no="1033333333")                      # not assigned to the vehicle
    tid = operator.post("/api/trips", json=body(vid, did)).json["trip_id"]
    r = manager.put(f"/api/trips/{tid}", json={"driver_id": stranger})
    assert r.status_code == 422 and "not assigned" in r.json["error"]


def test_a_trip_cannot_start_on_a_vehicle_that_went_into_service(operator, manager, seed, world):
    vid, did = fleet(seed)
    tid = operator.post("/api/trips", json=body(vid, did)).json["trip_id"]
    from app.extensions import db
    with world.app_context():
        models.Vehicle.query.filter_by(vehicle_id=vid).update({"status": "Under Maintenance"})
        db.session.commit()
    r = manager.put(f"/api/trips/{tid}", json={"status": "In Progress"})
    assert r.status_code == 422 and "Under Maintenance" in r.json["error"]


def test_put_on_a_missing_trip_is_404(manager):
    assert manager.put("/api/trips/999", json={"status": "Cancelled"}).status_code == 404


# --- vehicle and driver deactivation now see real trips -------------------------------------------------------

def test_deactivation_is_refused_for_a_vehicle_and_driver_with_a_planned_trip(operator, admin, seed):
    vid, did = fleet(seed)
    operator.post("/api/trips", json=body(vid, did))
    assert admin.delete(f"/api/vehicles/{vid}").status_code == 422
    assert admin.delete(f"/api/drivers/{did}").status_code == 422


# --- concurrency (ARCHITECTURE.md 8.8, R3) --------------------------------------------------------------------

def _race(world, requests):
    """Fire all requests at once from separate sessions; return their status codes."""
    apis = []
    for _ in requests:
        api = Api(world.test_client())
        assert api.login(USERS["Operator"]).status_code == 200
        apis.append(api)
    barrier, results = threading.Barrier(len(requests)), [None] * len(requests)

    def run(i):
        barrier.wait()
        results[i] = apis[i].post("/api/trips", json=requests[i]).status_code

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(requests))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_two_simultaneous_overlapping_trips_on_one_vehicle_only_one_wins(world, seed):
    vid, did = fleet(seed)
    for _ in range(3):  # repeat: a missing lock would pass most of the time, not always
        with world.app_context():
            from app.extensions import db
            models.Trip.query.delete()
            db.session.commit()
        codes = _race(world, [body(vid, did, "2026-10-10", "2026-10-12"), body(vid, did, "2026-10-11", "2026-10-13")])
        assert sorted(codes) == [201, 422], codes


def test_two_simultaneous_trips_for_one_driver_on_two_vehicles_only_one_wins(world, seed):
    v1, did = fleet(seed)
    v2 = seed(models.Vehicle, registration_no="DEF 5678")
    seed(models.VehicleAssignment, vehicle_id=v2, driver_id=did)
    codes = _race(world, [body(v1, did), body(v2, did)])
    assert sorted(codes) == [201, 422], codes
