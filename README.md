# Fleet Management and Analytics System

Flask + MySQL 8.0 web app with Pandas analytics and a Random Forest maintenance-risk model.
All data is synthetic dummy data.

## Setup (Windows)

```powershell
copy .env.example .env                 # then edit the passwords if you like
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
docker compose up -d                   # MySQL 8.0 on port 3306 (needs Docker Desktop running)
.\.venv\Scripts\python.exe scripts\init_db.py          # create fleet_db + fleet_test, load sql/schema.sql
.\.venv\Scripts\python.exe ml\generate_dummy_data.py   # seed fleet_db (fixed seed, reproducible)
.\.venv\Scripts\python.exe scripts\validate_data.py    # check the seeded data against the business rules
```

`generate_dummy_data.py --dry-run` prints the totals without touching the database.

## Demo logins

| Role | Email | Password |
|---|---|---|
| Administrator | admin@fleet.local | Admin@123 |
| Fleet Manager | manager@fleet.local | Manager@123 |
| Operator | operator@fleet.local | Operator@123 |

Passwords are stored as salted Werkzeug hashes. These are demo credentials for the seeded data only.
