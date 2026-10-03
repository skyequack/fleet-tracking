"""Seeded dummy-data generator for the fleet database.

50 vehicles (14 trucks, 14 vans, 14 pickups, 8 sedans), 12 months (Oct 2025 to Sep 2026) of
trips, fuel and maintenance, plus one login per role.

The data is synthetic. Per-type distance and maintenance totals are scaled to the figures in the
project report (Tables 7.2 and 7.4) so the dashboard lands in the same neighbourhood; fuel,
utilisation and cost per km then follow from the generated records. The parameters below are the
calibrated values: change them and the totals move.

Usage:
    python ml/generate_dummy_data.py              # generate and load into fleet_db
    python ml/generate_dummy_data.py --dry-run    # generate and print totals only
    python ml/generate_dummy_data.py --db test    # load into fleet_test instead
"""
import argparse
import bisect
import hashlib
import json
import math
import pathlib
import random
import sys
from datetime import date, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config import Config  # noqa: E402

SEED = 42
START, END, TODAY = date(2025, 10, 1), date(2026, 9, 30), date(2026, 10, 3)
SUMMER_MONTHS, SUMMER_FACTOR = (6, 7, 8), 1.07
# average litres-per-km multiplier over the year, so base km/L reproduces the report's km/L
YEAR_FACTOR = (9 * 1.0 + 3 * SUMMER_FACTOR) / 12
N_UNDER_MAINTENANCE = 7
N_SPARE_DRIVERS, N_REPLACED = 6, 4  # spares not assigned at the start; first N_REPLACED take over a vehicle

# dist / maint are the report's per-type targets (km, SAR); kmpl is the report's fleet km/L.
TYPES = {
    "Truck": dict(n=14, fuel="Diesel", kmpl=3.56, annual_km=49200, daily_km=200, tank=200,
                  dist=688559, maint=50600, maint_mult=1.8, breakdown_mult=1.3,
                  trip_days=[1, 2, 3, 4, 5], trip_w=[2, 3, 3, 2, 1], gap_w=[30, 35, 25, 10],
                  models=[("Isuzu", "NPR"), ("Hino", "500"), ("Mercedes-Benz", "Actros"), ("Volvo", "FH")]),
    "Van": dict(n=14, fuel="Petrol", kmpl=8.05, annual_km=16100, daily_km=66, tank=70,
                dist=225892, maint=60950, maint_mult=1.0, breakdown_mult=1.0,
                trip_days=[1, 2, 3], trip_w=[4, 3, 1], gap_w=[50, 35, 12, 3],
                models=[("Toyota", "HiAce"), ("Hyundai", "H1"), ("Ford", "Transit")]),
    "Pickup": dict(n=14, fuel="Petrol", kmpl=7.16, annual_km=18100, daily_km=74, tank=80,
                   dist=254222, maint=32150, maint_mult=1.0, breakdown_mult=0.9,
                   trip_days=[1, 2, 3], trip_w=[4, 3, 1], gap_w=[50, 35, 12, 3],
                   models=[("Toyota", "Hilux"), ("Nissan", "Navara"), ("Isuzu", "D-Max"), ("Ford", "Ranger")]),
    "Sedan": dict(n=8, fuel="Petrol", kmpl=12.18, annual_km=10400, daily_km=43, tank=55,
                  dist=83355, maint=27450, maint_mult=0.8, breakdown_mult=0.8,
                  trip_days=[1, 2], trip_w=[3, 1], gap_w=[55, 35, 8, 2],
                  models=[("Toyota", "Camry"), ("Hyundai", "Sonata"), ("Honda", "Accord")]),
}

BASE_COST = {"Oil Change": 250, "Routine Service": 1200, "Tyres": 2400, "Brakes": 1400,
             "Inspection": 200, "Breakdown Repair": 3500}
