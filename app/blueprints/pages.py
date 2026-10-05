"""The nine HTML screens (ARCHITECTURE.md 13.1). Pages carry no data: the browser fetches JSON from /api.

Navigation hides what a role cannot use, but every page route still checks the role itself.
"""
import secrets
from collections import namedtuple

from flask import Blueprint, make_response, redirect, render_template, request, session, url_for

from ..constants import ADMIN, MANAGER, OPERATOR
from ..security import current_user

bp = Blueprint("pages", __name__)

Page = namedtuple("Page", "key path title template roles")
EVERYONE = (ADMIN, MANAGER, OPERATOR)
LEADERSHIP = (ADMIN, MANAGER)
PAGES = (
    Page("dashboard", "/dashboard", "Dashboard", "dashboard.html", LEADERSHIP),
    Page("vehicles", "/vehicles", "Vehicles", "vehicles.html", EVERYONE),
    Page("drivers", "/drivers", "Drivers", "drivers.html", LEADERSHIP),
    Page("trips", "/trips", "Trips", "trips.html", EVERYONE),
    Page("fuel", "/fuel", "Fuel", "fuel.html", EVERYONE),
    Page("maintenance", "/maintenance", "Maintenance", "maintenance.html", EVERYONE),
    Page("analytics", "/analytics", "Analytics", "analytics.html", LEADERSHIP),
    Page("predict", "/predict", "Predictive Maintenance", "predict.html", LEADERSHIP),
)


def home_for(user):
    """Where a role lands after signing in: Operators have no dashboard, so they start at Trips."""
    return "/dashboard" if user.role in LEADERSHIP else "/trips"


def _no_store(body, status=200):
    resp = make_response(body, status)
    resp.headers["Cache-Control"] = "no-store"  # the back button after sign-out must not show data
    return resp


@bp.app_context_processor
def _layout():
    user = current_user()
    if user is not None and "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return {"user": user, "csrf": session.get("csrf", ""),
            "nav": [p for p in PAGES if user is not None and user.role in p.roles]}


@bp.get("/")
def index():
    user = current_user()
    return redirect(home_for(user) if user else url_for("pages.login"))


@bp.get("/login")
def login():
    user = current_user()
    if user:
        return redirect(home_for(user))
    return _no_store(render_template("login.html", title="Sign in", page="login"))


def _register(page):
    def view():
        user = current_user()
        if user is None:
            return redirect(url_for("pages.login", next=request.path))
        if user.role not in page.roles:
            return _no_store(render_template("forbidden.html", title="Not allowed", page="forbidden"), 403)
        return _no_store(render_template(page.template, title=page.title, page=page.key))
    bp.add_url_rule(page.path, endpoint=page.key, view_func=view)


for _page in PAGES:
    _register(_page)
