from datetime import date

from app import models


def _pair(seed):
    return seed(models.Vehicle), seed(models.Driver)


def test_only_admin_and_manager_use_assignments(operator, seed):
    vid, did = _pair(seed)
    body = {"vehicle_id": vid, "driver_id": did, "start_date": "2026-10-01"}
    assert operator.post("/api/assignments", json=body).status_code == 403
    assert operator.get("/api/assignments").status_code == 403


def test_manager_assigns_a_driver_and_lists_it(manager, seed):
    vid, did = _pair(seed)
    r = manager.post("/api/assignments", json={"vehicle_id": vid, "driver_id": did, "start_date": "2026-10-01"})
    assert r.status_code == 201 and r.json["end_date"] is None
    assert manager.get(f"/api/assignments?vehicle_id={vid}").json["total"] == 1
    assert manager.get(f"/api/assignments?driver_id={did + 1}").json["total"] == 0


def test_overlapping_assignment_on_the_same_vehicle_is_422(admin, seed):
    vid, did = _pair(seed)
    other = seed(models.Driver, license_no="1022222222")
    admin.post("/api/assignments", json={"vehicle_id": vid, "driver_id": did, "start_date": "2026-01-01"})
    r = admin.post("/api/assignments", json={"vehicle_id": vid, "driver_id": other, "start_date": "2026-06-01"})
    assert r.status_code == 422 and "already has assignment" in r.json["error"]


def test_a_new_driver_can_start_the_day_after_the_old_one_ends(admin, seed):
    vid, did = _pair(seed)
    other = seed(models.Driver, license_no="1022222222")
    first = admin.post("/api/assignments", json={"vehicle_id": vid, "driver_id": did,
                                                 "start_date": "2026-01-01"}).json["assignment_id"]
    assert admin.put(f"/api/assignments/{first}", json={"end_date": "2026-06-30"}).status_code == 200
    r = admin.post("/api/assignments", json={"vehicle_id": vid, "driver_id": other, "start_date": "2026-07-01"})
    assert r.status_code == 201


def test_validation_and_missing_references(admin, seed):
    vid, did = _pair(seed)
    r = admin.post("/api/assignments", json={"vehicle_id": vid, "driver_id": did, "start_date": "2026-10-05",
                                             "end_date": "2026-10-01"})
    assert r.status_code == 422 and r.json["field"] == "end_date"
    r = admin.post("/api/assignments", json={"vehicle_id": 999, "driver_id": did, "start_date": "2026-10-05"})
    assert r.status_code == 422 and r.json["field"] == "vehicle_id"
    r = admin.post("/api/assignments", json={"vehicle_id": vid, "driver_id": 999, "start_date": "2026-10-05"})
    assert r.status_code == 422 and r.json["field"] == "driver_id"


def test_cannot_assign_an_inactive_driver_or_vehicle(admin, seed):
    vid, gone = seed(models.Vehicle), seed(models.Driver, license_no="1044444444", status="Inactive")
    r = admin.post("/api/assignments", json={"vehicle_id": vid, "driver_id": gone, "start_date": "2026-10-05"})
    assert r.status_code == 422 and r.json["field"] == "driver_id"
    off, did = seed(models.Vehicle, registration_no="OFF 0001", status="Inactive"), seed(models.Driver)
    r = admin.post("/api/assignments", json={"vehicle_id": off, "driver_id": did, "start_date": "2026-10-05"})
    assert r.status_code == 422 and r.json["field"] == "vehicle_id"


def test_end_date_cannot_precede_start_and_missing_is_404(admin, seed):
    vid, did = _pair(seed)
    aid = admin.post("/api/assignments", json={"vehicle_id": vid, "driver_id": did,
                                               "start_date": "2026-10-05"}).json["assignment_id"]
    assert admin.put(f"/api/assignments/{aid}", json={"end_date": "2026-10-01"}).status_code == 422
    assert admin.put("/api/assignments/999", json={"end_date": "2026-10-01"}).status_code == 404
    assert admin.put(f"/api/assignments/{aid}", json={"driver_id": 1}).status_code == 400   # only end_date
