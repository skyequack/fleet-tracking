from flask import Blueprint, jsonify, request
from sqlalchemy import func, select

from ..clock import today
from ..constants import ADMIN, MANAGER, OPEN_SERVICE_STATUSES
from ..errors import ApiError
from ..extensions import db
from ..locking import begin_write, lock_row
from ..models import Driver, MaintenanceRecord, Trip, Vehicle, VehicleAssignment
from ..pagination import paginated
from ..security import current_user, login_required, roles_required
from ..services import rules
from ..validation import Field, decimal, integer, iso_date, json_body, one_of, parse, text

bp = Blueprint("trips", __name__, url_prefix="/api/trips")

TRIP_STATUSES = tuple(rules.TRIP_TRANSITIONS)
ID = integer(1, 2_147_483_647)
SCHEDULE_FIELDS = {"vehicle_id", "driver_id", "start_date", "end_date"}


def trip_json(t):
    return {"trip_id": t.trip_id, "vehicle_id": t.vehicle_id, "registration_no": t.vehicle.registration_no,
            "driver_id": t.driver_id, "driver_name": t.driver.name, "origin": t.origin,
            "destination": t.destination, "distance": float(t.distance), "start_date": t.start_date.isoformat(),
            "end_date": t.end_date.isoformat(), "status": t.status}


def _spec():
    # status is not accepted on create: a new trip is always Planned
    return {
        "vehicle_id": Field(ID), "driver_id": Field(ID),
        "origin": Field(text(100)), "destination": Field(text(100)),
        "distance": Field(decimal(0, 99_999, 1)),
        "start_date": Field(iso_date), "end_date": Field(iso_date),
    }


def _check(vehicle, driver, start, end, exclude_trip_id=None):
    """Gather the facts for the six checks (8.3) and let the pure rule decide. Raises 422 with the reason."""
    def overlapping(column, value):
        stmt = select(Trip.trip_id).where(column == value, Trip.status != "Cancelled",
                                          Trip.start_date <= end, Trip.end_date >= start).order_by(Trip.trip_id)
        if exclude_trip_id is not None:
            stmt = stmt.where(Trip.trip_id != exclude_trip_id)
        return list(db.session.scalars(stmt))

    assigned = db.session.scalar(select(func.count()).select_from(VehicleAssignment).where(
        VehicleAssignment.vehicle_id == vehicle.vehicle_id, VehicleAssignment.driver_id == driver.driver_id,
        VehicleAssignment.start_date <= start,
        (VehicleAssignment.end_date.is_(None)) | (VehicleAssignment.end_date >= start)))
    services = db.session.scalar(select(func.count()).select_from(MaintenanceRecord).where(
        MaintenanceRecord.vehicle_id == vehicle.vehicle_id,
        MaintenanceRecord.status.in_(OPEN_SERVICE_STATUSES),
        MaintenanceRecord.service_date.between(start, end)))
    rejection = rules.trip_rejection(
        start=start, end=end, today=today(), vehicle_status=vehicle.status, driver_status=driver.status,
        license_expiry=driver.license_expiry, driver_assigned=bool(assigned),
        vehicle_conflicts=overlapping(Trip.vehicle_id, vehicle.vehicle_id),
        driver_conflicts=overlapping(Trip.driver_id, driver.driver_id), blocking_services=services)
    if rejection:
        raise ApiError(422, rejection[1], rejection[0])


def _lock_pair(vehicle_id, driver_id):
    """Vehicle row first, then driver row, always in that order (8.8)."""
    vehicle = lock_row(Vehicle, vehicle_id)
    if vehicle is None:
        raise ApiError(422, "Vehicle does not exist", "vehicle_id")
    driver = lock_row(Driver, driver_id)
    if driver is None:
        raise ApiError(422, "Driver does not exist", "driver_id")
    return vehicle, driver


@bp.get("")
@login_required
def list_trips():
    stmt = select(Trip)
    status = request.args.get("status")
    if status:
        if status not in TRIP_STATUSES:
            raise ApiError(400, "status must be one of: " + ", ".join(TRIP_STATUSES), "status")
        stmt = stmt.where(Trip.status == status)
    for param in ("vehicle_id", "driver_id"):
        raw = request.args.get(param)
        if raw:
            try:
                stmt = stmt.where(getattr(Trip, param) == int(raw))
            except ValueError:
                raise ApiError(400, f"{param} must be a whole number", param)
    # ?from= and ?to= select trips that overlap the window
    for param, clause in (("from", lambda d: Trip.end_date >= d), ("to", lambda d: Trip.start_date <= d)):
        raw = request.args.get(param)
        if raw:
            try:
                stmt = stmt.where(clause(iso_date(raw)))
            except ValueError as e:
                raise ApiError(400, f"{param} {e}", param)
    return jsonify(paginated(stmt.order_by(Trip.start_date.desc(), Trip.trip_id.desc()), trip_json))


@bp.post("")
@login_required  # every role may create trips (matrix); the six checks decide whether this one is allowed
def create_trip():
    values = parse(json_body(), _spec())
    if values["end_date"] < values["start_date"]:
        raise ApiError(422, "end_date must not be before start_date", "end_date")
    user_id = current_user().user_id  # read before the transaction restarts
    begin_write()
    vehicle, driver = _lock_pair(values["vehicle_id"], values["driver_id"])
    _check(vehicle, driver, values["start_date"], values["end_date"])
    trip = Trip(**values, status="Planned", created_by=user_id)
    db.session.add(trip)
    db.session.commit()
    return jsonify(trip_json(trip)), 201


@bp.put("/<int:trip_id>")
@roles_required(ADMIN, MANAGER)
def update_trip(trip_id):
    spec = {**_spec(), "status": Field(one_of(TRIP_STATUSES))}
    values = parse(json_body(), spec, partial=True)
    new_status = values.pop("status", None)
    begin_write()
    trip = lock_row(Trip, trip_id)  # trip, then vehicle, then driver: POST never waits on an existing trip row
    if trip is None:
        raise ApiError(404, "Trip not found")
    edits = {k: v for k, v in values.items() if getattr(trip, k) != v}
    if edits and trip.status != "Planned":
        raise ApiError(422, f"A {trip.status} trip can no longer be edited")
    vehicle, driver = _lock_pair(values.get("vehicle_id", trip.vehicle_id), values.get("driver_id", trip.driver_id))
    start, end = values.get("start_date", trip.start_date), values.get("end_date", trip.end_date)
    if end < start:
        raise ApiError(422, "end_date must not be before start_date", "end_date")
    if edits.keys() & SCHEDULE_FIELDS:
        _check(vehicle, driver, start, end, exclude_trip_id=trip.trip_id)
    if new_status and new_status != trip.status:
        error = rules.trip_transition_error(trip.status, new_status)
        if error is None and new_status == "In Progress":
            error = rules.trip_start_block(vehicle.status, driver.status)
        if error:
            raise ApiError(422, error, "status")
    for name, value in edits.items():
        setattr(trip, name, value)
    if new_status:
        trip.status = new_status
    db.session.commit()
    return jsonify(trip_json(trip))