PARTS = {
    "Oil Change": [("Engine oil (L)", 28.0), ("Oil filter", 45.0)],
    "Routine Service": [("Oil filter", 45.0), ("Air filter", 60.0), ("Fuel filter", 80.0)],
    "Tyres": [("Tyre", 450.0)],
    "Brakes": [("Brake pad set", 220.0), ("Brake disc", 340.0)],
    "Breakdown Repair": [("Alternator", 900.0), ("Starter motor", 750.0), ("Radiator", 650.0),
                         ("Clutch kit", 1400.0), ("Water pump", 380.0), ("Fuel pump", 520.0),
                         ("Battery", 420.0), ("Suspension arm", 480.0)],
}
DURATION = {"Oil Change": (1, 1), "Routine Service": (1, 2), "Tyres": (1, 1), "Brakes": (1, 2),
            "Inspection": (1, 1), "Breakdown Repair": (2, 6)}
TECHNICIANS = ["Khalid Al-Harbi", "Faisal Al-Zahrani", "Yusuf Al-Ghamdi", "Hassan Al-Shehri",
               "Ibrahim Al-Dosari", "Majed Al-Otaibi"]
CITIES = ["Riyadh", "Jeddah", "Dammam", "Mecca", "Medina", "Khobar", "Tabuk", "Abha", "Buraydah",
          "Hail", "Jubail", "Yanbu", "Taif"]
AREAS = ["North Site", "Industrial Area", "Warehouse 2", "Airport Road", "Port Terminal", "East Depot"]
FIRST = ["Abdullah", "Mohammed", "Ahmed", "Khalid", "Fahad", "Saud", "Turki", "Nasser", "Salman", "Omar",
         "Ali", "Hamad", "Bandar", "Faisal", "Majid", "Yazeed", "Rayan", "Talal"]
LAST = ["Al-Qahtani", "Al-Harbi", "Al-Shammari", "Al-Dosari", "Al-Otaibi", "Al-Zahrani", "Al-Ghamdi",
        "Al-Mutairi", "Al-Anazi", "Al-Subaie", "Al-Shehri", "Al-Rashid", "Al-Malki", "Al-Juhani"]
PLATE_LETTERS = "ABDEGHJKLNRSTUVXZ"

USERS = [  # demo logins, documented in README.md
    ("System Administrator", "admin@fleet.local", "Admin@123", "Administrator"),
    ("Fleet Manager", "manager@fleet.local", "Manager@123", "Fleet Manager"),
    ("Fleet Operator", "operator@fleet.local", "Operator@123", "Operator"),
]


def poisson(rng, lam):
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def plate(rng, used):
    while True:
        p = "".join(rng.choice(PLATE_LETTERS) for _ in range(3)) + f" {rng.randint(1000, 9999)}"
        if p not in used:
            used.add(p)
            return p


def make_vehicles(rng):
    vehicles, used = [], set()
    for vtype, spec in TYPES.items():
        for _ in range(spec["n"]):
            year = rng.choices(range(2016, 2026), weights=[3, 4, 5, 6, 8, 9, 10, 10, 8, 6])[0]
            age = max(0.2, 2025.8 - year - rng.random() * 0.5)
            make, model = rng.choice(spec["models"])
            vehicles.append(dict(
                vehicle_id=len(vehicles) + 1, registration_no=plate(rng, used), type=vtype, make=make,
                model=model, year=year, fuel_type=spec["fuel"], status="Active",
                start_odo=round(age * spec["annual_km"] * rng.gauss(1, 0.15), 1),
                age=age, kmpl=spec["kmpl"] * YEAR_FACTOR * rng.gauss(1, 0.06)))
    return vehicles


def make_drivers(rng, vehicles):
    n = len(vehicles) + N_SPARE_DRIVERS
    names = rng.sample([f"{f} {l}" for f in FIRST for l in LAST], n)
    lic = rng.sample(range(10_000_000, 99_999_999), n)
    drivers = []
    for i in range(n):
        expiry = TODAY + timedelta(days=rng.randint(60, 1400))
        drivers.append(dict(driver_id=i + 1, name=names[i], phone="05" + str(rng.randint(10_000_000, 99_999_999)),
                            license_no=f"10{lic[i]}", license_expiry=expiry, status="Active"))
    for d in rng.sample(drivers[:len(vehicles)], 5):  # five licences inside the 30-day warning window
        d["license_expiry"] = TODAY + timedelta(days=rng.randint(5, 28))
    return drivers


