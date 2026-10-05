from flask import Blueprint, jsonify, request

from ..clock import today
from ..constants import ADMIN, MANAGER
from ..errors import ApiError
from ..security import roles_required
from ..services import analytics
from ..validation import iso_date

bp = Blueprint("analytics", __name__, url_prefix="/api/analytics")


def window_from_request():
    """?from= and ?to= (YYYY-MM-DD), defaulting to the last 12 complete months (9.1)."""
    default = analytics.default_window(today())
    parsed = {}
    for param, fallback in (("from", default.start), ("to", default.end)):
        raw = request.args.get(param)
        try:
            parsed[param] = iso_date(raw) if raw else fallback
        except ValueError as e:
            raise ApiError(400, f"{param} {e}", param)
    window = analytics.Window(parsed["from"], parsed["to"])
    if window.end < window.start:
        raise ApiError(400, "to must not be before from", "to")
    if analytics.window_days(window) > analytics.MAX_WINDOW_DAYS:
        raise ApiError(400, "The window may span at most 10 years", "to")
    return window


def _endpoint(name, builder):
    @bp.get(f"/{name}", endpoint=name.replace("-", "_"))
    @roles_required(ADMIN, MANAGER)
    def view():
        return jsonify(builder(window_from_request()))
    return view


_endpoint("fuel", analytics.fuel_analytics)
_endpoint("cost-per-km", analytics.cost_per_km_analytics)
_endpoint("utilisation", analytics.utilisation_analytics)
_endpoint("efficiency", analytics.efficiency_analytics)
