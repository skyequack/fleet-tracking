from flask import Blueprint, jsonify

from ..constants import ADMIN, MANAGER
from ..security import roles_required
from ..services import analytics
from .analytics import window_from_request

bp = Blueprint("dashboard", __name__, url_prefix="/api/dashboard")


@bp.get("")
@roles_required(ADMIN, MANAGER)
def dashboard():
    """One call for the whole screen: 6 cards, a 4-item strip and 4 chart series (9.2)."""
    return jsonify(analytics.build_dashboard(window_from_request()))
