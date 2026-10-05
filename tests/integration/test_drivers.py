from datetime import date

from app import models

NEW = {"name": "Ali Al-Harbi", "phone": "0551234567", "license_no": "1099999999", "license_expiry": "2027-06-30"}


def _drivers(seed):
    ok = seed(models.Driver, name="Far Away", license_no="1000000001", license_expiry=date(2027, 12, 31))
    soon = seed(models.Driver, name="Soon Expiring", license_no="1000000002", license_expiry=date(2026, 10, 20))
    edge = seed(models.Driver, name="Day Thirty", license_no="1000000003", license_expiry=date(2026, 11, 4))
    past = seed(models.Driver, name="Expired", license_no="1000000004", license_expiry=date(2026, 9, 1))
    gone = seed(models.Driver, name="Left Company", license_no="1000000005", status="Inactive")
    return ok, soon, edge, past, gone


def test_operator_cannot_list_drivers_but_can_use_lookup(operator, seed):
    _drivers(seed)
    assert operator.get("/api/drivers").status_code == 403
    r = operator.get("/api/drivers/lookup")
    assert r.status_code == 200
    names = [d["name"] for d in r.json["items"]]
    assert "Left Company" not in names and len(names) == 4           # active drivers only
    assert set(r.json["items"][0]) == {"id", "name"}                # no licence data for Operators


def test_lookup_needs_a_session(anon):
    assert anon.get("/api/drivers/lookup").status_code == 401


def test_manager_sees_licence_flags(manager, seed):
    _drivers(seed)
    by_name = {d["name"]: d for d in manager.get("/api/drivers").json["items"]}
    assert not by_name["Far Away"]["licence_warning"]
    assert by_name["Soon Expiring"]["licence_warning"]
    assert by_name["Day Thirty"]["licence_warning"]                  # exactly 30 days out still warns
    assert not by_name["Expired"]["licence_warning"] and by_name["Expired"]["licence_expired"]


def test_licence_warning_filter_is_the_thirty_day_query(manager, seed):
    _drivers(seed)
    names = {d["name"] for d in manager.get("/api/drivers?licence_warning=1").json["items"]}
    assert names == {"Soon Expiring", "Day Thirty"}


def test_licence_warning_filter_skips_inactive_drivers(manager, seed):
    seed(models.Driver, license_no="1000000009", license_expiry=date(2026, 10, 20), status="Inactive")
    assert manager.get("/api/drivers?licence_warning=1").json["total"] == 0


def test_search_and_status_filter(admin, seed):
    _drivers(seed)
    assert admin.get("/api/drivers?q=soon").json["total"] == 1
    assert admin.get("/api/drivers?q=100000000").json["total"] == 5   # matches licence numbers too
    assert admin.get("/api/drivers?status=Inactive").json["total"] == 1
    assert admin.get("/api/drivers?status=Gone").status_code == 400


def test_only_admin_writes_drivers(manager, operator):
    assert manager.post("/api/drivers", json=NEW).status_code == 403
    assert operator.post("/api/drivers", json=NEW).status_code == 403


def test_admin_creates_a_driver(admin):
    r = admin.post("/api/drivers", json=NEW)
    assert r.status_code == 201 and r.json["status"] == "Active" and not r.json["licence_warning"]


def test_phone_is_optional(admin):
    body = {k: v for k, v in NEW.items() if k != "phone"}
    r = admin.post("/api/drivers", json=body)
    assert r.status_code == 201 and r.json["phone"] is None


def test_duplicate_licence_is_409_with_the_field(admin):
    admin.post("/api/drivers", json=NEW)
    r = admin.post("/api/drivers", json={**NEW, "name": "Someone Else"})
    assert r.status_code == 409 and r.json["field"] == "license_no"


def test_validation_errors_name_the_field(admin):
    for bad, field in [({"license_expiry": "30/06/2027"}, "license_expiry"), ({"phone": "abc"}, "phone"),
                       ({"name": ""}, "name"), ({"status": "Retired"}, "status")]:
        r = admin.post("/api/drivers", json={**NEW, **bad})
        assert r.status_code == 422 and r.json["field"] == field, bad


def test_expired_licence_can_be_recorded(admin):
    r = admin.post("/api/drivers", json={**NEW, "license_expiry": "2026-01-01"})
    assert r.status_code == 201 and r.json["licence_expired"]


def test_update_renews_a_licence(admin, seed):
    did = seed(models.Driver, license_expiry=date(2026, 9, 1))
    r = admin.put(f"/api/drivers/{did}", json={"license_expiry": "2028-01-01"})
    assert r.status_code == 200 and not r.json["licence_expired"]


def test_update_missing_driver_is_404(admin):
    assert admin.put("/api/drivers/999", json={"name": "X"}).status_code == 404


def test_deactivate_keeps_the_row_and_is_repeatable(admin, seed):
    did = seed(models.Driver)
    assert admin.delete(f"/api/drivers/{did}").json["status"] == "Inactive"
    assert admin.delete(f"/api/drivers/{did}").status_code == 200
    assert admin.get("/api/drivers").json["total"] == 1


def test_deactivation_refused_with_an_open_trip_by_delete_and_by_put(admin, seed):
    vid, did = seed(models.Vehicle), seed(models.Driver)
    seed(models.Trip, vehicle_id=vid, driver_id=did, status="In Progress")
    assert admin.delete(f"/api/drivers/{did}").status_code == 422
    r = admin.put(f"/api/drivers/{did}", json={"status": "Inactive"})
    assert r.status_code == 422 and "trip" in r.json["error"]


def test_put_can_reactivate_a_driver(admin, seed):
    did = seed(models.Driver, status="Inactive")
    assert admin.put(f"/api/drivers/{did}", json={"status": "Active"}).json["status"] == "Active"
