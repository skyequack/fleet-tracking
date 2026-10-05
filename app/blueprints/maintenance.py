from datetime import timedelta
from decimal import Decimal

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import aliased, selectinload

from ..clock import today
from ..constants import ADMIN, MANAGER, OPEN_SERVICE_STATUSES, OPERATOR
from ..errors import ApiError
from ..extensions import db
from ..locking import begin_write, lock_row
from ..models import MaintenancePart, MaintenanceRecord, Trip, Vehicle
from ..pagination import paginated
from ..security import current_user, login_required, roles_required
from ..services import rules
from ..validation import Field, decimal, integer, iso_date, json_body, one_of, parse, text

bp = Blueprint("maintenance", __name__, url_prefix="/api/maintenance")

M = MaintenanceRecord
SERVICE_TYPES = ("Routine Service", "Oil Change", "Tyres", "Brakes", "Breakdown Repair", "Inspection")
STATUSES = tuple(rules.MAINTENANCE_TRANSITIONS)
MONEY = decimal(0, 99_999_999, 2)
ODOMETER = decimal(0, 9_999_999, 1)
READING_STATUSES = ("In Progress", "Completed")  # Scheduled rows hold a placeholder, not a reading


def record_json(m):
    return {"maintenance_id": m.maintenance_id, "vehicle_id": m.vehicle_id, "service_type": m.service_type,
            "service_date": m.service_date.isoformat(), "odometer": float(m.odometer), "cost": float(m.cost),
            "technician": m.technician,
            "next_service_date": m.next_service_date.isoformat() if m.next_service_date else None,
            "status": m.status,
            "parts": [{"part_id": p.part_id, "part_name": p.part_name, "quantity": p.quantity,
                       "unit_cost": float(p.unit_cost)} for p in m.parts]}


def _parts_total(maintenance_id):
    return db.session.scalar(select(func.coalesce(func.sum(MaintenancePart.quantity * MaintenancePart.unit_cost), 0))
                             .where(MaintenancePart.maintenance_id == maintenance_id))


def _lock_record(maintenance_id):
    """Vehicle row first, then the record (8.8). The record's vehicle is found, the snapshot dropped, then locked."""
    begin_write()
    vehicle_id = db.session.scalar(select(M.vehicle_id).where(M.maintenance_id == maintenance_id))
    if vehicle_id is None:
        raise ApiError(404, "Maintenance record not found")
    begin_write()  # the read above pinned a snapshot; drop it so the checks after the locks see fresh rows
    vehicle = lock_row(Vehicle, vehicle_id)
    record = lock_row(M, maintenance_id)
    return vehicle, record


def _apply_reading(record, vehicle, reading):
    """8.7: bracket a real reading among the vehicle's other real readings, then raise the master odometer."""
    others = (M.vehicle_id == vehicle.vehicle_id, M.status.in_(READING_STATUSES),
              M.maintenance_id != record.maintenance_id)
    order_key = (M.service_date, M.maintenance_id)
    previous = db.session.scalar(select(M.odometer).where(*others, M.service_date <= record.service_date)
                                 .order_by(M.service_date.desc(), M.maintenance_id.desc()).limit(1))
    following = db.session.scalar(select(M.odometer).where(*others, M.service_date > record.service_date)
                                  .order_by(*order_key).limit(1))
    error = rules.odometer_bracket_error(reading, previous, following)
    if error:
        raise ApiError(422, error, "odometer")
    record.odometer = reading
    vehicle.odometer = max(vehicle.odometer, reading)


def _avg_daily_km(vehicle_id):
    """Average km per day of one vehicle over the last AVG_KM_WINDOW_DAYS, from Completed trips; None without history."""
    days, now = current_app.config["AVG_KM_WINDOW_DAYS"], today()
    total = db.session.scalar(select(func.coalesce(func.sum(Trip.distance), 0)).where(
        Trip.vehicle_id == vehicle_id, Trip.status == "Completed",
        Trip.end_date > now - timedelta(days=days), Trip.end_date <= now))
    return float(total) / days if total else None


