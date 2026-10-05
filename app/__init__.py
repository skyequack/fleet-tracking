import logging

from flask import Flask

from .config import Config, check_production_config
from .extensions import db


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    check_production_config(app)
    db.init_app(app)

    from . import models  # noqa: F401  (register models)
    from .blueprints import (analytics, assignments, auth, dashboard, drivers, fuel, maintenance, trips, users,
                             vehicles)
    from .errors import init_errors
    from .security import init_security

    init_errors(app)
    init_security(app)
    for module in (auth, users, vehicles, drivers, assignments, trips, fuel, maintenance, analytics, dashboard):
        app.register_blueprint(module.bp)

    if not app.logger.handlers:  # one line per request with a request id (R16)
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        app.logger.addHandler(handler)
    app.logger.setLevel(logging.WARNING if app.config.get("TESTING") else logging.INFO)

    return app
