"""One error shape for the whole API: {"error": "<message>", "field": "<optional>"}."""
from flask import current_app, jsonify
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException

from .extensions import db

# MySQL constraint name -> (field, message). Keeps duplicate errors next to the offending input.
UNIQUE_KEYS = {
    "uq_users_email": ("email", "A user with this email already exists"),
    "uq_vehicles_reg": ("registration_no", "A vehicle with this registration number already exists"),
    "uq_drivers_license": ("license_no", "A driver with this licence number already exists"),
}

ER_DUP_ENTRY, ER_ROW_IS_REFERENCED, ER_NO_REFERENCED_ROW, ER_CHECK_VIOLATED = 1062, 1451, 1452, 3819


class ApiError(Exception):
    def __init__(self, status, message, field=None, headers=None):
        super().__init__(message)
        self.status, self.message, self.field, self.headers = status, message, field, headers or {}


def _response(status, message, field=None, headers=None):
    body = {"error": message}
    if field:
        body["field"] = field
    resp = jsonify(body)
    resp.status_code = status
    for k, v in (headers or {}).items():
        resp.headers[k] = v
    return resp


def _integrity_error(e):
    db.session.rollback()
    code = e.orig.args[0] if e.orig is not None and e.orig.args else None
    text = str(e.orig)
    if code == ER_DUP_ENTRY:
        for key, (field, message) in UNIQUE_KEYS.items():
            if key in text:
                return _response(409, message, field)
        return _response(409, "A record with these values already exists")
    if code == ER_ROW_IS_REFERENCED:
        return _response(409, "This record is referenced by other records")
    if code == ER_NO_REFERENCED_ROW:
        return _response(422, "A referenced record does not exist")
    if code == ER_CHECK_VIOLATED:
        return _response(422, "A value is outside the allowed range")
    current_app.logger.error("unmapped integrity error: %s", text)
    return _response(500, "Internal server error")


def init_errors(app):
    @app.errorhandler(ApiError)
    def _api_error(e):
        return _response(e.status, e.message, e.field, e.headers)

    @app.errorhandler(IntegrityError)
    def _integrity(e):
        return _integrity_error(e)

    @app.errorhandler(HTTPException)
    def _http(e):
        return _response(e.code, e.description)

    @app.errorhandler(Exception)
    def _unexpected(e):
        db.session.rollback()
        current_app.logger.error("unhandled error", exc_info=e)
        return _response(500, "Internal server error")
