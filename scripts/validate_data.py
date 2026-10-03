"""Check the seeded data against the business rules with direct SQL. Exits non-zero on any violation."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app import create_app
from app.extensions import db

CHECKS = {
    "trips overlapping on the same vehicle": """
        SELECT COUNT(*) FROM trips a JOIN trips b
          ON a.vehicle_id = b.vehicle_id AND a.trip_id < b.trip_id
         AND a.start_date <= b.end_date AND a.end_date >= b.start_date""",
    "trips overlapping for the same driver": """
        SELECT COUNT(*) FROM trips a JOIN trips b
          ON a.driver_id = b.driver_id AND a.trip_id < b.trip_id
         AND a.start_date <= b.end_date AND a.end_date >= b.start_date""",
    "trips with licence expired on the end date": """
        SELECT COUNT(*) FROM trips t JOIN drivers d USING (driver_id) WHERE d.license_expiry < t.end_date""",
    "trips on a vehicle during a service": """
        SELECT COUNT(*) FROM trips t JOIN maintenance_records m USING (vehicle_id)
         WHERE m.status = 'In Progress' AND t.end_date >= m.service_date""",
    "fuel odometer going backwards": """
        SELECT COUNT(*) FROM fuel_records a JOIN fuel_records b
          ON a.vehicle_id = b.vehicle_id AND a.date < b.date AND a.odometer > b.odometer""",
    "fuel total_cost != quantity * price": """
        SELECT COUNT(*) FROM fuel_records WHERE ABS(total_cost - quantity * price_per_litre) > 0.01""",
    "vehicle odometer below its latest record": """
        SELECT COUNT(*) FROM vehicles v WHERE v.odometer < COALESCE(
            (SELECT MAX(odometer) FROM fuel_records WHERE vehicle_id = v.vehicle_id), 0)""",
    "vehicles Under Maintenance without an In Progress service": """
        SELECT COUNT(*) FROM vehicles v WHERE v.status = 'Under Maintenance' AND NOT EXISTS (
            SELECT 1 FROM maintenance_records m WHERE m.vehicle_id = v.vehicle_id AND m.status = 'In Progress')""",
    "trips driven by a driver not assigned on the start date": """
        SELECT COUNT(*) FROM trips t WHERE NOT EXISTS (
            SELECT 1 FROM vehicle_assignments a WHERE a.vehicle_id = t.vehicle_id AND a.driver_id = t.driver_id
             AND a.start_date <= t.start_date AND (a.end_date IS NULL OR a.end_date >= t.start_date))""",
}

app = create_app()
bad = 0
with app.app_context():
    for name, sql in CHECKS.items():
        n = db.session.execute(text(sql)).scalar()
        bad += n > 0
        print(f"{'FAIL' if n else 'ok  '} {name}: {n}")
    warn = db.session.execute(text(
        "SELECT COUNT(*) FROM drivers WHERE status='Active' AND license_expiry BETWEEN '2026-10-03' "
        "AND DATE_ADD('2026-10-03', INTERVAL 30 DAY)")).scalar()
    print(f"info drivers with a licence expiring within 30 days of 2026-10-03: {warn}")
sys.exit(1 if bad else 0)
