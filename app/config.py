import os
import pathlib
from datetime import timedelta

from dotenv import load_dotenv
from sqlalchemy.engine import URL

load_dotenv()

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEAK_SECRET_KEYS = {"", "dev", "change-me", "dev-secret-key-not-for-production"}


def _uri(db_name):
    # URL.create escapes special characters (@ : / %) in the password, which an f-string would not.
    return URL.create(
        "mysql+pymysql",
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "3306")),
        database=db_name,
    ).render_as_string(hide_password=False)


def _test_db_name():
    # The test run drops and recreates tables, so it must never point at the real database.
    name = os.environ.get("TEST_DB_NAME", "fleet_test")
    if not name.endswith("_test") or name == os.environ.get("DB_NAME", "fleet_db"):
        raise RuntimeError(f"TEST_DB_NAME must end in '_test' and differ from DB_NAME (got {name!r})")
    return name


class Config:
    APP_ENV = os.environ.get("APP_ENV", "development")  # "production" turns on the strict checks
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev")
    SQLALCHEMY_DATABASE_URI = _uri(os.environ.get("DB_NAME", "fleet_db"))

    # Session cookie (ARCHITECTURE.md section 11)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = APP_ENV == "production"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    LOGIN_MAX_FAILURES = 5
    LOGIN_LOCKOUT_MINUTES = 15
    MIN_PASSWORD_LENGTH = 8
    MAX_CONTENT_LENGTH = 1_000_000  # request bodies are small JSON documents

    # One timezone for every "today" (licence expiry, trip checks, next-service dates)
    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Riyadh")

    # API list defaults (section 12)
    PAGE_SIZE_DEFAULT = 25
    PAGE_SIZE_MAX = 100

    # Prediction model bundle; the SHA-256 is checked before joblib loads it (section 10)
    MODEL_PATH = ROOT / "ml" / "model.joblib"
    MODEL_META_PATH = ROOT / "ml" / "model.meta.json"

    # Assumptions (ARCHITECTURE.md G10)
    CURRENCY = "SAR"
    FUEL_PRICES = {"Diesel": 1.66, "Petrol": 2.18}

    # Next-service rule: service_type -> (max days, max km). The earlier limit wins.
    # None means "no limit on that axis"; Breakdown Repair has no next service.
    SERVICE_INTERVALS = {
        "Oil Change": (180, 10000),
        "Routine Service": (180, 20000),
        "Tyres": (540, 40000),
        "Brakes": (365, 30000),
        "Inspection": (365, None),
    }


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = _uri(_test_db_name())


def check_production_config(app):
    """Fail fast instead of running with a guessable session key."""
    if app.config["APP_ENV"] == "production":
        key = app.config["SECRET_KEY"]
        if key in WEAK_SECRET_KEYS or len(key) < 32:
            raise RuntimeError("SECRET_KEY must be a random value of at least 32 characters when APP_ENV=production")
