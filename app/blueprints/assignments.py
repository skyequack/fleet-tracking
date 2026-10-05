from flask import Blueprint, jsonify, request
from sqlalchemy import select

from ..constants import ADMIN, MANAGER
from ..errors import ApiError
from ..extensions import db
from ..locking import begin_write, lock_row
from ..models import Driver, Vehicle, VehicleAssignment
from ..pagination import paginated
from ..security import roles_required
from ..services import rules
from ..validation import Field, integer, iso_date, json_body, parse

bp = Blueprint("assignments", __name__, url_prefix="/api/assignments")

ID = integer(1, 2_147_483_647)


def assignment_json(a):
    return {"assignment_id": a.assignment_id, "vehicle_id": a.vehicle_id,
            "registration_no": a.vehicle.registration_no, "driver_id": a.driver_id, "driver_name": a.driver.name,
            "start_date": a.start_date.isoformat(), "end_date": a.end_date.isoformat() if a.end_date else None}


def _refuse_overlap(vehicle_id, start, end, exclude_id=None):
    stmt = select(VehicleAssignment.assignment_id, VehicleAssignment.start_date, VehicleAssignment.end_date).where(
        VehicleAssignment.vehicle_id == vehicle_id)
    if exclude_id is not None:
        stmt = stmt.where(VehicleAssignment.assignment_id != exclude_id)
    clash = rules.assignment_conflict(start, end, [tuple(r) for r in db.session.execute(stmt)])
    if clash:
        raise ApiError(422, f"This vehicle already has assignment #{clash} in that period", "start_date")


@bp.get("")
@roles_required(ADMIN, MANAGER)
def list_assignments():
    stmt = select(VehicleAssignment)
    for param in ("vehicle_id", "driver_id"):
        raw = request.args.get(param)
        if raw:
            try:
                stmt = stmt.where(getattr(VehicleAssignment, param) == int(raw))
            except ValueError:
                raise ApiError(400, f"{param} must be a whole number", param)
    return jsonify(paginated(stmt.order_by(VehicleAssignment.start_date.desc(),
                                           VehicleAssignment.assignment_id.desc()), assignment_json))


@bp.post("")
@roles_required(ADMIN, MANAGER)
def create_assignment():
    values = parse(json_body(), {"vehicle_id": Field(ID), "driver_id": Field(ID), "start_date": Field(iso_date),
                                 "end_date": Field(iso_date, required=False, nullable=True)})
    start, end = values["start_date"], values.get("end_date")
    if end and end < start:
        raise ApiError(422, "end_date must not be before start_date", "end_date")
    begin_write()
    vehicle = lock_row(Vehicle, values["vehicle_id"])
    if vehicle is None:
        raise ApiError(422, "Vehicle does not exist", "vehicle_id")
    driver = lock_row(Driver, values["driver_id"])
    if driver is None:
        raise ApiError(422, "Driver does not exist", "driver_id")
    if vehicle.status == "Inactive":
        raise ApiError(422, "Vehicle is inactive", "vehicle_id")
    if driver.status != "Active":
        raise ApiError(422, "Driver is not active", "driver_id")
    _refuse_overlap(vehicle.vehicle_id, start, end)
    assignment = VehicleAssignment(**values)
    db.session.add(assignment)
    db.session.commit()
    return jsonify(assignment_json(assignment)), 201


@bp.put("/<int:assignment_id>")
@roles_required(ADMIN, MANAGER)
def end_assignment(assignment_id):
    """Set end_date: this is how a driver is taken off a vehicle."""
    values = parse(json_body(), {"end_date": Field(iso_date)})
    begin_write()
    assignment = lock_row(VehicleAssignment, assignment_id)
    if assignment is None:
        raise ApiError(404, "Assignment not found")
    lock_row(Vehicle, assignment.vehicle_id)
    if values["end_date"] < assignment.start_date:
        raise ApiError(422, "end_date must not be before start_date", "end_date")
    _refuse_overlap(assignment.vehicle_id, assignment.start_date, values["end_date"], assignment_id)
    assignment.end_date = values["end_date"]
    db.session.commit()
    return jsonify(assignment_json(assignment))
