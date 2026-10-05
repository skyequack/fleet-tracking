import time
from datetime import date

import pytest
from sqlalchemy import text

from app import models
from app.extensions import db

WINDOW = "from=2026-09-01&to=2026-09-30"   # 30 days


@pytest.fixture
def september(seed):
    """A small fleet whose September 2026 numbers are worked out by hand in the comments below."""
    a = seed(models.Vehicle, registration_no="VAN 0001", type="Van", status="Active")
    b = seed(models.Vehicle, registration_no="TRK 0001", type="Truck", status="Under Maintenance")
    c = seed(models.Vehicle, registration_no="SED 0001", type="Sedan", status="Inactive")
    d = seed(models.Driver)

    def trip(vid, start, end, distance, status="Completed"):
        seed(models.Trip, vehicle_id=vid, driver_id=d, start_date=start, end_date=end, distance=distance,
             status=status)

    trip(a, date(2026, 9, 1), date(2026, 9, 3), 300)                       # 3 active days
    trip(a, date(2026, 9, 10), date(2026, 9, 10), 100)                     # 1
    trip(a, date(2026, 9, 20), date(2026, 9, 21), 777, status="Planned")   # not counted anywhere
    trip(a, date(2026, 9, 22), date(2026, 9, 22), 888, status="Cancelled")
    trip(a, date(2026, 9, 29), date(2026, 10, 2), 50, status="In Progress")  # distance not counted; 2 days in window
    trip(b, date(2026, 8, 30), date(2026, 9, 2), 400)                      # ends in window: 400 km; 2 days in window
    trip(c, date(2026, 9, 5), date(2026, 9, 5), 999)                       # inactive vehicle: km count, days do not
    seed(models.FuelRecord, vehicle_id=a, date=date(2026, 9, 5), odometer=1100, quantity=40, total_cost=87.20)
    seed(models.FuelRecord, vehicle_id=b, date=date(2026, 9, 6), odometer=2000, quantity=100,
         price_per_litre=1.66, total_cost=166.00)
    seed(models.FuelRecord, vehicle_id=b, date=date(2026, 8, 31), odometer=1900, quantity=999,
         price_per_litre=1.66, total_cost=1658.34)                         # outside the window
    seed(models.MaintenanceRecord, vehicle_id=a, status="Completed", service_date=date(2026, 9, 7), cost=200)
    seed(models.MaintenanceRecord, vehicle_id=b, status="In Progress", service_date=date(2026, 9, 20), cost=300)
    seed(models.MaintenanceRecord, vehicle_id=c, status="Scheduled", service_date=date(2026, 9, 25), cost=50)
    seed(models.MaintenanceRecord, vehicle_id=a, status="Completed", service_date=date(2026, 8, 15), cost=1000)
    return a, b, c


def test_dashboard_values_worked_out_by_hand(manager, september):
    d = manager.get(f"/api/dashboard?{WINDOW}").json
    assert d["window"] == {"from": "2026-09-01", "to": "2026-09-30", "days": 30}
    assert d["cards"] == {"total_vehicles": 3, "active": 1, "under_maintenance": 1,
                          "total_distance_km": 1799.0,                    # 300+100+400+999
                          "fuel_cost": 253.2,                             # 87.20+166.00
                          "maintenance_cost": 500.0}                      # 200 + 300; Scheduled is only an estimate
    assert d["strip"] == {"fuel_litres": 140.0, "fuel_efficiency_km_per_l": 12.85,    # 1799/140
                          "cost_per_km": 0.419,                                       # (253.2+500)/1799
                          "utilisation_pct": 13.3}                                    # 8 active days / (2 x 30)


def test_dashboard_series(manager, september):
    s = manager.get(f"/api/dashboard?{WINDOW}").json["series"]
    assert s["monthly_fuel"] == [{"month": "2026-09", "fuel_litres": 140.0}]
    assert s["monthly_cost"] == [{"month": "2026-09", "fuel_cost": 253.2, "maintenance_cost": 500.0}]
    km_l = {r["type"]: r["km_per_l"] for r in s["efficiency_by_type"]}
    assert km_l == {"Truck": 4.0, "Van": 10.0, "Pickup": None, "Sedan": None}       # 400/100, 400/40, no data
    top = s["top_cost_per_km"]
    # 466/400 = 1.165, 287.2/400 = 0.718; the Inactive sedan drove 999 km at no counted cost, so 0.0
    assert [t["registration_no"] for t in top] == ["TRK 0001", "VAN 0001", "SED 0001"]
    assert [t["cost_per_km"] for t in top] == [1.165, 0.718, 0.0]


def test_a_window_spanning_months_lists_every_month(manager, september):
    s = manager.get("/api/dashboard?from=2026-07-01&to=2026-09-30").json["series"]
    assert [m["month"] for m in s["monthly_fuel"]] == ["2026-07", "2026-08", "2026-09"]
    aug = s["monthly_cost"][1]
    assert aug == {"month": "2026-08", "fuel_cost": 1658.34, "maintenance_cost": 1000.0}


