"""Request-body validation. Every endpoint declares a fixed field list, so nothing else is ever bound."""
import re
from collections import namedtuple
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import current_app, request

from .errors import ApiError

# check(value) returns the cleaned value or raises ValueError("<what is wrong>").
Field = namedtuple("Field", "check required nullable", defaults=(True, False))

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "Request body must be a JSON object")
    return data


def parse(data, spec, partial=False):
    """Validate `data` against `spec` {name: Field}. Unknown names are rejected (400), bad values give 422."""
    unknown = [k for k in data if k not in spec]
    if unknown:
        raise ApiError(400, f"Unknown field: {unknown[0]}", unknown[0])
    out = {}
    for name, f in spec.items():
        if name not in data:
            if f.required and not partial:
                raise ApiError(422, f"{name} is required", name)
            continue
        value = data[name]
        if value is None or (isinstance(value, str) and not value.strip()):
            if f.nullable:
                out[name] = None
                continue
            raise ApiError(422, f"{name} is required", name)
        try:
            out[name] = f.check(value)
        except ValueError as e:
            raise ApiError(422, f"{name} {e}", name)
    return out


def text(max_len, min_len=1, pattern=None, lower=False, upper=False):
    def check(v):
        if not isinstance(v, str):
            raise ValueError("must be text")
        v = v.strip()
        if len(v) < min_len:
            raise ValueError(f"must be at least {min_len} characters")
        if len(v) > max_len:
            raise ValueError(f"must be at most {max_len} characters")
        if pattern and not re.fullmatch(pattern, v):
            raise ValueError("has an invalid format")
        return v.lower() if lower else v.upper() if upper else v
    return check


def one_of(allowed):
    def check(v):
        if v not in allowed:
            raise ValueError("must be one of: " + ", ".join(allowed))
        return v
    return check


def integer(lo, hi):
    def check(v):
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("must be a whole number")
        if not lo <= v <= hi:
            raise ValueError(f"must be between {lo} and {hi}")
        return v
    return check


def decimal(lo, hi, places):
    def check(v):
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise ValueError("must be a number")
        try:
            d = Decimal(str(v).strip())
        except InvalidOperation:
            raise ValueError("must be a number")
        if not d.is_finite():
            raise ValueError("must be a number")
        if d.as_tuple().exponent < -places:
            raise ValueError(f"must have at most {places} decimal places")
        if not lo <= d <= hi:
            raise ValueError(f"must be between {lo} and {hi}")
        return d
    return check


def boolean(v):
    if not isinstance(v, bool):
        raise ValueError("must be true or false")
    return v


def iso_date(v):
    if not isinstance(v, str) or not _DATE_RE.match(v.strip()):
        raise ValueError("must be a date as YYYY-MM-DD")
    try:
        return date.fromisoformat(v.strip())
    except ValueError:
        raise ValueError("is not a real date")


def like_pattern(q):
    """A contains-match pattern for LIKE, with the user's own % and _ taken literally."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def page_args():
    """?page= and ?per_page= with the API defaults; per_page is capped at PAGE_SIZE_MAX."""
    cfg = current_app.config
    out = []
    for name, default in (("page", 1), ("per_page", cfg["PAGE_SIZE_DEFAULT"])):
        raw = request.args.get(name, default)
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise ApiError(400, f"{name} must be a whole number", name)
        if n < 1:
            raise ApiError(400, f"{name} must be at least 1", name)
        out.append(n)
    return out[0], min(out[1], cfg["PAGE_SIZE_MAX"])