def make_assignments(rng, vehicles, drivers):
    """One permanent driver per vehicle; N_REPLACED vehicles change driver mid-year (old driver leaves)."""
    assignments = []
    replaced = set(rng.sample(range(len(vehicles)), N_REPLACED))
    spare = iter(drivers[len(vehicles):])
    for i, v in enumerate(vehicles):
        d = drivers[i]
        if i in replaced:
            switch = date(2026, rng.randint(2, 7), rng.randint(1, 28))
            new = next(spare)
            assignments.append(dict(vehicle_id=v["vehicle_id"], driver_id=d["driver_id"], start_date=START,
                                    end_date=switch - timedelta(days=1)))
            assignments.append(dict(vehicle_id=v["vehicle_id"], driver_id=new["driver_id"], start_date=switch,
                                    end_date=None))
            d["status"] = "Inactive"
        else:
            assignments.append(dict(vehicle_id=v["vehicle_id"], driver_id=d["driver_id"], start_date=START,
                                    end_date=None))
    for i, a in enumerate(assignments):
        a["assignment_id"] = i + 1
    return assignments


def service_due(vehicle, stype):
    """Days until the next service: the earlier of the day limit and the km limit at this vehicle's pace."""
    max_days, max_km = Config.SERVICE_INTERVALS[stype]
    km_per_day = TYPES[vehicle["type"]]["annual_km"] / 365
    return min(max_days, max_km / km_per_day) if max_km else max_days


def plan_maintenance(rng, vehicles):
    """Decide what service happens on which day (no odometer or cost yet); returns events per vehicle."""
    under = set(rng.sample(range(len(vehicles)), N_UNDER_MAINTENANCE))
    scheduled = set(rng.sample([i for i in range(len(vehicles)) if i not in under], 4))
    plan = []
    for i, v in enumerate(vehicles):
        spec, events = TYPES[v["type"]], []
        for stype in Config.SERVICE_INTERVALS:
            interval = service_due(v, stype)
            day = rng.uniform(0, interval)
            while day < 365:
                events.append((START + timedelta(days=int(day)), stype))
                day += interval * rng.uniform(0.9, 1.1)
        lam = (0.25 + 0.10 * v["age"]) * spec["breakdown_mult"]
        for _ in range(poisson(rng, lam)):
            events.append((START + timedelta(days=rng.randint(0, 364)), "Breakdown Repair"))
        events.sort()
        if i in under:
            events = [e for e in events if e[0] < date(2026, 9, 12)]
        kept, busy_until = [], START - timedelta(days=1)
        for day, stype in events:
            length = rng.randint(*DURATION[stype])
            if day > busy_until:
                kept.append(dict(vehicle_id=v["vehicle_id"], service_type=stype, service_date=day,
                                 days=length, status="Completed"))
                busy_until = day + timedelta(days=length)
        if i in under:
            stype = rng.choices(["Breakdown Repair", "Brakes", "Routine Service"], [5, 2, 3])[0]
            kept.append(dict(vehicle_id=v["vehicle_id"], service_type=stype,
                             service_date=date(2026, 9, rng.randint(20, 29)), days=None, status="In Progress"))
            v["status"] = "Under Maintenance"
        if i in scheduled:
            stype = rng.choice(["Routine Service", "Oil Change", "Brakes", "Inspection"])
            kept.append(dict(vehicle_id=v["vehicle_id"], service_type=stype,
                             service_date=date(2026, 10, rng.randint(6, 20)), days=None, status="Scheduled"))
        plan.append(kept)
    return plan


def blocked_days(events):
    out = set()
    for e in events:
        if e["status"] == "Completed":
            out.update(e["service_date"] + timedelta(days=k) for k in range(e["days"]))
        elif e["status"] == "In Progress":
            day = e["service_date"]
            while day <= END:
                out.add(day)
                day += timedelta(days=1)
    return out