@bp.get("")
@login_required
def list_maintenance():
    user = current_user()
    stmt = select(M).options(selectinload(M.parts))
    if user.role == OPERATOR:  # Operators see open records only (R8)
        stmt = stmt.where(M.status.in_(OPEN_SERVICE_STATUSES))
    elif user.role not in (ADMIN, MANAGER):
        raise ApiError(403, "You do not have permission for this action")
    for param, column, allowed in (("status", M.status, STATUSES), ("service_type", M.service_type, SERVICE_TYPES)):
        value = request.args.get(param)
        if value:
            if value not in allowed:
                raise ApiError(400, f"{param} must be one of: " + ", ".join(allowed), param)
            stmt = stmt.where(column == value)
    raw = request.args.get("vehicle_id")
    if raw:
        try:
            stmt = stmt.where(M.vehicle_id == int(raw))
        except ValueError:
            raise ApiError(400, "vehicle_id must be a whole number", "vehicle_id")
    return jsonify(paginated(stmt.order_by(M.service_date.desc(), M.maintenance_id.desc()), record_json))


@bp.get("/upcoming")
@login_required
def upcoming():
    """Scheduled services, plus services that fall due (or are overdue) within ?days= (default 30)."""
    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        raise ApiError(400, "days must be a whole number", "days")
    if not 0 <= days <= 365:
        raise ApiError(400, "days must be between 0 and 365", "days")
    now = today()
    scheduled = db.session.execute(
        select(M, Vehicle.registration_no).join(Vehicle, Vehicle.vehicle_id == M.vehicle_id)
        .where(M.status == "Scheduled").order_by(M.service_date, M.maintenance_id)).all()

    # due: the latest completed service of each (vehicle, type) whose next date is near, unless that type is
    # already booked or running for the vehicle
    later = aliased(M)
    newer = and_(later.vehicle_id == M.vehicle_id, later.service_type == M.service_type)
    due = db.session.execute(
        select(M, Vehicle.registration_no).join(Vehicle, Vehicle.vehicle_id == M.vehicle_id)
        .where(M.status == "Completed", M.next_service_date.is_not(None),
               M.next_service_date <= now + timedelta(days=days), Vehicle.status != "Inactive",
               ~exists().where(newer, later.status == "Completed",
                               or_(later.service_date > M.service_date,
                                   and_(later.service_date == M.service_date,
                                        later.maintenance_id > M.maintenance_id))),
               ~exists().where(newer, later.status.in_(OPEN_SERVICE_STATUSES)))
        .order_by(M.next_service_date, M.maintenance_id)).all()
    return jsonify(
        scheduled=[{"maintenance_id": m.maintenance_id, "vehicle_id": m.vehicle_id, "registration_no": reg,
                    "service_type": m.service_type, "service_date": m.service_date.isoformat()}
                   for m, reg in scheduled],
        due=[{"maintenance_id": m.maintenance_id, "vehicle_id": m.vehicle_id, "registration_no": reg,
              "service_type": m.service_type, "next_service_date": m.next_service_date.isoformat(),
              "overdue": m.next_service_date < now} for m, reg in due])


@bp.post("")
@roles_required(ADMIN, OPERATOR)
def create_record():
    values = parse(json_body(), {
        "vehicle_id": Field(integer(1, 2_147_483_647)), "service_type": Field(one_of(SERVICE_TYPES)),
        "service_date": Field(iso_date), "odometer": Field(ODOMETER, required=False),
        "cost": Field(MONEY, required=False), "technician": Field(text(100), required=False, nullable=True)})
    user_id = current_user().user_id  # read before the transaction restarts
    begin_write()
    vehicle = lock_row(Vehicle, values["vehicle_id"])
    if vehicle is None:
        raise ApiError(422, "Vehicle does not exist", "vehicle_id")
    if vehicle.status == "Inactive":
        raise ApiError(422, "Vehicle is inactive", "vehicle_id")
    reading = values.pop("odometer", None)
    record = M(**values, odometer=reading if reading is not None else vehicle.odometer, status="Scheduled",
               created_by=user_id)
    db.session.add(record)
    db.session.flush()
    if reading is not None:  # a supplied reading is real: bracket it and raise the master odometer
        _apply_reading(record, vehicle, reading)
    db.session.commit()
    return jsonify(record_json(record)), 201


