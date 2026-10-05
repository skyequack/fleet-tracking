from datetime import date

from app import models

NEW = {"registration_no": "xyz 4321", "type": "Truck", "make": "Volvo", "model": "FH", "year": 2021,
       "fuel_type": "Diesel", "odometer": 5000.5}


def test_operator_and_manager_cannot_create_vehicles(operator, manager):
    assert operator.post("/api/vehicles", json=NEW).status_code == 403
    assert manager.post("/api/vehicles", json=NEW).status_code == 403


def test_admin_creates_a_vehicle(admin):
    r = admin.post("/api/vehicles", json=NEW)
    assert r.status_code == 201
    assert r.json["registration_no"] == "XYZ 4321"           # normalised
    assert r.json["status"] == "Active" and r.json["odometer"] == 5000.5


def test_duplicate_registration_is_409_with_the_field(admin):
    assert admin.post("/api/vehicles", json=NEW).status_code == 201
    r = admin.post("/api/vehicles", json={**NEW, "registration_no": "XYZ 4321"})
    assert r.status_code == 409 and r.json["field"] == "registration_no"
    assert admin.post("/api/vehicles", json={**NEW, "registration_no": "xyz 4321"}).status_code == 409


def test_duplicate_does_not_poison_the_next_request(admin):
    admin.post("/api/vehicles", json=NEW)
    admin.post("/api/vehicles", json=NEW)
    assert admin.post("/api/vehicles", json={**NEW, "registration_no": "OTH 1111"}).status_code == 201


def test_validation_errors_name_the_field(admin):
    for bad, field in [({"type": "Bus"}, "type"), ({"year": 1900}, "year"), ({"year": "2020"}, "year"),
                       ({"odometer": -1}, "odometer"), ({"make": ""}, "make"), ({"fuel_type": "Gas"}, "fuel_type")]:
        r = admin.post("/api/vehicles", json={**NEW, **bad})
        assert r.status_code == 422 and r.json["field"] == field, bad


def test_status_and_odometer_cannot_be_set_through_update(admin, seed):
    vid = seed(models.Vehicle)
    r = admin.put(f"/api/vehicles/{vid}", json={"status": "Inactive"})
    assert r.status_code == 400 and r.json["field"] == "status"
    r = admin.put(f"/api/vehicles/{vid}", json={"odometer": 5})
    assert r.status_code == 400 and r.json["field"] == "odometer"


def test_status_cannot_be_set_on_create(admin):
    r = admin.post("/api/vehicles", json={**NEW, "status": "Under Maintenance"})
    assert r.status_code == 400 and r.json["field"] == "status"


def test_update_changes_only_the_given_fields(admin, seed):
    vid = seed(models.Vehicle)
    r = admin.put(f"/api/vehicles/{vid}", json={"make": "Hyundai"})
    assert r.status_code == 200 and r.json["make"] == "Hyundai" and r.json["model"] == "HiAce"


def test_update_to_a_taken_registration_is_409(admin, seed):
    seed(models.Vehicle, registration_no="AAA 1111")
    vid = seed(models.Vehicle, registration_no="BBB 2222")
    assert admin.put(f"/api/vehicles/{vid}", json={"registration_no": "AAA 1111"}).status_code == 409


def test_update_missing_vehicle_is_404(admin):
    assert admin.put("/api/vehicles/999", json={"make": "X"}).status_code == 404


def test_all_roles_can_list_and_filter(operator, seed):
    seed(models.Vehicle, registration_no="AAA 1111", type="Van", make="Toyota")
    seed(models.Vehicle, registration_no="BBB 2222", type="Truck", make="Volvo")
    seed(models.Vehicle, registration_no="CCC 3333", type="Truck", make="Hino", status="Inactive")
    r = operator.get("/api/vehicles")
    assert r.status_code == 200 and r.json["total"] == 3
    assert set(r.json) == {"items", "page", "per_page", "total"}
    assert operator.get("/api/vehicles?type=Truck").json["total"] == 2
    assert operator.get("/api/vehicles?type=Truck&status=Inactive").json["total"] == 1
    assert [v["registration_no"] for v in operator.get("/api/vehicles?q=volv").json["items"]] == ["BBB 2222"]
    assert operator.get("/api/vehicles?q=%25").json["total"] == 0           # % is taken literally
    assert operator.get("/api/vehicles?type=Bus").status_code == 400


