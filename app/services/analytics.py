"""Read-only analytics (ARCHITECTURE.md 9). Grouping happens in SQL; only the small grouped results enter Pandas.

Definitions (9.1, R12):
  fuel efficiency  = total distance / total fuel               (km/L)
  cost per km      = (fuel cost + maintenance cost) / distance (SAR/km)
  utilisation      = active days / available days x 100
Distance counts Completed trips, dated by their end date. Fuel is dated by fill date. Maintenance cost counts
In Progress and Completed services (a Scheduled one is only an estimate), dated by service date. An active day
falls inside a Completed or In Progress trip, clipped to the window; available days are the window's days times
the vehicles that are not Inactive.
"""
import math
from collections import namedtuple
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, select

from ..extensions import db
from ..models import FuelRecord, MaintenanceRecord, Trip, Vehicle

Window = namedtuple("Window", "start end")

MAX_WINDOW_DAYS = 3660
COST_STATUSES = ("In Progress", "Completed")
ACTIVE_TRIP_STATUSES = ("Completed", "In Progress")
TYPES = ("Truck", "Van", "Pickup", "Sedan")


def default_window(today):
    """The last 12 complete calendar months, e.g. 2025-10-01 to 2026-09-30 when today is in October 2026."""
    end = today.replace(day=1) - timedelta(days=1)
    months = end.year * 12 + end.month - 1 - 11
    return Window(date(months // 12, months % 12 + 1, 1), end)


def window_days(window):
    return (window.end - window.start).days + 1


def _num(x, places):
    """A plain Python float (or None for missing, NaN or division by zero) for JSON."""
    if x is None or (isinstance(x, float) and math.isnan(x)) or pd.isna(x):
        return None
    return round(float(x), places)


def _ratio(a, b):
    return float(a) / float(b) if b else None


def _read(stmt):
    return pd.read_sql(stmt, db.session.connection())


def _month(column):
    return func.date_format(column, "%Y-%m")


def load_frames(window):
    """One grouped query per source. Row counts are vehicles x months at most, never whole tables."""
    s, e = window
    vehicles = _read(select(Vehicle.vehicle_id, Vehicle.registration_no, Vehicle.type, Vehicle.status)
                     .order_by(Vehicle.vehicle_id))
    distance = _read(
        select(Trip.vehicle_id, _month(Trip.end_date).label("month"), func.sum(Trip.distance).label("distance"))
        .where(Trip.status == "Completed", Trip.end_date.between(s, e))
        .group_by(Trip.vehicle_id, "month"))
    fuel = _read(
        select(FuelRecord.vehicle_id, _month(FuelRecord.date).label("month"),
               func.sum(FuelRecord.quantity).label("litres"), func.sum(FuelRecord.total_cost).label("fuel_cost"))
        .where(FuelRecord.date.between(s, e)).group_by(FuelRecord.vehicle_id, "month"))
    maint = _read(
        select(MaintenanceRecord.vehicle_id, _month(MaintenanceRecord.service_date).label("month"),
               func.sum(MaintenanceRecord.cost).label("maint_cost"))
        .where(MaintenanceRecord.status.in_(COST_STATUSES), MaintenanceRecord.service_date.between(s, e))
        .group_by(MaintenanceRecord.vehicle_id, "month"))
    days_in_window = (func.datediff(func.least(Trip.end_date, e), func.greatest(Trip.start_date, s)) + 1)
    active = _read(
        select(Trip.vehicle_id, func.sum(days_in_window).label("active_days"))
        .join(Vehicle, Vehicle.vehicle_id == Trip.vehicle_id)
        .where(Trip.status.in_(ACTIVE_TRIP_STATUSES), Trip.start_date <= e, Trip.end_date >= s,
               Vehicle.status != "Inactive")
        .group_by(Trip.vehicle_id))
    return dict(vehicles=vehicles, distance=distance, fuel=fuel, maint=maint, active=active)


def vehicle_table(frames, window):
    """One row per vehicle with its totals for the window and the indicators derived from them."""
    t = frames["vehicles"].copy()
    for key, column in (("distance", "distance"), ("fuel", "litres"), ("fuel", "fuel_cost"),
                        ("maint", "maint_cost"), ("active", "active_days")):
        sums = frames[key].groupby("vehicle_id")[column].sum()
        t[column] = t["vehicle_id"].map(sums).fillna(0.0).astype(float)
    t["km_per_l"] = t["distance"] / t["litres"].where(t["litres"] > 0)
    t["cost_per_km"] = (t["fuel_cost"] + t["maint_cost"]) / t["distance"].where(t["distance"] > 0)
    not_inactive = t["status"] != "Inactive"
    t["utilisation_pct"] = (t["active_days"] / window_days(window) * 100).where(not_inactive)
    return t


def by_type(table, window):
    rows = []
    for vtype in TYPES:
        g = table[table["type"] == vtype]
        live = g[g["status"] != "Inactive"]
        distance, litres = g["distance"].sum(), g["litres"].sum()
        fuel_cost, maint_cost = g["fuel_cost"].sum(), g["maint_cost"].sum()
        available = len(live) * window_days(window)
        utilisation = 100 * live["active_days"].sum() / available if available else None
        rows.append({
            "type": vtype, "vehicles": int(len(g)), "distance_km": _num(distance, 0), "fuel_litres": _num(litres, 0),
            "fuel_cost": _num(fuel_cost, 2), "maintenance_cost": _num(maint_cost, 2),
            "km_per_l": _num(_ratio(distance, litres), 2),
            "cost_per_km": _num(_ratio(fuel_cost + maint_cost, distance), 3),
            "utilisation_pct": _num(utilisation, 1)})
    return rows


def fleet_indicators(table, window):
    distance, litres = table["distance"].sum(), table["litres"].sum()
    fuel_cost, maint_cost = table["fuel_cost"].sum(), table["maint_cost"].sum()
    live = table[table["status"] != "Inactive"]
    available = len(live) * window_days(window)
    return {"distance_km": distance, "fuel_litres": litres, "fuel_cost": fuel_cost, "maintenance_cost": maint_cost,
            "km_per_l": _ratio(distance, litres), "cost_per_km": _ratio(fuel_cost + maint_cost, distance),
            "utilisation_pct": 100 * live["active_days"].sum() / available if available else None}


def monthly(frames, window):
    """Fuel litres and cost and maintenance cost per calendar month, with empty months shown as zero."""
    months = [str(p) for p in pd.period_range(window.start, window.end, freq="M")]
    fuel = frames["fuel"].groupby("month")[["litres", "fuel_cost"]].sum().reindex(months, fill_value=0.0)
    maint = frames["maint"].groupby("month")["maint_cost"].sum().reindex(months, fill_value=0.0)
    return [{"month": m, "fuel_litres": _num(fuel.loc[m, "litres"], 1), "fuel_cost": _num(fuel.loc[m, "fuel_cost"], 2),
             "maintenance_cost": _num(maint.loc[m], 2)} for m in months]


def _window_json(window):
    return {"from": window.start.isoformat(), "to": window.end.isoformat(), "days": window_days(window)}


def _vehicle_rows(table, columns):
    rows = []
    for r in table.to_dict("records"):
        row = {"vehicle_id": int(r["vehicle_id"]), "registration_no": r["registration_no"], "type": r["type"],
               "status": r["status"]}
        row.update({name: _num(r[name], places) for name, places in columns})
        rows.append(row)
    return rows


def build_dashboard(window):
    """The one aggregate payload behind GET /api/dashboard (9.2): 6 cards, 4 strip values, 4 series."""
    frames = load_frames(window)
    table = vehicle_table(frames, window)
    fleet = fleet_indicators(table, window)
    status = table["status"].value_counts()
    types = by_type(table, window)
    top = table[table["cost_per_km"].notna()].sort_values(["cost_per_km", "vehicle_id"], ascending=[False, True]).head(10)
    return {
        "window": _window_json(window),
        "cards": {"total_vehicles": int(len(table)), "active": int(status.get("Active", 0)),
                  "under_maintenance": int(status.get("Under Maintenance", 0)),
                  "total_distance_km": _num(fleet["distance_km"], 0), "fuel_cost": _num(fleet["fuel_cost"], 2),
                  "maintenance_cost": _num(fleet["maintenance_cost"], 2)},
        "strip": {"fuel_litres": _num(fleet["fuel_litres"], 0), "fuel_efficiency_km_per_l": _num(fleet["km_per_l"], 2),
                  "cost_per_km": _num(fleet["cost_per_km"], 3), "utilisation_pct": _num(fleet["utilisation_pct"], 1)},
        "series": {
            "monthly_fuel": [{"month": m["month"], "fuel_litres": m["fuel_litres"]} for m in monthly(frames, window)],
            "monthly_cost": [{"month": m["month"], "fuel_cost": m["fuel_cost"],
                              "maintenance_cost": m["maintenance_cost"]} for m in monthly(frames, window)],
            "efficiency_by_type": [{"type": t["type"], "km_per_l": t["km_per_l"]} for t in types],
            "top_cost_per_km": _vehicle_rows(top, [("cost_per_km", 3)]),
        },
    }


def fuel_analytics(window):
    frames = load_frames(window)
    table = vehicle_table(frames, window)
    types = by_type(table, window)
    return {"window": _window_json(window), "monthly": monthly(frames, window),
            "by_type": [{k: t[k] for k in ("type", "fuel_litres", "fuel_cost", "km_per_l")} for t in types]}


def cost_per_km_analytics(window):
    table = vehicle_table(load_frames(window), window)
    fleet = fleet_indicators(table, window)
    ranked = table.sort_values(["cost_per_km", "vehicle_id"], ascending=[False, True], na_position="last")
    return {"window": _window_json(window), "fleet_cost_per_km": _num(fleet["cost_per_km"], 3),
            "by_type": [{k: t[k] for k in ("type", "vehicles", "fuel_cost", "maintenance_cost", "distance_km",
                                           "cost_per_km")} for t in by_type(table, window)],
            "vehicles": _vehicle_rows(ranked, [("distance", 0), ("fuel_cost", 2), ("maint_cost", 2),
                                               ("cost_per_km", 3)])}


def utilisation_analytics(window):
    table = vehicle_table(load_frames(window), window)
    fleet = fleet_indicators(table, window)
    live = table[table["status"] != "Inactive"].sort_values(["utilisation_pct", "vehicle_id"],
                                                             ascending=[False, True])
    return {"window": _window_json(window), "fleet_utilisation_pct": _num(fleet["utilisation_pct"], 1),
            "by_type": [{k: t[k] for k in ("type", "vehicles", "utilisation_pct")} for t in by_type(table, window)],
            "vehicles": _vehicle_rows(live, [("active_days", 0), ("utilisation_pct", 1)])}


def efficiency_analytics(window):
    table = vehicle_table(load_frames(window), window)
    fleet = fleet_indicators(table, window)
    ranked = table.sort_values(["km_per_l", "vehicle_id"], ascending=[False, True], na_position="last")
    return {"window": _window_json(window), "fleet_km_per_l": _num(fleet["km_per_l"], 2),
            "by_type": [{k: t[k] for k in ("type", "vehicles", "distance_km", "fuel_litres", "km_per_l")}
                        for t in by_type(table, window)],
            "vehicles": _vehicle_rows(ranked, [("distance", 0), ("litres", 0), ("km_per_l", 2)])}