def make_trips(rng, vehicles, plan):
    trips = []
    for v, events in zip(vehicles, plan):
        spec, blocked = TYPES[v["type"]], blocked_days(events)
        day = START + timedelta(days=rng.randint(0, 3))
        while day <= END:
            if day in blocked:
                day += timedelta(days=1)
                continue
            want = rng.choices(spec["trip_days"], spec["trip_w"])[0]
            n = 0
            while n < want and day + timedelta(days=n) <= END and day + timedelta(days=n) not in blocked:
                n += 1
            end = day + timedelta(days=n - 1)
            trips.append(dict(vehicle_id=v["vehicle_id"], start_date=day, end_date=end,
                              distance=n * spec["daily_km"] * rng.uniform(0.7, 1.3)))
            day = end + timedelta(days=1 + rng.choices([0, 1, 2, 3], spec["gap_w"])[0])
    # scale each type's distance to the report target, then name routes and pick drivers
    by_id = {v["vehicle_id"]: v for v in vehicles}
    raw = {t: sum(x["distance"] for x in trips if by_id[x["vehicle_id"]]["type"] == t) for t in TYPES}
    for x in trips:
        t = by_id[x["vehicle_id"]]["type"]
        x["distance"] = round(max(1.0, x["distance"] * TYPES[t]["dist"] / raw[t]), 1)
        if x["distance"] < 120:
            city = rng.choice(CITIES)
            x["origin"], x["destination"] = f"{city} Depot", f"{city} {rng.choice(AREAS)}"
        else:
            x["origin"], x["destination"] = rng.sample(CITIES, 2)
        x["status"] = "Completed"
    trips.sort(key=lambda x: (x["start_date"], x["vehicle_id"]))
    for i, x in enumerate(trips):
        x["trip_id"] = i + 1
    return trips


def driver_for(assignments, vehicle_id, day):
    for a in assignments:
        if a["vehicle_id"] == vehicle_id and a["start_date"] <= day and (a["end_date"] is None or day <= a["end_date"]):
            return a["driver_id"]
    raise ValueError(f"no driver for vehicle {vehicle_id} on {day}")


def make_fuel(rng, vehicles, trips):
    """Refuel after trips once enough fuel is burned. Full-tank fills restore exactly what was burned
    since the previous fill; occasional partial fills carry the remainder to the next fill."""
    fuel = []
    for v in vehicles:
        spec, price = TYPES[v["type"]], Config.FUEL_PRICES[v["fuel_type"]]
        odo, burned, threshold = v["start_odo"], 0.0, spec["tank"] * rng.uniform(0.55, 0.85)
        for t in (x for x in trips if x["vehicle_id"] == v["vehicle_id"]):
            odo += t["distance"]
            summer = SUMMER_FACTOR if t["end_date"].month in SUMMER_MONTHS else 1.0
            burned += t["distance"] / v["kmpl"] * summer
            if burned < threshold:
                continue
            full = rng.random() > 0.05
            qty = burned * rng.gauss(1, 0.015) if full else burned * rng.uniform(0.5, 0.8)
            qty = round(max(qty, 1.0), 2)
            fuel.append(dict(vehicle_id=v["vehicle_id"], date=t["end_date"], odometer=round(odo, 1), quantity=qty,
                             price_per_litre=price, total_cost=round(qty * price, 2), full_tank=full))
            burned = 0.0 if full else burned - qty
            threshold = spec["tank"] * rng.uniform(0.55, 0.85)
    fuel.sort(key=lambda f: (f["date"], f["vehicle_id"]))
    for i, f in enumerate(fuel):
        f["fuel_id"] = i + 1
    return fuel


