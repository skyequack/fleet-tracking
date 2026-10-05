"""The eight model features (ARCHITECTURE.md 10), shared by training and inference so they cannot drift apart.

build_features(as_of) reads only records dated on or before `as_of` and does a handful of grouped SQL queries for
all vehicles at once, never one query set per vehicle (R13). "Earlier" records (counts, costs) are those dated
strictly before `as_of`. Training calls it once per monthly snapshot; inference calls it with today.

Cold start (R13): fewer than two full-tank fills uses the median efficiency of the vehicle's type and sets
`imputed_fuel`; no completed service gives km_since_service = current_mileage; no trips gives avg_monthly_km = 0.
"""
import numpy as np
import pandas as pd
from sqlalchemy import case, func, select

from ..extensions import db
from ..models import FuelRecord, MaintenanceRecord, Trip, Vehicle

FEATURES = ["vehicle_age", "current_mileage", "km_since_service", "prev_breakdowns", "maintenance_events",
            "avg_monthly_km", "avg_fuel_l_per_100km", "prev_maint_cost"]
CLASSES = ["LOW", "MEDIUM", "HIGH"]
RECORDED = ("In Progress", "Completed")   # a Scheduled service has not happened yet; its cost is only an estimate
DAYS_PER_MONTH = 30.4375
FALLBACK_KM_PER_L = 7.0                   # only if no vehicle at all has fuel history


def l_per_100km(km_per_l):
    """G5: the report shows km/L, the model uses L/100 km. This is the one place that converts."""
    return 100.0 / km_per_l


def _frame(stmt):
    return pd.read_sql(stmt, db.session.connection())


def build_features(as_of, vehicle_ids=None):
    """DataFrame indexed by vehicle_id with the eight FEATURES plus the boolean `imputed_fuel`."""
    vehicles = _frame(select(Vehicle.vehicle_id, Vehicle.type, Vehicle.year, Vehicle.odometer)).set_index("vehicle_id")

    # 2. current_mileage: the master odometer, wound back by trips completed after as_of (so as_of=today is exact)
    later = _frame(select(Trip.vehicle_id, func.sum(Trip.distance).label("d")).where(
        Trip.status == "Completed", Trip.end_date > as_of).group_by(Trip.vehicle_id)).set_index("vehicle_id")["d"].astype(float)
    mileage = (vehicles["odometer"].astype(float) - later.reindex(vehicles.index).fillna(0.0)).clip(lower=0.0)

    # 3. km_since_service: the reading at the latest completed service (dated on or before as_of)
    ranked = select(MaintenanceRecord.vehicle_id, MaintenanceRecord.odometer, func.row_number().over(
        partition_by=MaintenanceRecord.vehicle_id,
        order_by=(MaintenanceRecord.service_date.desc(), MaintenanceRecord.maintenance_id.desc())).label("rn")
    ).where(MaintenanceRecord.status == "Completed", MaintenanceRecord.service_date <= as_of).subquery()
    last_service = _frame(select(ranked.c.vehicle_id, ranked.c.odometer).where(ranked.c.rn == 1)).set_index(
        "vehicle_id")["odometer"].astype(float)
    km_since_service = (mileage - last_service.reindex(vehicles.index)).fillna(mileage).clip(lower=0.0)

    # 4, 5, 8. earlier maintenance: breakdowns, events, cost
    earlier = _frame(select(
        MaintenanceRecord.vehicle_id,
        func.sum(case((MaintenanceRecord.service_type == "Breakdown Repair", 1), else_=0)).label("breakdowns"),
        func.count().label("events"), func.sum(MaintenanceRecord.cost).label("cost")
    ).where(MaintenanceRecord.status.in_(RECORDED), MaintenanceRecord.service_date < as_of)
        .group_by(MaintenanceRecord.vehicle_id)).set_index("vehicle_id").astype(float)
    earlier = earlier.reindex(vehicles.index).fillna(0.0)

    # 6. avg_monthly_km: completed distance / months since the vehicle's first completed trip (at least 1)
    trips = _frame(select(Trip.vehicle_id, func.sum(Trip.distance).label("km"), func.min(Trip.start_date).label("first"))
                   .where(Trip.status == "Completed", Trip.end_date <= as_of).group_by(Trip.vehicle_id)
                   ).set_index("vehicle_id").reindex(vehicles.index)
    months = ((pd.Timestamp(as_of) - pd.to_datetime(trips["first"])).dt.days / DAYS_PER_MONTH).clip(lower=1.0)
    avg_monthly_km = (trips["km"].astype(float) / months).fillna(0.0)

    # 7. avg_fuel_l_per_100km, from the full-tank fills: distance between the first and last full tank over the
    #    litres added after the first one up to and including the last one (partial fills in between count)
    full = (select(FuelRecord.vehicle_id, func.min(FuelRecord.odometer).label("first_odo"),
                   func.max(FuelRecord.odometer).label("last_odo"), func.count().label("n_full"))
            .where(FuelRecord.full_tank.is_(True), FuelRecord.date <= as_of).group_by(FuelRecord.vehicle_id).subquery())
    litres = _frame(select(FuelRecord.vehicle_id, func.sum(FuelRecord.quantity).label("litres"))
                    .join(full, full.c.vehicle_id == FuelRecord.vehicle_id)
                    .where(FuelRecord.date <= as_of, FuelRecord.odometer > full.c.first_odo,
                           FuelRecord.odometer <= full.c.last_odo).group_by(FuelRecord.vehicle_id)
                    ).set_index("vehicle_id")["litres"]
    fills = _frame(select(full.c.vehicle_id, full.c.first_odo, full.c.last_odo, full.c.n_full)).set_index("vehicle_id")
    fills = fills.reindex(vehicles.index)
    km = (fills["last_odo"].astype(float) - fills["first_odo"].astype(float))
    km_per_l = km / litres.reindex(vehicles.index).astype(float)
    measured = (fills["n_full"] >= 2) & (km > 0) & km_per_l.notna() & np.isfinite(km_per_l)
    km_per_l = km_per_l.where(measured)
    by_type = km_per_l.groupby(vehicles["type"]).median()
    overall = km_per_l.median()
    fallback = overall if pd.notna(overall) else FALLBACK_KM_PER_L
    type_median = vehicles["type"].map(by_type).fillna(fallback)
    km_per_l = km_per_l.fillna(type_median)

    out = pd.DataFrame({
        "vehicle_age": as_of.year - vehicles["year"].astype(float),
        "current_mileage": mileage,
        "km_since_service": km_since_service,
        "prev_breakdowns": earlier["breakdowns"],
        "maintenance_events": earlier["events"],
        "avg_monthly_km": avg_monthly_km,
        "avg_fuel_l_per_100km": km_per_l.map(l_per_100km),
        "prev_maint_cost": earlier["cost"],
    }, index=vehicles.index)
    out[FEATURES] = out[FEATURES].astype(float)   # Decimal arithmetic can leave object columns; the model needs floats
    out["imputed_fuel"] = ~measured
    out = out[FEATURES + ["imputed_fuel"]]
    return out if vehicle_ids is None else out.loc[list(vehicle_ids)]


