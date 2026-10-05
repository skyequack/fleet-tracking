from flask import Blueprint, current_app, jsonify
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from ..constants import ACTIVE_INACTIVE, ADMIN, ROLES
from ..errors import ApiError
from ..extensions import db
from ..models import User
from ..pagination import paginated
from ..security import current_user, roles_required
from ..validation import Field, json_body, one_of, parse, text
from .auth import user_json

bp = Blueprint("users", __name__, url_prefix="/api/users")

EMAIL = r"[^@\s]+@[^@\s]+\.[^@\s]+"


def _spec(password_required):
    min_len = current_app.config["MIN_PASSWORD_LENGTH"]
    return {
        "name": Field(text(100)),
        "email": Field(text(150, pattern=EMAIL, lower=True)),
        "role": Field(one_of(ROLES)),
        "status": Field(one_of(ACTIVE_INACTIVE), required=False),
        "password": Field(text(128, min_len=min_len), required=password_required),
    }


def _apply(user, values):
    password = values.pop("password", None)
    for name, value in values.items():
        setattr(user, name, value)
    if password:
        user.password_hash = generate_password_hash(password)


@bp.get("")
@roles_required(ADMIN)
def list_users():
    return jsonify(paginated(select(User).order_by(User.user_id), user_json))


@bp.post("")
@roles_required(ADMIN)
def create_user():
    values = parse(json_body(), _spec(password_required=True))
    user = User()
    _apply(user, values)
    db.session.add(user)
    db.session.commit()
    return jsonify(user_json(user)), 201


@bp.put("/<int:user_id>")
@roles_required(ADMIN)
def update_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        raise ApiError(404, "User not found")
    values = parse(json_body(), _spec(password_required=False), partial=True)
    # An administrator who demotes or deactivates themselves could lock everyone out.
    if user.user_id == current_user().user_id:
        if values.get("role", user.role) != ADMIN:
            raise ApiError(422, "You cannot change your own role", "role")
        if values.get("status", user.status) != "Active":
            raise ApiError(422, "You cannot deactivate your own account", "status")
    _apply(user, values)
    db.session.commit()
    return jsonify(user_json(user))
