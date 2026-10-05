"""Shared fixtures. Integration tests use fleet_test (TestConfig refuses any other database)."""
from datetime import date

import pytest
from sqlalchemy import text
from werkzeug.security import generate_password_hash

from app import clock, create_app, models
from app.config import TestConfig
from app.extensions import db

PASSWORD = "Test-pass-1"
TODAY = date(2026, 10, 5)
TABLES = ("maintenance_parts", "maintenance_records", "fuel_records", "trips", "vehicle_assignments",
          "drivers", "vehicles", "users")
USERS = {"Administrator": "admin@test.local", "Fleet Manager": "manager@test.local",
         "Operator": "operator@test.local"}
_HASH = generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000")  # cheap hash keeps the suite fast


@pytest.fixture(scope="session")
def app():
    return create_app(TestConfig)


class Api:
    """A test-client wrapper that sends the CSRF header the way the browser will."""

    def __init__(self, client):
        self.client, self.csrf = client, None

    def login(self, email, password=PASSWORD):
        resp = self.client.post("/api/auth/login", json={"email": email, "password": password})
        if resp.status_code == 200:
            self.csrf = resp.json["csrf_token"]
        return resp

    def _headers(self):
        return {"X-CSRF-Token": self.csrf} if self.csrf else {}

    def get(self, url, **kw):
        return self.client.get(url, **kw)

    def post(self, url, json=None, **kw):
        return self.client.post(url, json=json, headers=self._headers(), **kw)

    def put(self, url, json=None, **kw):
        return self.client.put(url, json=json, headers=self._headers(), **kw)

    def delete(self, url, **kw):
        return self.client.delete(url, headers=self._headers(), **kw)


@pytest.fixture
def world(app):
    """Empty fleet_test with one user per role, a frozen clock and a reset login throttle."""
    with app.app_context():
        db.session.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for table in TABLES:
            db.session.execute(text(f"TRUNCATE TABLE {table}"))
        db.session.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        for role, email in USERS.items():
            db.session.add(models.User(name=role, email=email, password_hash=_HASH, role=role))
        db.session.commit()
    app.extensions["login_throttle"].clear()
    clock.freeze(TODAY)
    yield app
    clock.unfreeze()
    with app.app_context():
        db.session.remove()


@pytest.fixture
def anon(world):
    return Api(world.test_client())


@pytest.fixture
def login_as(world):
    def _login(role):
        api = Api(world.test_client())
        assert api.login(USERS[role]).status_code == 200
        return api
    return _login


@pytest.fixture
def admin(login_as):
    return login_as("Administrator")


@pytest.fixture
def manager(login_as):
    return login_as("Fleet Manager")


@pytest.fixture
def operator(login_as):
    return login_as("Operator")


@pytest.fixture
def seed(world):
    """Insert rows directly and return their ids: seed(models.Vehicle, registration_no=...) -> id."""
    defaults = {
        models.Vehicle: dict(registration_no="ABC 1234", type="Van", make="Toyota", model="HiAce", year=2022,
                             fuel_type="Petrol", odometer=1000, status="Active"),
        models.Driver: dict(name="Test Driver", phone="0512345678", license_no="1011111111",
                            license_expiry=date(2027, 12, 31), status="Active"),
        models.VehicleAssignment: dict(start_date=date(2026, 1, 1), end_date=None),
        models.Trip: dict(origin="Riyadh", destination="Jeddah", distance=100, start_date=date(2026, 10, 10),
                          end_date=date(2026, 10, 11), status="Planned"),
        models.FuelRecord: dict(date=date(2026, 9, 1), odometer=1000, quantity=50, price_per_litre=2.18,
                                total_cost=109.00, full_tank=True),
        models.MaintenancePart: dict(part_name="Oil filter", quantity=1, unit_cost=10),
        models.MaintenanceRecord: dict(service_type="Oil Change", service_date=date(2026, 10, 10), odometer=1000,
                                       cost=0, status="Scheduled"),
    }

    def _seed(model, **kw):
        with world.app_context():
            row = model(**{**defaults[model], **kw})
            db.session.add(row)
            db.session.commit()
            return db.inspect(row).identity[0]
    return _seed


@pytest.fixture
def set_user(world):
    """Change a seeded user directly in the database (to prove role and status are re-read per request)."""
    def _set(who, **changes):
        with world.app_context():
            user = models.User.query.filter_by(email=USERS[who]).one()
            for k, v in changes.items():
                setattr(user, k, v)
            db.session.commit()
    return _set
