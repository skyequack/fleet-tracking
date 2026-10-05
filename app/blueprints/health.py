from flask import Blueprint, current_app, jsonify
from sqlalchemy import text

from ..extensions import db
from ..services.predictor import Predictor

bp = Blueprint("health", __name__, url_prefix="/api/health")


@bp.get("")
def health():
    """Public liveness check (ARCHITECTURE.md 12): database and model status, no data and no reasons.

    503 only when the database cannot be reached. A missing or rejected model is reported as "degraded" with 200,
    because everything except /api/predict still works; the reason is logged at start-up, not published here.
    """
    try:
        db.session.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # any failure to talk to MySQL means the app cannot serve data
        db.session.rollback()
        current_app.logger.error("health check: database unreachable")
        database = "unavailable"
    model = "ok" if isinstance(current_app.extensions.get("predictor"), Predictor) else "unavailable"
    status = "down" if database != "ok" else ("ok" if model == "ok" else "degraded")
    return jsonify(status=status, database=database, model=model), (503 if database != "ok" else 200)