def test_empty_window_gives_nulls_not_errors(manager, seed):
    seed(models.Vehicle)
    d = manager.get(f"/api/dashboard?{WINDOW}").json
    assert d["cards"]["total_distance_km"] == 0.0 and d["strip"]["fuel_efficiency_km_per_l"] is None
    assert d["strip"]["cost_per_km"] is None and d["series"]["top_cost_per_km"] == []


def test_default_window_is_the_last_twelve_complete_months(manager):
    w = manager.get("/api/dashboard").json["window"]
    assert (w["from"], w["to"], w["days"]) == ("2025-10-01", "2026-09-30", 365)       # clock frozen at 5 Oct 2026


def test_roles_only_admin_and_manager(admin, manager, operator, anon):
    for url in ("/api/dashboard", "/api/analytics/fuel", "/api/analytics/cost-per-km",
                "/api/analytics/utilisation", "/api/analytics/efficiency"):
        assert admin.get(url).status_code == 200 and manager.get(url).status_code == 200, url
        assert operator.get(url).status_code == 403, url
        assert anon.get(url).status_code == 401, url


@pytest.mark.parametrize("query", ["from=yesterday", "to=2026-13-01", "from=2026-09-30&to=2026-09-01",
                                   "from=2000-01-01&to=2026-09-30"])
def test_bad_windows_are_400(manager, query):
    r = manager.get(f"/api/dashboard?{query}")
    assert r.status_code == 400 and r.json["field"] in ("from", "to")


def test_analytics_endpoints(manager, september):
    fuel = manager.get(f"/api/analytics/fuel?{WINDOW}").json
    assert fuel["monthly"] == [{"month": "2026-09", "fuel_litres": 140.0, "fuel_cost": 253.2,
                                "maintenance_cost": 500.0}]
    assert {t["type"]: t["fuel_litres"] for t in fuel["by_type"]} == {"Truck": 100.0, "Van": 40.0, "Pickup": 0.0,
                                                                      "Sedan": 0.0}
    cpk = manager.get(f"/api/analytics/cost-per-km?{WINDOW}").json
    assert cpk["fleet_cost_per_km"] == 0.419
    assert [v["registration_no"] for v in cpk["vehicles"]] == ["TRK 0001", "VAN 0001", "SED 0001"]  # worst first
    assert cpk["vehicles"][-1]["cost_per_km"] == 0.0
    util = manager.get(f"/api/analytics/utilisation?{WINDOW}").json
    assert util["fleet_utilisation_pct"] == 13.3
    assert [(v["registration_no"], v["active_days"], v["utilisation_pct"]) for v in util["vehicles"]] == \
        [("VAN 0001", 6.0, 20.0), ("TRK 0001", 2.0, 6.7)]                      # the Inactive vehicle is left out
    eff = manager.get(f"/api/analytics/efficiency?{WINDOW}").json
    assert eff["fleet_km_per_l"] == 12.85
    assert [(v["registration_no"], v["km_per_l"]) for v in eff["vehicles"][:2]] == [("VAN 0001", 10.0),
                                                                                    ("TRK 0001", 4.0)]


# --- every indicator against a direct SQL query, on the full generated dataset (plan: Thu 8 Oct) ----------------

@pytest.fixture(scope="module")
def generated(app):
    """Load the seeded 12-month dataset into fleet_test once for this module."""
    import ml.generate_dummy_data as gen
    gen.load(gen.generate(), "test")
    yield app


def _scalar(app, sql, **params):
    with app.app_context():
        return db.session.execute(text(sql), params).scalar()