def make_maintenance(rng, vehicles, trips, plan):
    by_vehicle, ends, cum = {}, {}, {}
    for v in vehicles:
        mine = [t for t in trips if t["vehicle_id"] == v["vehicle_id"]]
        ends[v["vehicle_id"]] = [t["end_date"] for t in mine]
        running, acc = v["start_odo"], []
        for t in mine:
            running += t["distance"]
            acc.append(running)
        cum[v["vehicle_id"]] = (v["start_odo"], acc)
        v["odometer"] = round(running, 1)
        by_vehicle[v["vehicle_id"]] = v

    def odometer_on(vid, day):
        k = bisect.bisect_left(ends[vid], day)  # trips ending before `day`
        start, acc = cum[vid]
        return round(acc[k - 1] if k else start, 1)

    records = []
    for events in plan:
        for e in events:
            v = by_vehicle[e["vehicle_id"]]
            spec = TYPES[v["type"]]
            if e["status"] == "Scheduled":
                cost, odo = 0.0, v["odometer"]
            else:
                cost = BASE_COST[e["service_type"]] * spec["maint_mult"]
                cost *= rng.uniform(0.4, 2.2) if e["service_type"] == "Breakdown Repair" else rng.uniform(0.6, 1.5)
                odo = odometer_on(e["vehicle_id"], e["service_date"])
            nxt = None
            if e["status"] == "Completed" and e["service_type"] in Config.SERVICE_INTERVALS:
                nxt = e["service_date"] + timedelta(days=int(service_due(v, e["service_type"])))
            records.append(dict(vehicle_id=e["vehicle_id"], service_type=e["service_type"],
                                service_date=e["service_date"], odometer=odo, cost=cost,
                                technician=rng.choice(TECHNICIANS), next_service_date=nxt, status=e["status"]))
    # scale each type's cost to the report target
    raw = {t: sum(r["cost"] for r in records if by_vehicle[r["vehicle_id"]]["type"] == t) for t in TYPES}
    for r in records:
        t = by_vehicle[r["vehicle_id"]]["type"]
        r["cost"] = round(r["cost"] * TYPES[t]["maint"] / raw[t], 2)
    records.sort(key=lambda r: (r["service_date"], r["vehicle_id"]))
    parts = []
    for i, r in enumerate(records):
        r["maintenance_id"] = i + 1
        if r["cost"] <= 0 or r["service_type"] not in PARTS:
            continue
        truck = by_vehicle[r["vehicle_id"]]["type"] == "Truck"
        catalogue = PARTS[r["service_type"]]
        if r["service_type"] == "Breakdown Repair":
            chosen = rng.sample(catalogue, rng.randint(1, 3))
        elif r["service_type"] == "Routine Service":
            chosen = catalogue[:2] + ([catalogue[2]] if by_vehicle[r["vehicle_id"]]["fuel_type"] == "Diesel" else [])
        else:
            chosen = catalogue
        qtys = {"Engine oil (L)": 18 if truck else 7, "Tyre": 6 if truck else 4, "Brake pad set": 2,
                "Brake disc": 2}
        budget = r["cost"] * rng.uniform(0.35, 0.7)
        weights = [rng.uniform(0.5, 1.5) for _ in chosen]
        for (name, _), w in zip(chosen, weights):
            qty = qtys.get(name, 1)
            parts.append(dict(maintenance_id=r["maintenance_id"], part_name=name, quantity=qty,
                              unit_cost=round(budget * w / sum(weights) / qty, 2)))
    for i, p in enumerate(parts):
        p["part_id"] = i + 1
    return records, parts


def generate(seed=SEED):
    rng = random.Random(seed)
    vehicles = make_vehicles(rng)
    drivers = make_drivers(rng, vehicles)
    assignments = make_assignments(rng, vehicles, drivers)
    plan = plan_maintenance(rng, vehicles)
    trips = make_trips(rng, vehicles, plan)
    for t in trips:
        t["driver_id"] = driver_for(assignments, t["vehicle_id"], t["start_date"])
    fuel = make_fuel(rng, vehicles, trips)
    maintenance, parts = make_maintenance(rng, vehicles, trips, plan)
    return dict(vehicles=vehicles, drivers=drivers, assignments=assignments, trips=trips, fuel=fuel,
                maintenance=maintenance, parts=parts)


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