def test_pagination(operator, seed):
    for i in range(5):
        seed(models.Vehicle, registration_no=f"AAA {1000 + i}")
    r = operator.get("/api/vehicles?page=2&per_page=2").json
    assert (r["page"], r["per_page"], r["total"], len(r["items"])) == (2, 2, 5, 2)
    assert operator.get("/api/vehicles?per_page=9999").json["per_page"] == 100
    assert operator.get("/api/vehicles?page=0").status_code == 400
    assert operator.get("/api/vehicles?page=x").status_code == 400


def test_vehicle_list_needs_a_session(anon):
    assert anon.get("/api/vehicles").status_code == 401


def test_delete_deactivates_and_keeps_the_row(admin, seed):
    vid = seed(models.Vehicle)
    r = admin.delete(f"/api/vehicles/{vid}")
    assert r.status_code == 200 and r.json["status"] == "Inactive"
    assert admin.get("/api/vehicles").json["total"] == 1
    assert admin.delete(f"/api/vehicles/{vid}").status_code == 200       # repeating is harmless


def test_operator_cannot_deactivate(operator, seed):
    assert operator.delete(f"/api/vehicles/{seed(models.Vehicle)}").status_code == 403


def test_delete_refused_while_a_trip_is_open(admin, seed):
    vid, did = seed(models.Vehicle), seed(models.Driver)
    seed(models.Trip, vehicle_id=vid, driver_id=did, status="Planned")
    r = admin.delete(f"/api/vehicles/{vid}")
    assert r.status_code == 422 and "trip" in r.json["error"]


def test_delete_allowed_when_trips_are_finished(admin, seed):
    vid, did = seed(models.Vehicle), seed(models.Driver)
    seed(models.Trip, vehicle_id=vid, driver_id=did, status="Completed")
    seed(models.Trip, vehicle_id=vid, driver_id=did, status="Cancelled", start_date=date(2026, 11, 1),
         end_date=date(2026, 11, 2))
    assert admin.delete(f"/api/vehicles/{vid}").status_code == 200


def test_delete_refused_while_a_service_is_open(admin, seed):
    vid = seed(models.Vehicle)
    seed(models.MaintenanceRecord, vehicle_id=vid, status="Scheduled")
    r = admin.delete(f"/api/vehicles/{vid}")
    assert r.status_code == 422 and "service" in r.json["error"]


def test_reactivate_returns_to_active(admin, seed):
    vid = seed(models.Vehicle, status="Inactive")
    r = admin.post(f"/api/vehicles/{vid}/reactivate")
    assert r.status_code == 200 and r.json["status"] == "Active"


def test_reactivate_with_a_running_service_is_under_maintenance(admin, seed):
    vid = seed(models.Vehicle, status="Inactive")
    seed(models.MaintenanceRecord, vehicle_id=vid, status="In Progress")
    assert admin.post(f"/api/vehicles/{vid}/reactivate").json["status"] == "Under Maintenance"


def test_reactivate_does_not_touch_a_vehicle_under_maintenance(admin, seed):
    vid = seed(models.Vehicle, status="Under Maintenance")
    assert admin.post(f"/api/vehicles/{vid}/reactivate").json["status"] == "Under Maintenance"


def test_reactivate_is_admin_only(manager, seed):
    assert manager.post(f"/api/vehicles/{seed(models.Vehicle, status='Inactive')}/reactivate").status_code == 403
