"""Create fleet_db and fleet_test, grant the app user access, and load sql/schema.sql in both."""
import argparse
import os
import pathlib
import sys

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


def has_data(cur, db):
    """True if `db` already holds users or vehicles (schema.sql would DROP them)."""
    cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s "
                "AND table_name IN ('users','vehicles')", (db,))
    if cur.fetchone()[0] < 2:
        return False
    cur.execute(f"SELECT (SELECT COUNT(*) FROM `{db}`.users) + (SELECT COUNT(*) FROM `{db}`.vehicles)")
    return cur.fetchone()[0] > 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reset", action="store_true",
                    help="allow schema.sql to drop and recreate tables that already hold data in the main database")
    args = ap.parse_args()
    statements = load_statements()
    user = os.environ["DB_USER"]
    for db in (os.environ["DB_NAME"], os.environ["TEST_DB_NAME"]):
        conn = pymysql.connect(**cfg)
        with conn.cursor() as cur:
            # fleet_test is disposable; the main database is only wiped on request
            if db == os.environ["DB_NAME"] and not args.reset and has_data(cur, db):
                sys.exit(f"{db} already holds data; schema.sql drops every table. Re-run with --reset to confirm.")
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db}` CHARACTER SET utf8mb4")
            cur.execute(f"GRANT ALL PRIVILEGES ON `{db}`.* TO %s@'%%'", (user,))
            cur.execute(f"USE `{db}`")
            for stmt in statements:
                cur.execute(stmt)
        conn.close()
        print(f"{db}: schema loaded ({len(statements)} statements)")
