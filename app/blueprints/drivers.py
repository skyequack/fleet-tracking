from datetime import timedelta

from flask import Blueprint, jsonify, request
from sqlalchemy import func, or_, select

from ..clock import today
from ..constants import (ACTIVE_INACTIVE, ADMIN, LICENCE_WARNING_DAYS, MANAGER, OPEN_TRIP_STATUSES)
from ..errors import ApiError
from ..extensions import db
from ..locking import begin_write, lock_row
from ..models import Driver, Trip
from ..pagination import paginated
from ..security import login_required, roles_required
from ..services import rules
from ..validation import Field, iso_date, json_body, like_pattern, one_of, parse, text

bp = Blueprint("drivers", __name__, url_prefix="/api/drivers")


def driver_json(d, today_=None):
    today_ = today_ or today()
    return {"driver_id": d.driver_id, "name": d.name, "phone": d.phone, "license_no": d.license_no,
            "license_expiry": d.license_expiry.isoformat(), "status": d.status,
            "licence_warning": rules.licence_warning(d.license_expiry, today_),
            "licence_expired": d.license_expiry < today_}


def _spec():
    return {
        "name": Field(text(100)),
        "phone": Field(text(20, pattern=r"\+?[0-9 ]{7,19}"), required=False, nullable=True),
        "license_no": Field(text(30, upper=True)),
        "license_expiry": Field(iso_date),
        "status": Field(one_of(ACTIVE_INACTIVE), required=False),
    }


def _lock(driver_id):
    begin_write()
    driver = lock_row(Driver, driver_id)
    if driver is None:
        raise ApiError(404, "Driver not found")
    return driver


@bp.get("")
@roles_required(ADMIN, MANAGER)
def list_drivers():
    stmt, now = select(Driver), today()
    q = request.args.get("q", "").strip()
    if q:
        like = like_pattern(q)
        stmt = stmt.where(or_(Driver.name.like(like), Driver.license_no.like(like)))
    status = request.args.get("status")
    if status:
        if status not in ACTIVE_INACTIVE:
            raise ApiError(400, "status must be one of: " + ", ".join(ACTIVE_INACTIVE), "status")
        stmt = stmt.where(Driver.status == status)
    if request.args.get("licence_warning") in ("1", "true"):  # the 30-day expiry query (8.2)
        # active drivers only: a driver who has left needs no renewal reminder
        stmt = stmt.where(Driver.status == "Active",
                          Driver.license_expiry.between(now, now + timedelta(days=LICENCE_WARNING_DAYS)))
    return jsonify(paginated(stmt.order_by(Driver.driver_id), lambda d: driver_json(d, now)))


@bp.get("/lookup")
@login_required
def lookup():
    """Names of active drivers for the trip form; Operators create trips but cannot list drivers (R8)."""
    rows = db.session.execute(select(Driver.driver_id, Driver.name).where(Driver.status == "Active")
                              .order_by(Driver.name)).all()
    return jsonify(items=[{"id": r.driver_id, "name": r.name} for r in rows])


@bp.post("")
@roles_required(ADMIN)
def create_driver():
    values = parse(json_body(), _spec())
    driver = Driver(**values)
    db.session.add(driver)
    db.session.commit()
    return jsonify(driver_json(driver)), 201


@bp.put("/<int:driver_id>")
@roles_required(ADMIN)
def update_driver(driver_id):
    driver = _lock(driver_id)
    values = parse(json_body(), _spec(), partial=True)
    if values.get("status") == "Inactive" and driver.status != "Inactive":
        _refuse_if_open_trips(driver_id)
    for name, value in values.items():
        setattr(driver, name, value)
    db.session.commit()
    return jsonify(driver_json(driver))


@bp.delete("/<int:driver_id>")
@roles_required(ADMIN)
def deactivate_driver(driver_id):
    driver = _lock(driver_id)
    if driver.status != "Inactive":
        _refuse_if_open_trips(driver_id)
        driver.status = "Inactive"
        db.session.commit()
    return jsonify(driver_json(driver))


def _refuse_if_open_trips(driver_id):
    open_trips = db.session.scalar(select(func.count()).select_from(Trip).where(
        Trip.driver_id == driver_id, Trip.status.in_(OPEN_TRIP_STATUSES)))
    reason = rules.driver_deactivation_block(open_trips)
    if reason:
        raise ApiError(422, reason)
