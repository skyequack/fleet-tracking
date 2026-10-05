"""The feature builder (ARCHITECTURE.md 10, R13) on small hand-computed fleets in fleet_test."""
from datetime import date

import pytest
from sqlalchemy import event

from app import models
from app.extensions import db
from app.services import features as F

AS_OF = date(2026, 9, 30)


@pytest.fixture
def fleet(seed):
    """Vehicle A is worked out in detail; B (truck), C (van) and E (sedan) have no fuel history; D is a measured truck."""
    d = seed(models.Driver)
    a = seed(models.Vehicle, registration_no="VAN 0001", type="Van", year=2022, odometer=20000)
    b = seed(models.Vehicle, registration_no="TRK 0001", type="Truck", year=2016, odometer=50000)
    c = seed(models.Vehicle, registration_no="VAN 0002", type="Van", year=2020, odometer=30000)
    t = seed(models.Vehicle, registration_no="TRK 0002", type="Truck", year=2019, odometer=300)
    e = seed(models.Vehicle, registration_no="SED 0001", type="Sedan", year=2024, odometer=900)

    def trip(vid, start, end, km, status="Completed"):
        seed(models.Trip, vehicle_id=vid, driver_id=d, start_date=start, end_date=end, distance=km, status=status)

    trip(a, date(2026, 6, 1), date(2026, 6, 2), 600)
    trip(a, date(2026, 9, 10), date(2026, 9, 10), 400)
    trip(a, date(2026, 10, 10), date(2026, 10, 11), 1000)                  # after as_of: master odometer includes it
    trip(a, date(2026, 9, 20), date(2026, 9, 20), 777, status="Planned")   # never counts

    def service(vid, kind, day, odo, cost, status="Completed"):
        seed(models.MaintenanceRecord, vehicle_id=vid, service_type=kind, service_date=day, odometer=odo, cost=cost,
             status=status)

    service(a, "Oil Change", date(2026, 3, 1), 15000, 200)
    service(a, "Breakdown Repair", date(2026, 6, 15), 17000, 1000)
    service(a, "Brakes", date(2026, 9, 30), 18900, 500)                    # on as_of: the last service, not "earlier"
    service(a, "Tyres", date(2026, 8, 1), 18000, 300, status="In Progress")
    service(a, "Inspection", date(2026, 7, 1), 18500, 999, status="Scheduled")   # not happened: ignored
    service(a, "Oil Change", date(2026, 10, 5), 19500, 250)                # after as_of: ignored

    def fuel(vid, day, odo, qty, full=True):
        seed(models.FuelRecord, vehicle_id=vid, date=day, odometer=odo, quantity=qty, full_tank=full,
             price_per_litre=1, total_cost=qty)

    fuel(a, date(2026, 5, 1), 10000, 30)
    fuel(a, date(2026, 5, 20), 10300, 15, full=False)
    fuel(a, date(2026, 6, 10), 10600, 25)                                  # 600 km / (15 + 25) L = 15 km/L
    fuel(a, date(2026, 10, 2), 11500, 10)                                  # after as_of: ignored
    fuel(t, date(2026, 5, 1), 0, 100)
    fuel(t, date(2026, 6, 1), 300, 100)                                    # 300 km / 100 L = 3 km/L
    return dict(a=a, b=b, c=c, t=t, e=e)


def build(world, as_of=AS_OF, **kw):
    with world.app_context():
        return F.build_features(as_of, **kw)


def test_every_feature_for_a_fully_worked_vehicle(world, fleet):
    row = build(world).loc[fleet["a"]]
    months = (AS_OF - date(2026, 6, 1)).days / F.DAYS_PER_MONTH            # first trip to as_of
    assert row["vehicle_age"] == 4                                          # 2026 - 2022
    assert row["current_mileage"] == 19000                                  # 20000 master - 1000 km driven after as_of
    assert row["km_since_service"] == 100                                   # 19000 - 18900 at the service on as_of
    assert row["prev_breakdowns"] == 1                                      # the 15 Jun repair
    assert row["maintenance_events"] == 3                                   # Mar oil change, Jun repair, Aug tyres (In Progress)
    assert row["prev_maint_cost"] == 1500                                   # 200 + 1000 + 300
    assert row["avg_monthly_km"] == pytest.approx(1000 / months)            # the 400 and 600 km completed trips
    assert row["avg_fuel_l_per_100km"] == pytest.approx(100 / 15)           # partial fill counted in the litres
    assert not row["imputed_fuel"]


def test_the_features_are_floats_in_the_documented_order(world, fleet):
    df = build(world)
    assert list(df.columns) == F.FEATURES + ["imputed_fuel"]
    assert set(df[F.FEATURES].dtypes.astype(str)) == {"float64"}           # Decimal columns would break the model


