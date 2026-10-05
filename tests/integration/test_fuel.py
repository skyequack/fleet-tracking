from datetime import date

from app import models
from app.extensions import db
from tests.conftest import USERS


def fill(vid, day="2026-10-01", odo=1500, qty=40, **kw):
    return {"vehicle_id": vid, "date": day, "odometer": odo, "quantity": qty, **kw}


def vehicle_odometer(world, vid):
    with world.app_context():
        return float(db.session.get(models.Vehicle, vid).odometer)


def test_operator_enters_a_fill_and_total_cost_is_computed_by_the_backend(operator, seed):
    vid = seed(models.Vehicle, fuel_type="Petrol")
    r = operator.post("/api/fuel", json=fill(vid, qty=33.33))
    assert r.status_code == 201
    assert r.json["price_per_litre"] == 2.18                 # default for Petrol from config
    assert r.json["total_cost"] == 72.66                     # 33.33 x 2.18 = 72.6594
    assert r.json["full_tank"] is True


def test_default_price_follows_the_fuel_type_and_can_be_overridden(admin, seed):
    diesel = seed(models.Vehicle, registration_no="DSL 0001", fuel_type="Diesel")
    assert admin.post("/api/fuel", json=fill(diesel, qty=50)).json["price_per_litre"] == 1.66
    r = admin.post("/api/fuel", json=fill(diesel, "2026-10-02", 1600, 50, price_per_litre=1.9))
    assert r.json["price_per_litre"] == 1.9 and r.json["total_cost"] == 95.0


def test_total_cost_cannot_be_sent_by_the_client(operator, seed):
    vid = seed(models.Vehicle)
    for name in ("total_cost", "created_by"):
        r = operator.post("/api/fuel", json=fill(vid, **{name: 1}))
        assert r.status_code == 400 and r.json["field"] == name


def test_roles(manager, operator, anon, seed):
    vid = seed(models.Vehicle)
    assert manager.post("/api/fuel", json=fill(vid)).status_code == 403     # Managers read, they do not enter
    assert manager.get("/api/fuel").status_code == 200
    assert anon.post("/api/fuel", json=fill(vid)).status_code == 401


def test_saving_a_fill_raises_the_vehicle_odometer(operator, seed, world):
    vid = seed(models.Vehicle, odometer=1000)
    operator.post("/api/fuel", json=fill(vid, odo=1500))
    assert vehicle_odometer(world, vid) == 1500


def test_tc06_odometer_lower_than_the_previous_reading_is_rejected(operator, seed, world):
    vid = seed(models.Vehicle, odometer=1500)
    seed(models.FuelRecord, vehicle_id=vid, date=date(2026, 9, 20), odometer=1500)
    r = operator.post("/api/fuel", json=fill(vid, "2026-10-01", odo=1400))
    assert r.status_code == 422 and r.json["field"] == "odometer" and "below the previous" in r.json["error"]
    assert operator.get("/api/fuel").json["total"] == 0                       # nothing was saved
    assert vehicle_odometer(world, vid) == 1500


def test_a_back_dated_fill_is_bracketed_on_both_sides_and_never_lowers_the_master(operator, seed, world):
    vid = seed(models.Vehicle, odometer=2000)
    seed(models.FuelRecord, vehicle_id=vid, date=date(2026, 9, 1), odometer=1000)
    seed(models.FuelRecord, vehicle_id=vid, date=date(2026, 9, 20), odometer=2000)
    below = operator.post("/api/fuel", json=fill(vid, "2026-09-10", odo=900))
    above = operator.post("/api/fuel", json=fill(vid, "2026-09-10", odo=2100))
    assert below.status_code == above.status_code == 422
    assert "above the next" in above.json["error"]
    ok = operator.post("/api/fuel", json=fill(vid, "2026-09-10", odo=1500))
    assert ok.status_code == 201
    assert vehicle_odometer(world, vid) == 2000                              # GREATEST(current, new)


def test_same_day_fills_count_as_earlier_ones(operator, seed):
    vid = seed(models.Vehicle)
    assert operator.post("/api/fuel", json=fill(vid, "2026-10-01", 1500)).status_code == 201
    assert operator.post("/api/fuel", json=fill(vid, "2026-10-01", 1500)).status_code == 201   # equal is allowed
    assert operator.post("/api/fuel", json=fill(vid, "2026-10-01", 1499)).status_code == 422


def test_other_vehicles_do_not_affect_the_bracket(operator, seed):
    a, b = seed(models.Vehicle), seed(models.Vehicle, registration_no="DEF 5678")
    operator.post("/api/fuel", json=fill(a, odo=9000))
    assert operator.post("/api/fuel", json=fill(b, odo=100)).status_code == 201


