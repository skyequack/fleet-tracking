from flask import Blueprint, jsonify, request
from sqlalchemy import func, or_, select

from ..clock import today
from ..constants import (ADMIN, FUEL_TYPES, OPEN_SERVICE_STATUSES, OPEN_TRIP_STATUSES, VEHICLE_STATUSES,
                         VEHICLE_TYPES)
from ..errors import ApiError
from ..extensions import db
from ..locking import begin_write, lock_row
from ..models import MaintenanceRecord, Trip, Vehicle
from ..pagination import paginated
from ..security import login_required, roles_required
from ..services import rules
from ..validation import Field, decimal, integer, json_body, like_pattern, one_of, parse, text

bp = Blueprint("vehicles", __name__, url_prefix="/api/vehicles")


def vehicle_json(v):
    return {"vehicle_id": v.vehicle_id, "registration_no": v.registration_no, "type": v.type, "make": v.make,
            "model": v.model, "year": v.year, "fuel_type": v.fuel_type, "odometer": float(v.odometer),
            "status": v.status}


def _spec():
    # status and odometer are not bound from the body: status follows deactivate/reactivate and maintenance,
    # odometer follows fuel and maintenance entries (8.7). A new vehicle may start with a reading.
    return {
        "registration_no": Field(text(20, upper=True)),
        "type": Field(one_of(VEHICLE_TYPES)),
        "make": Field(text(50)),
        "model": Field(text(50)),
        "year": Field(integer(1980, today().year + 1)),
        "fuel_type": Field(one_of(FUEL_TYPES), required=False),
        "odometer": Field(decimal(0, 9_999_999, 1), required=False),
    }


def _lock(vehicle_id):
    """Fetch the vehicle row with a lock, so deactivation and other writes serialise (8.8)."""
    begin_write()
    vehicle = lock_row(Vehicle, vehicle_id)
    if vehicle is None:
        raise ApiError(404, "Vehicle not found")
    return vehicle


def _count(model, vehicle_id, statuses):
    return db.session.scalar(select(func.count()).select_from(model).where(
        model.vehicle_id == vehicle_id, model.status.in_(statuses)))


@bp.get("")
@login_required
def list_vehicles():
    stmt = select(Vehicle)
    q = request.args.get("q", "").strip()
    if q:
        like = like_pattern(q)
        stmt = stmt.where(or_(Vehicle.registration_no.like(like), Vehicle.make.like(like),
                              Vehicle.model.like(like)))
    for param, column, allowed in (("status", Vehicle.status, VEHICLE_STATUSES),
                                   ("type", Vehicle.type, VEHICLE_TYPES)):
        value = request.args.get(param)
        if value:
            if value not in allowed:
                raise ApiError(400, f"{param} must be one of: " + ", ".join(allowed), param)
            stmt = stmt.where(column == value)
    return jsonify(paginated(stmt.order_by(Vehicle.vehicle_id), vehicle_json))


@bp.post("")
@roles_required(ADMIN)
def create_vehicle():
    values = parse(json_body(), _spec())
    vehicle = Vehicle(**values)
    db.session.add(vehicle)
    db.session.commit()
    return jsonify(vehicle_json(vehicle)), 201


@bp.put("/<int:vehicle_id>")
@roles_required(ADMIN)
def update_vehicle(vehicle_id):
    vehicle = _lock(vehicle_id)
    spec = _spec()
    spec.pop("odometer")  # the odometer is only set when the vehicle is created
    for name, value in parse(json_body(), spec, partial=True).items():
        setattr(vehicle, name, value)
    db.session.commit()
    return jsonify(vehicle_json(vehicle))


@bp.delete("/<int:vehicle_id>")
@roles_required(ADMIN)
def deactivate_vehicle(vehicle_id):
    vehicle = _lock(vehicle_id)
    if vehicle.status != "Inactive":
        reason = rules.vehicle_deactivation_block(_count(Trip, vehicle_id, OPEN_TRIP_STATUSES),
                                                  _count(MaintenanceRecord, vehicle_id, OPEN_SERVICE_STATUSES))
        if reason:
            raise ApiError(422, reason)
        vehicle.status = "Inactive"
        db.session.commit()
    return jsonify(vehicle_json(vehicle))


@bp.post("/<int:vehicle_id>/reactivate")
@roles_required(ADMIN)
def reactivate_vehicle(vehicle_id):
    vehicle = _lock(vehicle_id)
    if vehicle.status == "Inactive":
        # a service may still be running (8.5): the vehicle returns to Under Maintenance, not Active
        running = _count(MaintenanceRecord, vehicle_id, ("In Progress",))
        vehicle.status = "Under Maintenance" if running else "Active"
        db.session.commit()
    return jsonify(vehicle_json(vehicle))
