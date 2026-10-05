from flask import Blueprint, current_app, jsonify
from sqlalchemy import select

from ..clock import today
from ..constants import ADMIN, MANAGER
from ..errors import ApiError
from ..extensions import db
from ..models import Vehicle
from ..security import roles_required
from ..services import features as F
from ..services.predictor import Predictor

bp = Blueprint("predict", __name__, url_prefix="/api/predict")


def _predictor():
    state = current_app.extensions.get("predictor")
    if not isinstance(state, Predictor):
        raise ApiError(503, f"model_unavailable: {state or 'the model has not been loaded'}")
    return state


def _score(predictor, vehicles):
    """Features for every vehicle are built together (the type medians need all of them), then the rows are picked."""
    now = today()
    features = F.build_features(now)
    scored = predictor.predict(features.loc[[v.vehicle_id for v in vehicles]])
    items = []
    for v in vehicles:
        row = scored.loc[v.vehicle_id]
        items.append({"vehicle_id": v.vehicle_id, "registration_no": v.registration_no, "type": v.type,
                      "risk": row["risk"], "p_high": round(float(row["p_high"]), 4),
                      "probabilities": {c: round(float(row[f"p_{c.lower()}"]), 4) for c in F.CLASSES},
                      "imputed_fuel": bool(features.loc[v.vehicle_id, "imputed_fuel"])})
    return now, features, items


@bp.get("")
@roles_required(ADMIN, MANAGER)
def predict_all():
    predictor = _predictor()
    vehicles = db.session.scalars(select(Vehicle).where(Vehicle.status != "Inactive").order_by(Vehicle.vehicle_id)).all()
    if not vehicles:
        return jsonify(items=[], total=0, summary=dict.fromkeys(F.CLASSES, 0), as_of=today().isoformat(),
                       model=predictor.summary())
    now, _, items = _score(predictor, vehicles)
    items.sort(key=lambda i: (-i["p_high"], i["vehicle_id"]))
    summary = {c: sum(1 for i in items if i["risk"] == c) for c in F.CLASSES}
    return jsonify(items=items, total=len(items), summary=summary, as_of=now.isoformat(), model=predictor.summary())


@bp.get("/<int:vehicle_id>")
@roles_required(ADMIN, MANAGER)
def predict_one(vehicle_id):
    predictor = _predictor()
    vehicle = db.session.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise ApiError(404, "Vehicle not found")
    if vehicle.status == "Inactive":
        raise ApiError(422, "Vehicle is inactive; no prediction is made for it")
    now, features, items = _score(predictor, [vehicle])
    item = items[0]
    item["features"] = {name: round(float(features.loc[vehicle_id, name]), 2) for name in F.FEATURES}
    return jsonify(item=item, as_of=now.isoformat(), model=predictor.summary())
