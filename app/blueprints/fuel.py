from decimal import Decimal

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import case, func, select

from ..clock import today
from ..constants import ADMIN, MANAGER, OPERATOR
from ..errors import ApiError
from ..extensions import db
from ..locking import begin_write, lock_row
from ..models import FuelRecord, Vehicle
from ..pagination import paginated_rows
from ..security import current_user, roles_required
from ..services import rules
from ..validation import Field, boolean, decimal, integer, iso_date, json_body, parse

bp = Blueprint("fuel", __name__, url_prefix="/api/fuel")

F = FuelRecord


def _spec():
    return {
        "vehicle_id": Field(integer(1, 2_147_483_647)),
        "date": Field(iso_date),
        "odometer": Field(decimal(0, 9_999_999, 1)),
        "quantity": Field(decimal(Decimal("0.01"), 999_999, 2)),
        # optional: defaults from config by fuel type. total_cost is never accepted from the client.
        "price_per_litre": Field(decimal(Decimal("0.01"), 9_999, 2), required=False),
        "full_tank": Field(boolean, required=False),
    }


def _efficiency(vehicle_id=None):
    """Subquery (fuel_id, km_per_l) for full-tank fills (8.1).

    km/L = (odometer - odometer of the previous full-tank fill) / (litres added since that fill, this one
    included). With no partial fills in between this is exactly odometer delta / quantity; with partial fills it
    stays correct. The first full-tank fill of a vehicle has no previous one and gets NULL.
    """
    order = (F.date, F.fuel_id)
    cum = func.sum(F.quantity).over(partition_by=F.vehicle_id, order_by=order).label("cum")
    base_stmt = select(F.fuel_id, F.vehicle_id, F.date, F.odometer, F.full_tank, cum)
    if vehicle_id is not None:
        base_stmt = base_stmt.where(F.vehicle_id == vehicle_id)
    base = base_stmt.subquery()
    before = dict(partition_by=base.c.vehicle_id, order_by=(base.c.date, base.c.fuel_id), rows=(None, -1))
    prev_odo = func.max(case((base.c.full_tank, base.c.odometer))).over(**before)
    prev_cum = func.max(case((base.c.full_tank, base.c.cum))).over(**before)
    km_per_l = case((base.c.full_tank, (base.c.odometer - prev_odo) / func.nullif(base.c.cum - prev_cum, 0)))
    return select(base.c.fuel_id, km_per_l.label("km_per_l")).subquery()


def _row_json(row):
    f, registration_no, km_per_l = row
    return {"fuel_id": f.fuel_id, "vehicle_id": f.vehicle_id, "registration_no": registration_no,
            "date": f.date.isoformat(), "odometer": float(f.odometer), "quantity": float(f.quantity),
            "price_per_litre": float(f.price_per_litre), "total_cost": float(f.total_cost),
            "full_tank": bool(f.full_tank), "km_per_l": round(float(km_per_l), 2) if km_per_l is not None else None}


def _select(vehicle_id=None):
    eff = _efficiency(vehicle_id)
    return (select(F, Vehicle.registration_no, eff.c.km_per_l)
            .join(Vehicle, Vehicle.vehicle_id == F.vehicle_id)
            .outerjoin(eff, eff.c.fuel_id == F.fuel_id))


@bp.get("")
@roles_required(ADMIN, MANAGER, OPERATOR)
def list_fuel():
    user = current_user()
    vehicle_id = request.args.get("vehicle_id")
    try:
        vehicle_id = int(vehicle_id) if vehicle_id else None
    except ValueError:
        raise ApiError(400, "vehicle_id must be a whole number", "vehicle_id")
    stmt = _select(vehicle_id)
    if user.role == OPERATOR:  # Operators see only the fills they entered (R8)
        stmt = stmt.where(F.created_by == user.user_id)
    if vehicle_id is not None:
        stmt = stmt.where(F.vehicle_id == vehicle_id)
    for param, clause in (("from", lambda d: F.date >= d), ("to", lambda d: F.date <= d)):
        raw = request.args.get(param)
        if raw:
            try:
                stmt = stmt.where(clause(iso_date(raw)))
            except ValueError as e:
                raise ApiError(400, f"{param} {e}", param)
    return jsonify(paginated_rows(stmt.order_by(F.date.desc(), F.fuel_id.desc()), _row_json))


@bp.post("")
@roles_required(ADMIN, OPERATOR)
def create_fuel():
    values = parse(json_body(), _spec())
    if values["date"] > today():
        raise ApiError(422, "date cannot be in the future", "date")
    user_id = current_user().user_id  # read before the transaction restarts
    begin_write()
    vehicle = lock_row(Vehicle, values["vehicle_id"])
    if vehicle is None:
        raise ApiError(422, "Vehicle does not exist", "vehicle_id")
    if vehicle.status == "Inactive":
        raise ApiError(422, "Vehicle is inactive", "vehicle_id")

    # 8.7: bracket the reading between the neighbouring fills by date; same-day fills count as earlier
    day, same_vehicle = values["date"], F.vehicle_id == vehicle.vehicle_id
    previous = db.session.scalar(select(F.odometer).where(same_vehicle, F.date <= day)
                                 .order_by(F.date.desc(), F.fuel_id.desc()).limit(1))
    following = db.session.scalar(select(F.odometer).where(same_vehicle, F.date > day)
                                  .order_by(F.date, F.fuel_id).limit(1))
    error = rules.odometer_bracket_error(values["odometer"], previous, following)
    if error:
        raise ApiError(422, error, "odometer")

    price = values.get("price_per_litre")
    if price is None:
        price = Decimal(str(current_app.config["FUEL_PRICES"][vehicle.fuel_type]))
    record = F(vehicle_id=vehicle.vehicle_id, date=day, odometer=values["odometer"], quantity=values["quantity"],
               price_per_litre=price, total_cost=rules.fuel_total_cost(values["quantity"], price),
               full_tank=values.get("full_tank", True), created_by=user_id)
    db.session.add(record)
    # the master odometer never goes down, so a back-dated entry cannot lower it
    vehicle.odometer = max(vehicle.odometer, values["odometer"])
    db.session.commit()

    row = db.session.execute(_select(vehicle.vehicle_id).where(F.fuel_id == record.fuel_id)).one()
    return jsonify(_row_json(row)), 201
