"""The single definition of "today" (ARCHITECTURE.md 8.9). Tests freeze it."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from flask import current_app, has_app_context

from .config import Config

_frozen = None


def freeze(day: date) -> None:
    global _frozen
    _frozen = day


def unfreeze() -> None:
    global _frozen
    _frozen = None


def today() -> date:
    if _frozen is not None:
        return _frozen
    tz = current_app.config["APP_TIMEZONE"] if has_app_context() else Config.APP_TIMEZONE
    return datetime.now(ZoneInfo(tz)).date()
