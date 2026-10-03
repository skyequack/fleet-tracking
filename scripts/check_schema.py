"""Phase 1 day 1 check: query all 8 tables via the ORM and prove the DB rejects a duplicate registration."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy.exc import DBAPIError, IntegrityError

from app import create_app, models
from app.extensions import db

app = create_app()
with app.app_context():
    for m in (models.User, models.Vehicle, models.Driver, models.VehicleAssignment, models.Trip,
              models.FuelRecord, models.MaintenanceRecord, models.MaintenancePart):
        print(f"{m.__tablename__:22} rows={m.query.count()}")

    models.Vehicle.query.filter(models.Vehicle.registration_no.like("TEST-%")).delete()
    db.session.commit()
    db.session.add(models.Vehicle(registration_no="TEST-0001", type="Van", make="X", model="Y", year=2022))
    db.session.commit()
    try:
        db.session.add(models.Vehicle(registration_no="TEST-0001", type="Van", make="X", model="Y", year=2022))
        db.session.commit()
        print("FAIL: duplicate registration accepted")
    except IntegrityError as e:
        db.session.rollback()
        print("OK: duplicate registration rejected ->", str(e.orig)[:70])
    try:
        db.session.add(models.Vehicle(registration_no="TEST-0002", type="Bus", make="X", model="Y", year=2022))
        db.session.commit()
        print("FAIL: invalid type accepted")
    except DBAPIError as e:
        db.session.rollback()
        print("OK: CHECK constraint rejected invalid type ->", str(e.orig)[:70])
    models.Vehicle.query.filter_by(registration_no="TEST-0001").delete()
    db.session.commit()
    print("cleanup done; vehicles =", models.Vehicle.query.count())