# ---- synthetic labels (report 7.10, architecture G2) ----------------------------------------------------------------
LABEL_WEIGHTS = {"vehicle_age": 0.30, "current_mileage": 0.25, "km_since_service": 0.30, "prev_breakdowns": 0.15}
LABEL_SCALES = {"vehicle_age": 8.0, "current_mileage": 250_000.0, "km_since_service": 5_000.0, "prev_breakdowns": 2.0}
LABEL_NOISE_SD = 0.10
LABEL_THRESHOLDS = (0.40, 0.60)   # LOW below the first, MEDIUM below the second, else HIGH (about 42/27/31 %)


def risk_score(features):
    """The documented formula: a weighted sum of four scaled features. No noise yet."""
    return sum(w * features[name].astype(float) / LABEL_SCALES[name] for name, w in LABEL_WEIGHTS.items())


def risk_labels(features, rng):
    """LOW / MEDIUM / HIGH from the formula plus Gaussian noise. These are synthetic labels, not failure data."""
    noisy = risk_score(features) + rng.normal(0.0, LABEL_NOISE_SD, len(features))
    return pd.Series(np.select([noisy < LABEL_THRESHOLDS[0], noisy < LABEL_THRESHOLDS[1]], ["LOW", "MEDIUM"], "HIGH"),
                     index=features.index)


def label_source():
    terms = " + ".join(f"{w} x {n}/{LABEL_SCALES[n]:g}" for n, w in LABEL_WEIGHTS.items())
    return (f"SYNTHETIC. risk score = {terms}, plus Gaussian noise (sd {LABEL_NOISE_SD}); LOW below "
            f"{LABEL_THRESHOLDS[0]}, MEDIUM below {LABEL_THRESHOLDS[1]}, otherwise HIGH. The labels come from a "
            "formula over the model's own features, so the model learns that formula. This is not real failure "
            "data and the results demonstrate the method only.")