def test_cold_start_vehicle_without_history(world, fleet):
    row = build(world).loc[fleet["b"]]
    assert row["vehicle_age"] == 10 and row["current_mileage"] == 50000
    assert row["km_since_service"] == 50000                                 # no completed service: the whole mileage
    assert row["avg_monthly_km"] == 0                                       # no trips: not a division by zero
    assert row["prev_breakdowns"] == row["maintenance_events"] == row["prev_maint_cost"] == 0


def test_missing_fuel_history_uses_the_median_of_the_vehicle_type_and_is_flagged(world, fleet):
    df = build(world)
    assert df.loc[fleet["b"], "avg_fuel_l_per_100km"] == pytest.approx(100 / 3) and df.loc[fleet["b"], "imputed_fuel"]
    assert df.loc[fleet["c"], "avg_fuel_l_per_100km"] == pytest.approx(100 / 15) and df.loc[fleet["c"], "imputed_fuel"]
    assert not df.loc[fleet["t"], "imputed_fuel"] and df.loc[fleet["t"], "avg_fuel_l_per_100km"] == pytest.approx(100 / 3)


def test_a_type_with_no_data_falls_back_to_the_fleet_median(world, fleet):
    row = build(world).loc[fleet["e"]]                                      # the only sedan: median of 15 and 3 km/L is 9
    assert row["avg_fuel_l_per_100km"] == pytest.approx(100 / 9) and row["imputed_fuel"]


def test_one_full_tank_fill_is_not_enough_to_measure_efficiency(world, seed):
    vid = seed(models.Vehicle)
    seed(models.FuelRecord, vehicle_id=vid, date=date(2026, 9, 1), odometer=500, quantity=40, total_cost=87.20)
    row = build(world).loc[vid]
    assert row["imputed_fuel"] and row["avg_fuel_l_per_100km"] == pytest.approx(100 / F.FALLBACK_KM_PER_L)


def test_no_future_record_can_change_a_snapshot(world, fleet, seed):
    """Training on a past snapshot must see exactly what was known then (R13: no leakage from the future)."""
    before = build(world)
    a = fleet["a"]
    d = seed(models.Driver, license_no="1099999999")
    with world.app_context():                                               # the real flow also raises the master odometer
        vehicle = db.session.get(models.Vehicle, a)
        vehicle.odometer = vehicle.odometer + 500
        db.session.commit()
    seed(models.Trip, vehicle_id=a, driver_id=d, start_date=date(2026, 11, 1), end_date=date(2026, 11, 2), distance=500,
         status="Completed")
    seed(models.FuelRecord, vehicle_id=a, date=date(2026, 11, 3), odometer=12000, quantity=60, price_per_litre=1, total_cost=60)
    seed(models.MaintenanceRecord, vehicle_id=a, service_type="Breakdown Repair", service_date=date(2026, 11, 4),
         odometer=19900, cost=5000, status="Completed")
    after = build(world)
    assert after.loc[a].equals(before.loc[a])
    assert build(world, date(2026, 11, 30)).loc[a, "prev_breakdowns"] == 2   # but a later snapshot does see them


def test_a_record_dated_on_as_of_counts_for_the_last_service_but_not_as_earlier(world, fleet):
    on_day = build(world, AS_OF).loc[fleet["a"]]
    next_day = build(world, date(2026, 10, 1)).loc[fleet["a"]]
    assert on_day["maintenance_events"] == 3 and next_day["maintenance_events"] == 4   # the 30 Sep Brakes record
    assert next_day["prev_maint_cost"] == 2000


def test_months_observed_is_at_least_one(world, seed):
    vid, d = seed(models.Vehicle), seed(models.Driver)
    seed(models.Trip, vehicle_id=vid, driver_id=d, start_date=date(2026, 9, 29), end_date=date(2026, 9, 29), distance=300,
         status="Completed")
    assert build(world).loc[vid, "avg_monthly_km"] == 300


def test_requested_vehicles_only(world, fleet):
    df = build(world, vehicle_ids=[fleet["a"], fleet["b"]])
    assert list(df.index) == [fleet["a"], fleet["b"]]


def test_query_count_does_not_grow_with_the_number_of_vehicles(world, seed):
    """R13: a few grouped queries for all vehicles, never a query set per vehicle."""
    def count_queries():
        statements = []

        def record(conn, cursor, statement, *a):
            statements.append(statement)
        with world.app_context():
            event.listen(db.engine, "before_cursor_execute", record)
            try:
                F.build_features(AS_OF)
            finally:
                event.remove(db.engine, "before_cursor_execute", record)
        return len(statements)

    for i in range(3):
        seed(models.Vehicle, registration_no=f"AAA {i}000")
    few = count_queries()
    for i in range(30):
        seed(models.Vehicle, registration_no=f"BBB {i}000")
    assert count_queries() == few and few <= 10
