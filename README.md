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

`init_db.py` refuses to touch `fleet_db` once it holds data, because `schema.sql` drops every table. After a schema change, run `init_db.py --reset` and then the generator again. `requirements.lock.txt` holds the exact versions used for the results (`pip install -r requirements.lock.txt` to reproduce them).

## Train the prediction model

```powershell
.\.venv\Scripts\python.exe ml\train.py     # reads fleet_db, writes ml/model.joblib, ml/model.meta.json, ml/figures/
```

The script prints accuracy, per-class precision/recall/F1 and the confusion matrix, and saves the confusion-matrix and feature-importance figures. The app loads the model once at start-up after checking its SHA-256, feature list and scikit-learn version; if anything fails, `/api/predict` answers 503 and the rest of the app keeps working. The training labels are synthetic (see `label_source` in `ml/model.meta.json`), so the results demonstrate the method only.

## Run the app

```powershell
.\.venv\Scripts\python.exe -m flask --app app run     # then open http://localhost:5000 and sign in
```

Operators start at Trips; Administrators and Fleet Managers start at the Dashboard. Bootstrap 5.3.3 and Plotly are served from `app/static/vendor` (no CDN at run time; the content security policy allows this origin only).

## Demo logins

| Role | Email | Password |
|---|---|---|
| Administrator | admin@fleet.local | Admin@123 |
| Fleet Manager | manager@fleet.local | Manager@123 |
| Operator | operator@fleet.local | Operator@123 |

Passwords are stored as salted Werkzeug hashes. These are demo credentials for the seeded data only. Never deploy with them: set `APP_ENV=production` (which requires a random `SECRET_KEY` of 32+ characters) and replace the seeded users.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q    # unit tests need no database; integration tests use fleet_test
```
