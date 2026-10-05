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

## Report figures and numbers

```powershell
.\.venv\Scripts\python.exe scripts\make_report_figures.py --with-tests   # figures 7.1 to 7.8 in report/figures/, all numbers in report/numbers.json
.\.venv\Scripts\python.exe scripts\refresh_report.py                      # writes Fleet_Management_Synopsis_and_Report_refreshed.docx
```

Nothing in Chapters 6 and 7 is typed by hand: both scripts read the live database, the trained model and the test run. The original `.docx` is never modified. The page numbers in the contents lists are plain text in the original, so update them in Word after opening the refreshed file.

Screenshots of the nine screens for the report appendix (Table 4.3) are in `report/screenshots/`. To retake them, start the app and run `node scripts/capture_screenshots.mjs`; it drives an installed Chrome or Edge in the background (light mode, 1440 px wide, 2x density) and needs no extra packages.

`GET /api/health` is a public check that returns the database and model status and no data (503 if the database is down, 200 with `degraded` if only the model is missing).

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