def test_dashboard_equals_direct_sql_on_the_generated_dataset(generated):
    app = generated
    from app import clock
    from tests.conftest import Api
    clock.freeze(date(2026, 10, 5))
    try:
        api = Api(app.test_client())
        app.extensions["login_throttle"].clear()
        assert api.login("manager@fleet.local", "Manager@123").status_code == 200
        s, e = "2025-10-01", "2026-09-30"

        started = time.perf_counter()
        d = api.get("/api/dashboard").json
        elapsed = time.perf_counter() - started
        assert elapsed < 3.0, f"dashboard took {elapsed:.2f}s (target: under 3 s for 50 vehicles)"

        distance = _scalar(app, "SELECT SUM(distance) FROM trips WHERE status='Completed' AND end_date BETWEEN :s AND :e", s=s, e=e)
        litres = _scalar(app, "SELECT SUM(quantity) FROM fuel_records WHERE date BETWEEN :s AND :e", s=s, e=e)
        fuel_cost = _scalar(app, "SELECT SUM(total_cost) FROM fuel_records WHERE date BETWEEN :s AND :e", s=s, e=e)
        maint = _scalar(app, "SELECT SUM(cost) FROM maintenance_records WHERE status IN ('In Progress','Completed') "
                             "AND service_date BETWEEN :s AND :e", s=s, e=e)
        active = _scalar(app, "SELECT SUM(DATEDIFF(LEAST(t.end_date, :e), GREATEST(t.start_date, :s)) + 1) "
                              "FROM trips t JOIN vehicles v USING (vehicle_id) WHERE t.status IN ('Completed','In Progress') "
                              "AND t.start_date <= :e AND t.end_date >= :s AND v.status <> 'Inactive'", s=s, e=e)
        n_live = _scalar(app, "SELECT COUNT(*) FROM vehicles WHERE status <> 'Inactive'")

        assert d["cards"]["total_distance_km"] == round(float(distance))
        assert d["cards"]["fuel_cost"] == round(float(fuel_cost), 2)
        assert d["cards"]["maintenance_cost"] == round(float(maint), 2)
        assert d["strip"]["fuel_litres"] == round(float(litres))
        assert d["strip"]["fuel_efficiency_km_per_l"] == round(float(distance) / float(litres), 2)
        assert d["strip"]["cost_per_km"] == round((float(fuel_cost) + float(maint)) / float(distance), 3)
        assert d["strip"]["utilisation_pct"] == round(100 * float(active) / (n_live * 365), 1)
        for key, status in (("total_vehicles", None), ("active", "Active"), ("under_maintenance", "Under Maintenance")):
            sql = "SELECT COUNT(*) FROM vehicles" + (" WHERE status = :st" if status else "")
            assert d["cards"][key] == _scalar(app, sql, st=status)

        # monthly series
        with app.app_context():
            fuel_rows = db.session.execute(text(
                "SELECT DATE_FORMAT(date,'%Y-%m') m, SUM(quantity) l, SUM(total_cost) c FROM fuel_records "
                "WHERE date BETWEEN :s AND :e GROUP BY m"), {"s": s, "e": e}).all()
            maint_rows = db.session.execute(text(
                "SELECT DATE_FORMAT(service_date,'%Y-%m') m, SUM(cost) c FROM maintenance_records "
                "WHERE status IN ('In Progress','Completed') AND service_date BETWEEN :s AND :e GROUP BY m"),
                {"s": s, "e": e}).all()
            by_type = db.session.execute(text(
                "SELECT v.type, SUM(t.distance) d FROM trips t JOIN vehicles v USING (vehicle_id) "
                "WHERE t.status='Completed' AND t.end_date BETWEEN :s AND :e GROUP BY v.type"), {"s": s, "e": e}).all()
            by_type_fuel = dict(db.session.execute(text(
                "SELECT v.type, SUM(f.quantity) FROM fuel_records f JOIN vehicles v USING (vehicle_id) "
                "WHERE f.date BETWEEN :s AND :e GROUP BY v.type"), {"s": s, "e": e}).all())
            top = db.session.execute(text(
                "SELECT v.vehicle_id, (COALESCE(f.c,0) + COALESCE(m.c,0)) / d.dist AS cpk FROM vehicles v "
                "JOIN (SELECT vehicle_id, SUM(distance) dist FROM trips WHERE status='Completed' "
                "      AND end_date BETWEEN :s AND :e GROUP BY vehicle_id) d USING (vehicle_id) "
                "LEFT JOIN (SELECT vehicle_id, SUM(total_cost) c FROM fuel_records WHERE date BETWEEN :s AND :e "
                "           GROUP BY vehicle_id) f USING (vehicle_id) "
                "LEFT JOIN (SELECT vehicle_id, SUM(cost) c FROM maintenance_records WHERE status IN "
                "           ('In Progress','Completed') AND service_date BETWEEN :s AND :e GROUP BY vehicle_id) m "
                "USING (vehicle_id) ORDER BY cpk DESC, v.vehicle_id LIMIT 10"), {"s": s, "e": e}).all()

        series = d["series"]
        assert {r["month"]: r["fuel_litres"] for r in series["monthly_fuel"]} == \
            {m: round(float(l), 1) for m, l, _ in fuel_rows}
        assert {r["month"]: (r["fuel_cost"], r["maintenance_cost"]) for r in series["monthly_cost"]} == \
            {m: (round(float(c), 2), round(float(dict(maint_rows).get(m, 0)), 2)) for m, _, c in fuel_rows}
        assert {r["type"]: r["km_per_l"] for r in series["efficiency_by_type"]} == \
            {t: round(float(dist) / float(by_type_fuel[t]), 2) for t, dist in by_type}
        assert [t["vehicle_id"] for t in series["top_cost_per_km"]] == [vid for vid, _ in top]
        assert [t["cost_per_km"] for t in series["top_cost_per_km"]] == [round(float(c), 3) for _, c in top]
    finally:
        clock.unfreeze()
