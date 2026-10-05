"""ORM models. The schema is owned by sql/schema.sql; these map onto it."""
from .extensions import db


class User(db.Model):
    __tablename__ = "users"
    user_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(10), nullable=False, default="Active", server_default="Active")


class Vehicle(db.Model):
    __tablename__ = "vehicles"
    vehicle_id = db.Column(db.Integer, primary_key=True)
    registration_no = db.Column(db.String(20), nullable=False, unique=True)
    type = db.Column(db.String(20), nullable=False)
    make = db.Column(db.String(50), nullable=False)
    model = db.Column(db.String(50), nullable=False)
    year = db.Column(db.SmallInteger, nullable=False)
    fuel_type = db.Column(db.String(10), nullable=False, default="Diesel")
    odometer = db.Column(db.Numeric(10, 1), nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default="Active")


class Driver(db.Model):
    __tablename__ = "drivers"
    driver_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    license_no = db.Column(db.String(30), nullable=False, unique=True)
    license_expiry = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(10), nullable=False, default="Active")


class VehicleAssignment(db.Model):
    __tablename__ = "vehicle_assignments"
    assignment_id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.vehicle_id"), nullable=False)
    driver_id = db.Column(db.Integer, db.ForeignKey("drivers.driver_id"), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date)
    vehicle = db.relationship("Vehicle", lazy="joined")
    driver = db.relationship("Driver", lazy="joined")


class Trip(db.Model):
    __tablename__ = "trips"
    trip_id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.vehicle_id"), nullable=False)
    driver_id = db.Column(db.Integer, db.ForeignKey("drivers.driver_id"), nullable=False)
    origin = db.Column(db.String(100), nullable=False)
    destination = db.Column(db.String(100), nullable=False)
    distance = db.Column(db.Numeric(8, 1), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(15), nullable=False, default="Planned")
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    created_by = db.Column(db.Integer, db.ForeignKey("users.user_id"))
    vehicle = db.relationship("Vehicle", lazy="joined")
    driver = db.relationship("Driver", lazy="joined")


class FuelRecord(db.Model):
    __tablename__ = "fuel_records"
    fuel_id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.vehicle_id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    odometer = db.Column(db.Numeric(10, 1), nullable=False)
    quantity = db.Column(db.Numeric(8, 2), nullable=False)
    price_per_litre = db.Column(db.Numeric(6, 2), nullable=False)
    total_cost = db.Column(db.Numeric(10, 2), nullable=False)
    full_tank = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    created_by = db.Column(db.Integer, db.ForeignKey("users.user_id"))


class MaintenanceRecord(db.Model):
    __tablename__ = "maintenance_records"
    maintenance_id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.vehicle_id"), nullable=False)
    service_type = db.Column(db.String(30), nullable=False)
    service_date = db.Column(db.Date, nullable=False)
    odometer = db.Column(db.Numeric(10, 1), nullable=False)
    cost = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    technician = db.Column(db.String(100))
    next_service_date = db.Column(db.Date)
    status = db.Column(db.String(15), nullable=False, default="Scheduled")
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    created_by = db.Column(db.Integer, db.ForeignKey("users.user_id"))
    parts = db.relationship("MaintenancePart", backref="maintenance", lazy=True)


class MaintenancePart(db.Model):
    __tablename__ = "maintenance_parts"
    part_id = db.Column(db.Integer, primary_key=True)
    maintenance_id = db.Column(db.Integer, db.ForeignKey("maintenance_records.maintenance_id"), nullable=False)
    part_name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_cost = db.Column(db.Numeric(10, 2), nullable=False)
