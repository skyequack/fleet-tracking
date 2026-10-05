import secrets

from flask import Blueprint, current_app, jsonify, request, session
from sqlalchemy import select
from werkzeug.security import check_password_hash

from ..errors import ApiError
from ..extensions import db
from ..models import User
from ..security import DUMMY_HASH, current_user, login_required
from ..validation import json_body

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

MAX_PASSWORD_LENGTH = 128


def user_json(user):
    return {"user_id": user.user_id, "name": user.name, "email": user.email, "role": user.role,
            "status": user.status}


@bp.post("/login")
def login():
    data = json_body()
    email, password = data.get("email"), data.get("password")
    if not isinstance(email, str) or not isinstance(password, str) or not email.strip() or not password:
        raise ApiError(422, "email and password are required")
    email = email.strip().lower()
    throttle = current_app.extensions["login_throttle"]
    key = (email, request.remote_addr)
    wait = throttle.retry_after(key)
    if wait:
        raise ApiError(429, f"Too many failed sign-ins; try again in {wait} seconds",
                       headers={"Retry-After": str(wait)})

    user = db.session.scalar(select(User).where(User.email == email))
    # Always run one hash check, so an unknown email takes as long as a wrong password.
    valid = check_password_hash(user.password_hash if user else DUMMY_HASH, password[:MAX_PASSWORD_LENGTH])
    if not (user and valid and user.status == "Active" and len(password) <= MAX_PASSWORD_LENGTH):
        throttle.record_failure(key)
        current_app.logger.warning("sign-in failed for %s from %s", email, request.remote_addr)
        raise ApiError(401, "Invalid email or password")

    throttle.reset(key)
    session.clear()  # a new session on every sign-in (no fixation)
    session.permanent = True
    session["user_id"] = user.user_id
    session["csrf"] = secrets.token_urlsafe(32)
    return jsonify(user=user_json(user), csrf_token=session["csrf"])


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify(message="Signed out")


@bp.get("/me")
@login_required
def me():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return jsonify(user=user_json(current_user()), csrf_token=session["csrf"])