def test_validation(operator, seed):
    vid = seed(models.Vehicle)
    for bad, field in [({"quantity": 0}, "quantity"), ({"quantity": -5}, "quantity"), ({"odometer": -1}, "odometer"),
                       ({"date": "2026-10-06"}, "date"), ({"date": "01-10-2026"}, "date"),
                       ({"price_per_litre": 0}, "price_per_litre"), ({"full_tank": "yes"}, "full_tank"),
                       ({"vehicle_id": 999}, "vehicle_id")]:
        r = operator.post("/api/fuel", json=fill(vid, **bad))
        assert r.status_code == 422 and r.json["field"] == field, bad


def test_an_inactive_vehicle_cannot_be_refuelled(operator, seed):
    vid = seed(models.Vehicle, status="Inactive")
    assert operator.post("/api/fuel", json=fill(vid)).status_code == 422


def test_efficiency_between_consecutive_full_tank_fills(operator, seed):
    vid = seed(models.Vehicle, odometer=0)
    first = operator.post("/api/fuel", json=fill(vid, "2026-09-01", 1000, 50)).json
    second = operator.post("/api/fuel", json=fill(vid, "2026-09-10", 1400, 40)).json
    assert first["km_per_l"] is None                      # nothing to compare the first fill with
    assert second["km_per_l"] == 10.0                     # 400 km / 40 L


def test_partial_fills_are_counted_in_the_next_full_tank_efficiency(operator, seed):
    vid = seed(models.Vehicle, odometer=0)
    operator.post("/api/fuel", json=fill(vid, "2026-09-01", 1000, 50))
    partial = operator.post("/api/fuel", json=fill(vid, "2026-09-05", 1300, 30, full_tank=False)).json
    full = operator.post("/api/fuel", json=fill(vid, "2026-09-09", 1500, 20)).json
    assert partial["km_per_l"] is None                    # efficiency is defined between full tanks only
    assert full["km_per_l"] == 10.0                       # 500 km / (30 + 20) L, not 500 / 20


def test_efficiency_is_per_vehicle_and_follows_back_dated_entries(operator, seed):
    a, b = seed(models.Vehicle, odometer=0), seed(models.Vehicle, registration_no="DEF 5678", odometer=0)
    operator.post("/api/fuel", json=fill(a, "2026-09-01", 1000, 50))
    operator.post("/api/fuel", json=fill(b, "2026-09-02", 5000, 50))
    twenty = operator.post("/api/fuel", json=fill(a, "2026-09-20", 1600, 60)).json
    assert twenty["km_per_l"] == 10.0                                                  # 600 km / 60 L
    operator.post("/api/fuel", json=fill(a, "2026-09-10", 1200, 20))                   # back-dated full tank
    rows = {r["date"]: r["km_per_l"] for r in operator.get(f"/api/fuel?vehicle_id={a}").json["items"]}
    # the new fill sits between them: 200 km / 20 L, and 20 Sep now measures 400 km / 60 L
    assert rows == {"2026-09-20": 6.67, "2026-09-10": 10.0, "2026-09-01": None}


def test_listing_filters_and_envelope(admin, seed):
    a, b = seed(models.Vehicle), seed(models.Vehicle, registration_no="DEF 5678")
    seed(models.FuelRecord, vehicle_id=a, date=date(2026, 8, 1), odometer=100)
    seed(models.FuelRecord, vehicle_id=a, date=date(2026, 9, 1), odometer=200)
    seed(models.FuelRecord, vehicle_id=b, date=date(2026, 9, 2), odometer=300)
    r = admin.get("/api/fuel").json
    assert set(r) == {"items", "page", "per_page", "total"} and r["total"] == 3
    assert r["items"][0]["date"] == "2026-09-02" and r["items"][0]["registration_no"] == "DEF 5678"
    assert admin.get(f"/api/fuel?vehicle_id={a}").json["total"] == 2
    assert admin.get("/api/fuel?from=2026-09-01&to=2026-09-01").json["total"] == 1
    assert admin.get("/api/fuel?per_page=1&page=2").json["items"][0]["date"] == "2026-09-01"
    assert admin.get("/api/fuel?vehicle_id=x").status_code == 400
    assert admin.get("/api/fuel?from=soon").status_code == 400


def test_operator_sees_only_their_own_entries_but_a_manager_sees_all(operator, manager, admin, seed, world):
    vid = seed(models.Vehicle)
    seed(models.FuelRecord, vehicle_id=vid, date=date(2026, 8, 1), odometer=100)          # seeded: no creator
    admin.post("/api/fuel", json=fill(vid, "2026-09-01", 200))
    operator.post("/api/fuel", json=fill(vid, "2026-09-02", 300))
    mine = operator.get("/api/fuel").json
    assert mine["total"] == 1 and mine["items"][0]["odometer"] == 300
    assert manager.get("/api/fuel").json["total"] == 3
    assert admin.get("/api/fuel").json["total"] == 3


def test_fill_records_who_entered_it(operator, seed, world):
    vid = seed(models.Vehicle)
    fid = operator.post("/api/fuel", json=fill(vid)).json["fuel_id"]
    with world.app_context():
        uid = models.User.query.filter_by(email=USERS["Operator"]).one().user_id
        assert db.session.get(models.FuelRecord, fid).created_by == uid
