"""Sessions, role checks, CSRF, login throttle, response headers and request logging (ARCHITECTURE.md 11).

The cookie holds only user_id and the CSRF token. Role and status are read from the database on every
request, so a demoted or deactivated user loses access at once (R9).
"""
import hmac
import math
import threading
import time
import uuid
from collections import deque
from functools import wraps

from flask import g, request, session
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from .errors import ApiError
from .extensions import db
from .models import User

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PUBLIC_ENDPOINTS = {"auth.login"}  # no session exists yet, so no CSRF token either
# Computed once: checked for unknown emails so the response time does not reveal which emails exist.
DUMMY_HASH = generate_password_hash(uuid.uuid4().hex)
CSP = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'"


class LoginThrottle:
    """In-process lockout: `max_failures` failures per key inside `window` seconds lock that key for `window`."""

    def __init__(self, max_failures, window, clock=time.monotonic):
        self.max_failures, self.window, self.clock = max_failures, window, clock
        self._failures, self._locked_until = {}, {}
        self._lock = threading.Lock()

    def retry_after(self, key):
        """Seconds until `key` may try again; 0 if it is not locked."""
        with self._lock:
            remaining = self._locked_until.get(key, 0) - self.clock()
            if remaining > 0:
                return math.ceil(remaining)
            self._locked_until.pop(key, None)
            return 0

    def record_failure(self, key):
        with self._lock:
            now = self.clock()
            if len(self._failures) > 10_000:  # unknown emails must not grow memory without bound
                self._prune(now)
            hits = self._failures.setdefault(key, deque())
            hits.append(now)
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.max_failures:
                self._locked_until[key] = now + self.window
                hits.clear()

    def reset(self, key):
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)

    def clear(self):
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()

    def _prune(self, now):
        self._failures = {k: v for k, v in self._failures.items() if v and v[-1] > now - self.window}
        self._locked_until = {k: t for k, t in self._locked_until.items() if t > now}


def current_user():
    """The signed-in, active user for this request, or None. Reloaded from the database once per request."""
    if "user" not in g:
        user = None
        user_id = session.get("user_id")
        if user_id is not None:
            user = db.session.get(User, user_id)
            if user is None or user.status != "Active":
                session.clear()
                user = None
        g.user = user
    return g.user


def roles_required(*roles):
    """401 if not signed in, 403 if the user's role is not listed. No roles means any signed-in user."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None:
                raise ApiError(401, "Sign in required")
            if roles and user.role not in roles:
                raise ApiError(403, "You do not have permission for this action")
            return fn(*args, **kwargs)
        return wrapper
    return decorator


login_required = roles_required()


def init_security(app):
    app.extensions["login_throttle"] = LoginThrottle(
        app.config["LOGIN_MAX_FAILURES"], app.config["LOGIN_LOCKOUT_MINUTES"] * 60)

    @app.before_request
    def _guard():
        g.request_id = uuid.uuid4().hex[:8]
        if request.method not in UNSAFE_METHODS:
            return
        if request.get_data() and not request.is_json:
            raise ApiError(415, "Request body must be application/json")
        # Without a session there is nothing to forge; the route's decorator answers 401.
        if request.endpoint not in PUBLIC_ENDPOINTS and session.get("user_id") is not None:
            sent = request.headers.get("X-CSRF-Token", "").encode()
            expected = session.get("csrf", "").encode()
            if not expected or not hmac.compare_digest(sent, expected):
                raise ApiError(403, "Missing or invalid CSRF token")

    @app.after_request
    def _headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Request-ID"] = g.get("request_id", "-")
        app.logger.info("%s %s %s %s", g.get("request_id", "-"), request.method, request.path, resp.status_code)
        return resp
