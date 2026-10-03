import os

from dotenv import load_dotenv

load_dotenv()


def _uri(db_name):
    return (f"mysql+pymysql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
            f"@{os.environ['DB_HOST']}:{os.environ.get('DB_PORT', '3306')}/{db_name}")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev")
    SQLALCHEMY_DATABASE_URI = _uri(os.environ.get("DB_NAME", "fleet_db"))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Assumptions (ARCHITECTURE.md G10)
    CURRENCY = "SAR"
    FUEL_PRICES = {"Diesel": 1.66, "Petrol": 2.18}


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = _uri(os.environ.get("TEST_DB_NAME", "fleet_test"))