@bp.post("/<int:maintenance_id>/parts")
@roles_required(ADMIN, OPERATOR)
def add_part(maintenance_id):
    values = parse(json_body(), {"part_name": Field(text(100)), "quantity": Field(integer(1, 100_000)),
                                 "unit_cost": Field(MONEY)})
    _, record = _lock_record(maintenance_id)
    if record.status == "Completed":
        raise ApiError(422, "Parts cannot be added to a completed service")
    total = _parts_total(maintenance_id) + values["quantity"] * values["unit_cost"]
    error = rules.parts_total_error(total, record.cost)
    if error:
        raise ApiError(422, error, "unit_cost")
    db.session.add(MaintenancePart(maintenance_id=maintenance_id, **values))
    db.session.commit()
    return jsonify(record_json(record)), 201


@bp.put("/<int:maintenance_id>/status")
@roles_required(ADMIN, OPERATOR)
def advance(maintenance_id):
    """Scheduled -> In Progress -> Completed. Optional `odometer`, `cost` and `technician` update the record."""
    values = parse(json_body(), {"status": Field(one_of(STATUSES)), "odometer": Field(ODOMETER, required=False),
                                 "cost": Field(MONEY, required=False),
                                 "technician": Field(text(100), required=False, nullable=True)})
    vehicle, record = _lock_record(maintenance_id)
    new = values["status"]
    error = rules.maintenance_transition_error(record.status, new)
    if error:
        raise ApiError(422, error, "status")

    if "cost" in values:
        if values["cost"] < _parts_total(maintenance_id):
            raise ApiError(422, rules.parts_total_error(_parts_total(maintenance_id), values["cost"]), "cost")
        record.cost = values["cost"]
    if "technician" in values:
        record.technician = values["technician"]

    warnings = []
    if new == "In Progress":
        error = rules.service_start_block(vehicle.status, db.session.scalar(
            select(func.count()).select_from(Trip).where(Trip.vehicle_id == vehicle.vehicle_id,
                                                         Trip.status == "In Progress")))
        if error:
            raise ApiError(422, error, "status")
        # the odometer at the start is the vehicle's reading unless the technician supplies one
        _apply_reading(record, vehicle, values.get("odometer", vehicle.odometer))
        vehicle.status = "Under Maintenance"
        window_end = today() + timedelta(days=current_app.config["SERVICE_WINDOW_DAYS"] - 1)
        planned = db.session.scalars(select(Trip).where(
            Trip.vehicle_id == vehicle.vehicle_id, Trip.status == "Planned",
            Trip.start_date <= window_end, Trip.end_date >= today()).order_by(Trip.start_date))
        warnings = [{"trip_id": t.trip_id, "start_date": t.start_date.isoformat(), "end_date": t.end_date.isoformat(),
                     "message": f"Planned trip #{t.trip_id} overlaps the service window"} for t in planned]
    else:  # Completed
        if "odometer" in values:
            _apply_reading(record, vehicle, values["odometer"])
        others = db.session.scalar(select(func.count()).select_from(M).where(
            M.vehicle_id == vehicle.vehicle_id, M.status == "In Progress", M.maintenance_id != maintenance_id))
        vehicle.status = rules.vehicle_status_after_completion(vehicle.status, others)
        record.next_service_date = rules.next_service_date(
            record.service_type, record.service_date, current_app.config["SERVICE_INTERVALS"],
            _avg_daily_km(vehicle.vehicle_id))
    record.status = new
    db.session.commit()
    return jsonify(record=record_json(record), vehicle_status=vehicle.status, warnings=warnings)
