"""Unit tests for the analytics maths on hand-made frames: no database."""
from datetime import date

import pandas as pd
import pytest

from app.services import analytics as a

W = a.Window(date(2026, 9, 1), date(2026, 9, 30))   # 30 days


@pytest.mark.parametrize("today, start, end", [
    (date(2026, 10, 5), date(2025, 10, 1), date(2026, 9, 30)),
    (date(2026, 1, 15), date(2025, 1, 1), date(2025, 12, 31)),
    (date(2026, 3, 1), date(2025, 3, 1), date(2026, 2, 28)),      # the current month is never included
    (date(2028, 3, 20), date(2027, 3, 1), date(2028, 2, 29)),     # leap February
    (date(2026, 12, 31), date(2025, 12, 1), date(2026, 11, 30)),
])
def test_default_window_is_the_last_twelve_complete_months(today, start, end):
    assert a.default_window(today) == (start, end)


def test_window_days_is_inclusive():
    assert a.window_days(W) == 30
    assert a.window_days(a.Window(date(2026, 9, 5), date(2026, 9, 5))) == 1


def test_num_gives_json_safe_floats():
    assert a._num(None, 2) is None and a._num(float("nan"), 2) is None
    assert a._num(pd.NA, 2) is None
    assert a._num(1.23456, 2) == 1.23 and isinstance(a._num(3, 1), float)


def frames():
    return dict(
        vehicles=pd.DataFrame({"vehicle_id": [1, 2, 3], "registration_no": ["A", "B", "C"],
                               "type": ["Van", "Truck", "Sedan"], "status": ["Active", "Under Maintenance", "Inactive"]}),
        distance=pd.DataFrame({"vehicle_id": [1, 1, 2], "month": ["2026-09", "2026-08", "2026-09"],
                               "distance": [300.0, 100.0, 400.0]}),
        fuel=pd.DataFrame({"vehicle_id": [1, 2], "month": ["2026-09", "2026-09"], "litres": [40.0, 100.0],
                           "fuel_cost": [87.2, 166.0]}),
        maint=pd.DataFrame({"vehicle_id": [1, 2], "month": ["2026-09", "2026-09"], "maint_cost": [200.0, 300.0]}),
        active=pd.DataFrame({"vehicle_id": [1, 2], "active_days": [6.0, 3.0]}))


def test_vehicle_table_and_fleet_indicators():
    table = a.vehicle_table(frames(), W)
    assert table.set_index("vehicle_id").loc[1, "km_per_l"] == pytest.approx(400 / 40)
    assert table.set_index("vehicle_id").loc[1, "cost_per_km"] == pytest.approx((87.2 + 200) / 400)
    assert pd.isna(table.set_index("vehicle_id").loc[3, "km_per_l"])             # no distance, no fuel: undefined
    f = a.fleet_indicators(table, W)
    assert f["distance_km"] == 800 and f["fuel_litres"] == 140
    assert f["km_per_l"] == pytest.approx(800 / 140)
    assert f["cost_per_km"] == pytest.approx((253.2 + 500) / 800)
    assert f["utilisation_pct"] == pytest.approx(100 * 9 / (2 * 30))             # the Inactive vehicle is not available


def test_inactive_vehicles_have_no_utilisation_but_keep_their_history():
    table = a.vehicle_table(frames(), W).set_index("vehicle_id")
    assert pd.isna(table.loc[3, "utilisation_pct"])
    assert table.loc[1, "utilisation_pct"] == pytest.approx(20.0)                # 6 of 30 days


def test_by_type_covers_every_type_even_when_empty():
    rows = {r["type"]: r for r in a.by_type(a.vehicle_table(frames(), W), W)}
    assert set(rows) == {"Truck", "Van", "Pickup", "Sedan"}
    assert rows["Van"]["km_per_l"] == 10.0 and rows["Truck"]["utilisation_pct"] == 10.0
    assert rows["Pickup"]["vehicles"] == 0 and rows["Pickup"]["km_per_l"] is None
    assert rows["Pickup"]["utilisation_pct"] is None and rows["Sedan"]["utilisation_pct"] is None


def test_monthly_fills_empty_months_with_zero():
    w = a.Window(date(2026, 7, 15), date(2026, 9, 30))
    rows = a.monthly(frames(), w)
    assert [r["month"] for r in rows] == ["2026-07", "2026-08", "2026-09"]
    assert rows[0] == {"month": "2026-07", "fuel_litres": 0.0, "fuel_cost": 0.0, "maintenance_cost": 0.0}
    assert rows[2]["fuel_litres"] == 140.0 and rows[2]["maintenance_cost"] == 500.0


def test_empty_frames_do_not_crash_and_give_none_not_zero_for_ratios():
    empty = dict(vehicles=frames()["vehicles"].iloc[0:0],
                 distance=pd.DataFrame({"vehicle_id": [], "month": [], "distance": []}),
                 fuel=pd.DataFrame({"vehicle_id": [], "month": [], "litres": [], "fuel_cost": []}),
                 maint=pd.DataFrame({"vehicle_id": [], "month": [], "maint_cost": []}),
                 active=pd.DataFrame({"vehicle_id": [], "active_days": []}))
    f = a.fleet_indicators(a.vehicle_table(empty, W), W)
    assert f["km_per_l"] is None and f["cost_per_km"] is None and f["utilisation_pct"] is None
    assert len(a.monthly(empty, W)) == 1