def summary(data):
    v = {x["vehicle_id"]: x for x in data["vehicles"]}
    dist = sum(t["distance"] for t in data["trips"])
    litres = sum(f["quantity"] for f in data["fuel"])
    fuel_cost = sum(f["total_cost"] for f in data["fuel"])
    maint = sum(m["cost"] for m in data["maintenance"])
    active_days = sum((t["end_date"] - t["start_date"]).days + 1 for t in data["trips"])
    status = {s: sum(1 for x in data["vehicles"] if x["status"] == s) for s in ("Active", "Under Maintenance")}
    lines = [
        f"vehicles {len(v)} {status} | drivers {len(data['drivers'])} | trips {len(data['trips'])} | "
        f"fuel records {len(data['fuel'])} | maintenance {len(data['maintenance'])} | parts {len(data['parts'])}",
        f"distance      {dist:>12,.0f} km      (report 1,252,028)",
        f"fuel          {litres:>12,.0f} L       (report 264,036)",
        f"fuel cost     {fuel_cost:>12,.0f} SAR     (report 474,921)",
        f"maint cost    {maint:>12,.0f} SAR     (report 171,150)",
        f"efficiency    {dist / litres:>12.2f} km/L    (report 4.74)",
        f"cost per km   {(fuel_cost + maint) / dist:>12.3f} SAR/km  (report 0.516)",
        f"utilisation   {100 * active_days / (len(v) * 365):>12.0f} %        (report 67)",
        "", "type     veh   distance(km)   fuel(L)  km/L  cost/km",
    ]
    for t in TYPES:
        ids = {i for i, x in v.items() if x["type"] == t}
        d = sum(x["distance"] for x in data["trips"] if x["vehicle_id"] in ids)
        l = sum(x["quantity"] for x in data["fuel"] if x["vehicle_id"] in ids)
        c = sum(x["total_cost"] for x in data["fuel"] if x["vehicle_id"] in ids) + \
            sum(x["cost"] for x in data["maintenance"] if x["vehicle_id"] in ids)
        lines.append(f"{t:<8} {len(ids):>3} {d:>14,.0f} {l:>9,.0f} {d / l:>5.2f} {c / d:>8.3f}")
    months = {}
    for t in data["trips"]:
        months.setdefault(t["end_date"].strftime("%Y-%m"), [0.0, 0.0])[0] += t["distance"]
    for f in data["fuel"]:
        months.setdefault(f["date"].strftime("%Y-%m"), [0.0, 0.0])[1] += f["quantity"]
    lines += ["", "month     distance(km)   fuel(L)"] + [
        f"{m}  {d:>12,.0f} {l:>9,.0f}" for m, (d, l) in sorted(months.items())]
    return "\n".join(lines)


def load(data, db_name=None):
    from sqlalchemy import text
    from werkzeug.security import generate_password_hash

    from app import create_app, models
    from app.config import Config as Cfg, TestConfig
    from app.extensions import db

    app = create_app(TestConfig if db_name == "test" else Cfg)
    users = [dict(user_id=i + 1, name=n, email=e, password_hash=generate_password_hash(p), role=r)
             for i, (n, e, p, r) in enumerate(USERS)]
    vehicles = [{k: x[k] for k in ("vehicle_id", "registration_no", "type", "make", "model", "year",
                                   "fuel_type", "odometer", "status")} for x in data["vehicles"]]
    trips = [{k: v for k, v in t.items()} for t in data["trips"]]
    fuel = data["fuel"]
    maintenance = [{k: v for k, v in m.items()} for m in data["maintenance"]]
    with app.app_context():
        db.session.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for table in ("maintenance_parts", "maintenance_records", "fuel_records", "trips",
                      "vehicle_assignments", "drivers", "vehicles", "users"):
            db.session.execute(text(f"TRUNCATE TABLE {table}"))
        db.session.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        for model, rows in ((models.User, users), (models.Vehicle, vehicles), (models.Driver, data["drivers"]),
                            (models.VehicleAssignment, data["assignments"]), (models.Trip, trips),
                            (models.FuelRecord, fuel), (models.MaintenanceRecord, maintenance),
                            (models.MaintenancePart, data["parts"])):
            columns = {c.key for c in model.__table__.columns}
            db.session.bulk_insert_mappings(model, [{k: v for k, v in r.items() if k in columns} for r in rows])
        db.session.commit()
        return app.config["SQLALCHEMY_DATABASE_URI"].rsplit("/", 1)[1]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print totals without touching the database")
    ap.add_argument("--db", choices=["dev", "test"], default="dev")
    args = ap.parse_args()
    data = generate()
    print(summary(data))
    print(f"\nfingerprint {fingerprint(data)}")
    if not args.dry_run:
        print(f"loaded into {load(data, args.db)}")
