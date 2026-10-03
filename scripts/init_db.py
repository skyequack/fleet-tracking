"""Create fleet_db and fleet_test, grant the app user access, and load sql/schema.sql in both."""
import os
import pathlib

import pymysql
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

cfg = dict(host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]),
           user="root", password=os.environ["MYSQL_ROOT_PASSWORD"], autocommit=True)


def load_statements():
    lines = [l for l in (ROOT / "sql" / "schema.sql").read_text(encoding="utf-8").splitlines()
             if not l.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


if __name__ == "__main__":
    statements = load_statements()
    user = os.environ["DB_USER"]
    for db in (os.environ["DB_NAME"], os.environ["TEST_DB_NAME"]):
        conn = pymysql.connect(**cfg)
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db}` CHARACTER SET utf8mb4")
            cur.execute(f"GRANT ALL PRIVILEGES ON `{db}`.* TO %s@'%%'", (user,))
            cur.execute(f"USE `{db}`")
            for stmt in statements:
                cur.execute(stmt)
        conn.close()
        print(f"{db}: schema loaded ({len(statements)} statements)")
