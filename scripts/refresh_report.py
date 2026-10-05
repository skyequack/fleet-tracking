"""Refresh the Word report from the real build (ARCHITECTURE.md G1, plan Sun 11 Oct).

    python scripts/make_report_figures.py --with-tests      # first: figures and report/numbers.json
    python scripts/refresh_report.py                        # then: writes Fleet_Management_Synopsis_and_Report_refreshed.docx

The original .docx is never modified. Every number comes from report/numbers.json; every edit names the paragraph or
table it changes and fails loudly if that text is not found, so a silent skip cannot leave an old number behind.
Page numbers in the contents lists are typed text in the original and are NOT recomputed: update them in Word.
"""
import json
import pathlib
import re
import sys
import zipfile
from datetime import date
from xml.sax.saxutils import escape, unescape
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "Fleet_Management_Synopsis_and_Report.docx"
TARGET = ROOT / "Fleet_Management_Synopsis_and_Report_refreshed.docx"
NUMBERS = ROOT / "report" / "numbers.json"
FIGURES = ROOT / "report" / "figures"
FIGURE_IMAGES = {"figure_7_1.png": "image5.png", "figure_7_2.png": "image6.png", "figure_7_3.png": "image7.png",
                 "figure_7_4.png": "image8.png", "figure_7_5.png": "image9.png", "figure_7_6.png": "image10.png",
                 "figure_7_7.png": "image11.png", "figure_7_8.png": "image12.png"}
P = r"<w:p(?:\s[^>]*)?>.*?</w:p>"
TBL = r"<w:tbl>.*?</w:tbl>"


def n0(x): return f"{x:,.0f}"
def n1(x): return f"{x:,.1f}"
def n2(x): return f"{x:,.2f}"
def n3(x): return f"{x:,.3f}"
def pct(x): return f"{100 * x:.1f}%"


def confusions(count, actual, predicted, first=True):
    """'One LOW vehicle is classed as HIGH' / 'no HIGH vehicle is classed as LOW' / '3 HIGH vehicles are classed as LOW'."""
    words = {1: "One" if first else "one"}
    if count == 0:
        return f"no {actual} vehicle is classed as {predicted}"
    if count == 1:
        return f"{words[1]} {actual} vehicle is classed as {predicted}"
    return f"{count} {actual} vehicles are classed as {predicted}"


def ptext(p):
    return unescape("".join(re.findall(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>", p)))


class Report:
    def __init__(self, xml):
        self.xml, self.edits = xml, 0

    # ---- paragraphs ---------------------------------------------------------------------------------------------
    def _paragraphs(self):
        return [(m.start(), m.end(), m.group(0)) for m in re.finditer(P, self.xml, flags=re.S)]

    def _find(self, prefix, exact=False):
        hits = [(s, e, p) for s, e, p in self._paragraphs()
                if (ptext(p) == prefix if exact else ptext(p).startswith(prefix))]
        if len(hits) != 1:
            raise SystemExit(f"expected one paragraph starting {prefix[:70]!r}, found {len(hits)}")
        return hits[0]

    @staticmethod
    def _rebuild(p, text):
        head = re.match(r"(<w:p(?:\s[^>]*)?>)(<w:pPr>.*?</w:pPr>)?", p, flags=re.S)
        runs = [m.group(1) or "" for m in re.finditer(r"<w:r(?:\s[^>]*)?>(<w:rPr>.*?</w:rPr>)?<w:t[ >]", p, flags=re.S)]
        if len(set(runs)) > 1:
            raise SystemExit(f"paragraph has mixed formatting, cannot rewrite: {ptext(p)[:70]!r}")
        rpr = runs[0] if runs else ""
        return f'{head.group(1)}{head.group(2) or ""}<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'

    def set_par(self, prefix, text, exact=False):
        s, e, p = self._find(prefix, exact)
        self.xml = self.xml[:s] + self._rebuild(p, text) + self.xml[e:]
        self.edits += 1

    def insert_after(self, prefix, text, exact=False):
        s, e, p = self._find(prefix, exact)
        self.xml = self.xml[:e] + self._rebuild(p, text) + self.xml[e:]
        self.edits += 1

    def set_block(self, first_prefix, count, lines):
        """Replace `count` consecutive paragraphs starting at `first_prefix` with `lines` (cloning the last as needed)."""
        paras = self._paragraphs()
        start = next((i for i, (_, _, p) in enumerate(paras) if ptext(p).startswith(first_prefix)), None)
        if start is None:
            raise SystemExit(f"block start not found: {first_prefix!r}")
        old = paras[start:start + count]
        new = [self._rebuild(old[min(i, len(old) - 1)][2], line) for i, line in enumerate(lines)]
        self.xml = self.xml[:old[0][0]] + "".join(new) + self.xml[old[-1][1]:]
        self.edits += 1

    def replace_text(self, old, new, count):
        """Exact text of a whole <w:t> node, e.g. a heading that also appears in the contents list."""
        target = f">{escape(old)}<"
        found = self.xml.count(target)
        if found != count:
            raise SystemExit(f"expected {count} x {old!r}, found {found}")
        self.xml = self.xml.replace(target, f">{escape(new)}<")
        self.edits += 1

    # ---- tables ---------------------------------------------------------------------------------------------------
    def _tables(self):
        return [(m.start(), m.end(), m.group(0)) for m in re.finditer(TBL, self.xml, flags=re.S)]

    @staticmethod
    def _rows(t):
        return [m.group(0) for m in re.finditer(r"<w:tr(?:\s[^>]*)?>.*?</w:tr>", t, flags=re.S)]

    @staticmethod
    def _cells(r):
        return [m.group(0) for m in re.finditer(r"<w:tc>.*?</w:tc>", r, flags=re.S)]

    def _find_table(self, header):
        hits = [(s, e, t) for s, e, t in self._tables()
                if [ptext(c) for c in self._cells(self._rows(t)[0])] == header]
        if len(hits) != 1:
            raise SystemExit(f"expected one table with header {header}, found {len(hits)}")
        return hits[0]

    def _set_cell(self, cell, text):
        p = re.search(P, cell, flags=re.S)   # a second paragraph in the cell, if any, is dropped
        return cell[:p.start()] + self._rebuild(p.group(0), text) + "</w:tc>"

    def _fill_row(self, row, values):
        """The row with each cell's text replaced (None keeps a cell). Works by position, so equal cells are safe."""
        spans = [(m.start(), m.end()) for m in re.finditer(r"<w:tc>.*?</w:tc>", row, flags=re.S)]
        if len(spans) != len(values):
            raise SystemExit(f"row has {len(values)} values for {len(spans)} cells: {values}")
        out, pos = [], 0
        for (a, b), value in zip(spans, values):
            out.append(row[pos:a])
            out.append(row[a:b] if value is None else self._set_cell(row[a:b], value))
            pos = b
        out.append(row[pos:])
        return "".join(out)

    def set_table(self, header, rows, new_header=None):
        """Replace the body rows of the table with this header (reusing the row formatting; the last one is cloned)."""
        s, e, t = self._find_table(header)
        trs = self._rows(t)
        templates = trs[1:] or [trs[0]]
        head = self._fill_row(trs[0], new_header) if new_header else trs[0]
        body = [self._fill_row(templates[min(i, len(templates) - 1)], values) for i, values in enumerate(rows)]
        start, stop = t.index(trs[0]), t.rindex(trs[-1]) + len(trs[-1])
        self.xml = self.xml[:s] + t[:start] + head + "".join(body) + t[stop:] + self.xml[e:]
        self.edits += 1

    def set_cell_in_row(self, header, first_cell, column, text):
        s, e, t = self._find_table(header)
        for r in self._rows(t):
            cells = self._cells(r)
            if ptext(cells[0]) == first_cell:
                values = [None] * len(cells)
                values[column] = text
                self.xml = self.xml[:s] + t.replace(r, self._fill_row(r, values), 1) + self.xml[e:]
                self.edits += 1
                return
        raise SystemExit(f"row {first_cell!r} not found in table {header}")

    def insert_after_table(self, header, text, like_prefix):
        s, e, t = self._find_table(header)
        _, _, template = self._find(like_prefix)
        self.xml = self.xml[:e] + self._rebuild(template, text) + self.xml[e:]
        self.edits += 1


def unique_ids(xml):
    """Cloned paragraphs and rows repeat their original's w14:paraId; keep the first and drop the attribute from the rest."""
    seen = set()

    def keep(match):
        if match.group(2) in seen:
            return ""
        seen.add(match.group(2))
        return match.group(0)
    xml = re.sub(r'(\sw14:paraId="([^"]*)")', keep, xml)
    return xml


def build(d):
    x = Report(zipfile.ZipFile(SOURCE).read("word/document.xml").decode("utf-8"))
    c, s, f, types, months = d["cards"], d["strip"], d["fleet"], d["types"], d["months"]
    m, strat, grp = d["model"], d["model"]["stratified_split"], d["model"]["grouped_split"]
    acc, gacc = strat["accuracy"], grp["accuracy"]
    pc = strat["per_class"]
    cm = strat["confusion_matrix"]
    tests = d["tests"]["levels"]
    unit, integ = tests["unit"]["passed"], tests["integration"]["passed"]
    failed = sum(v["failed"] for v in tests.values())
    auto = unit + integ
    rows = d["prediction"]["rows"]
    summ = d["prediction"]["summary"]
    asof = date.fromisoformat(d["prediction"]["as_of"])
    asof_text = f"{asof.day} {asof.strftime('%B %Y')}"
    if failed or d["tests"]["exit_code"] != 0:
        raise SystemExit("the recorded test run has failures; fix them before refreshing the report")

    month_name = lambda mm: date(int(mm[:4]), int(mm[5:]), 1).strftime("%B %Y")
    short = lambda mm: date(int(mm[:4]), int(mm[5:]), 1).strftime("%b %Y")
    top_fuel = max(months, key=lambda r: r["fuel_litres"])
    low_fuel = min(months, key=lambda r: r["fuel_litres"])
    fuel_costs = [r["fuel_cost"] for r in months]
    maint_costs = [r["maintenance_cost"] for r in months]
    max_maint = max(months, key=lambda r: r["maintenance_cost"])
    min_maint = min(months, key=lambda r: r["maintenance_cost"])
    total_cost = c["fuel_cost"] + c["maintenance_cost"]
    fuel_share = c["fuel_cost"] / total_cost
    truck = types[0]
    best = max(types, key=lambda t: t["km_per_l"])
    van_like = max(types, key=lambda t: t["maintenance_cost"])
    by_name = {t["type"]: t for t in types}
    sum_l, sum_fc, sum_mc, sum_km = (sum(round(r[k]) for r in months) for k in ("fuel_litres", "fuel_cost", "maintenance_cost", "distance_km"))
    importance = m["feature_importance"]
    top3 = list(importance.items())[:3]
    outside_rule = ("prev_maint_cost", "avg_fuel_l_per_100km", "avg_monthly_km", "maintenance_events")
    outside_sum = sum(importance[k] for k in outside_rule)
    wp = sum(pc[k]["precision"] * pc[k]["support"] for k in pc) / strat["test_rows"]
    wr = sum(pc[k]["recall"] * pc[k]["support"] for k in pc) / strat["test_rows"]
    wf = sum(pc[k]["f1-score"] * pc[k]["support"] for k in pc) / strat["test_rows"]
    seconds = d["dashboard_seconds"]
    counts = d["counts"]

    # ---- synopsis and abstract ------------------------------------------------------------------------------------
    x.set_par("The author generated a dummy dataset of 50 vehicles over 12 months and ran the design on it.",
              f"The author generated a dummy dataset of 50 vehicles over 12 months and ran the system on it. The dashboard shows "
              f"{n0(c['total_distance_km'])} km of distance, a fuel cost of {n0(c['fuel_cost'])} SAR, a maintenance cost of "
              f"{n0(c['maintenance_cost'])} SAR and a cost of {n3(f['cost_per_km'])} SAR per kilometre. On the held-out test set of the "
              f"dummy data, the Random Forest model reaches {100 * acc:.1f}% accuracy. These values show the output format. They do not "
              "show real predictive performance. The author must retrain the model on real maintenance history before it claims real predictions.")
    x.set_par("This report describes the requirements, design, implementation and test plan.",
              "This report describes the requirements, design, implementation and tests. It also shows results from a dummy dataset of 50 "
              f"vehicles over 12 months. In this dataset, the total distance is {n0(c['total_distance_km'])} km, the cost per kilometre is "
              f"{n3(f['cost_per_km'])} SAR, and the classifier reaches {100 * acc:.1f}% accuracy on the test set. The dummy data shows the "
              "output format. It does not prove real predictive performance.")

    # ---- headings and captions that said "planned" or "expected" ---------------------------------------------------
    x.replace_text("Chapter 7: Expected Results and Discussion", "Chapter 7: Results and Discussion", 2)
    x.replace_text("6.6 Planned Results", "6.6 Test Results", 2)
    x.replace_text("7.8 Expected Dashboard Layout", "7.8 Dashboard Layout", 2)
    x.replace_text("Table 6.2: Planned test counts and expected outcome", "Table 6.2: Test counts and results", 2)
    x.replace_text("Table 7.2: Expected dashboard indicators", "Table 7.2: Dashboard indicators", 2)
    x.replace_text("Figure 7.8: Expected dashboard indicators", "Figure 7.8: Dashboard indicators", 2)

    # ---- chapter 4: database table ---------------------------------------------------------------------------------
    h41 = ["Table", "Primary key", "Main fields"]
    x.set_cell_in_row(h41, "users", 2, "name, email, password_hash, role, status")
    x.set_cell_in_row(h41, "vehicles", 2, "registration_no, type, make, model, year, fuel_type, odometer, status")
    x.set_cell_in_row(h41, "trips", 2, "vehicle_id, driver_id, origin, destination, distance, start_date, end_date, status, created_at, created_by")
    x.set_cell_in_row(h41, "fuel_records", 2, "vehicle_id, date, odometer, quantity, price_per_litre, total_cost, full_tank, created_at, created_by")
    x.set_cell_in_row(h41, "maintenance_records", 2, "vehicle_id, service_type, service_date, odometer, cost, technician, next_service_date, status, created_at, created_by")

    # ---- chapter 5: the implementation as built ------------------------------------------------------------------
    x.set_par("This chapter describes the planned implementation.",
              "This chapter describes the implementation. The code samples come from the working system.")
    x.set_par("The author will develop the system in VS Code",
              "The author developed the system in VS Code on a Windows laptop with Python 3.13. MySQL 8.0 runs in a Docker container that "
              "accepts connections from the local machine only. MySQL Workbench helps with design and inspection. Git provides version control. "
              "A virtual environment keeps the Python packages separate, and the file requirements.lock.txt records the exact versions that "
              "produced the results in Chapter 7.")
    x.set_block("CREATE TABLE maintenance_records (", 13, [
        "CREATE TABLE maintenance_records (",
        "    maintenance_id    INT AUTO_INCREMENT PRIMARY KEY,",
        "    vehicle_id        INT           NOT NULL,",
        "    service_type      VARCHAR(30)   NOT NULL,",
        "    service_date      DATE          NOT NULL,",
        "    odometer          DECIMAL(10,1) NOT NULL,",
        "    cost              DECIMAL(10,2) NOT NULL DEFAULT 0,",
        "    technician        VARCHAR(100),",
        "    next_service_date DATE          NULL,",
        "    status            VARCHAR(15)   NOT NULL DEFAULT 'Scheduled',",
        "    created_at        TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,",
        "    created_by        INT           NULL,",
        "    CONSTRAINT fk_maint_vehicle FOREIGN KEY (vehicle_id)",
        "        REFERENCES vehicles(vehicle_id) ON DELETE RESTRICT,",
        "    CONSTRAINT ck_maint_cost CHECK (cost >= 0),",
        "    CONSTRAINT ck_maint_status CHECK",
        "        (status IN ('Scheduled','In Progress','Completed')),",
        "    INDEX idx_maint_vehicle_date (vehicle_id, service_date)",
        ");"])
    x.set_par("The design adds an index on vehicle_id",
              "The schema adds an index on vehicle_id and on the date column of the fuel and maintenance tables, and two indexes on trips for "
              "the overlap checks. CHECK constraints reject a negative cost, a fuel total that differs from quantity times price, and a value "
              "outside each status list. The script init_db.py refuses to rebuild a database that already holds data unless the user confirms.")
    x.set_par("The system stores only password hashes.",
              "The system stores only password hashes. It uses the Werkzeug functions generate_password_hash and check_password_hash. After a "
              "successful sign-in, Flask starts a new session that holds only the user identifier and a token against cross-site requests. The "
              "role and the status of the user are read from the database on every request, so a user who is demoted or deactivated loses "
              "access at once. A decorator checks the role before each protected route. It returns the status code 403 when the role is not "
              "allowed and 401 when nobody is signed in. Five failed sign-ins for one email and address lock that pair for 15 minutes, and "
              "the system gives the same message for a wrong password and an unknown email.")
    x.set_par("The vehicle routes accept JSON and return JSON.",
              "The vehicle routes accept JSON and return JSON. The add route rejects a duplicate registration number with the status code 409. "
              "The delete route sets the status to Inactive and does not remove the row. It refuses with the status code 422 while the vehicle "
              "has a planned or running trip or an open service, and a separate route restores the vehicle. Only the Administrator role can "
              "call these routes.")
    x.set_par("The driver module runs a daily query.",
              "The driver module selects the active drivers whose licence expires in the next 30 days. The whole system uses one definition of "
              "today, in the time zone of the fleet. The drivers page shows these drivers with a warning label.")
    x.set_par("Before the system creates a trip, it checks four rules.",
              "Before the system creates a trip, it runs six checks in one transaction that first locks the vehicle row and then the driver row. "
              "The vehicle must not be Inactive, and a trip that starts today or earlier needs an Active vehicle. The driver status must be "
              "Active. The driver licence must be valid on the trip end date. The vehicle must not belong to another trip in the same period. "
              "The driver must not belong to another trip in the same period. The driver must be assigned to the vehicle on the start date. "
              "Cancelled trips do not count. If a check fails, the system shows the reason next to the field and does not save the trip. A "
              "trip moves from Planned to In Progress to Completed, or to Cancelled.")
    x.set_par("Fuel Efficiency (km/L) = (Odometer_n",
              "Fuel Efficiency (km/L) = (Odometer_n − Odometer of the previous full tank) / Litres added since that full tank")
    x.set_par("The system rejects an odometer value that is lower",
              "The system rejects an odometer value that is lower than the previous reading or higher than the next reading of the same "
              "vehicle, so a back-dated entry fits between its neighbours. The odometer of the vehicle never decreases. When no partial fill "
              "lies between two full tanks, the formula equals the distance divided by the litres of the second fill.")
    x.set_par("An operator records the service type",
              "An operator records the service type, service date, cost and technician, and adds the parts. A new service has the status "
              "Scheduled. When the service starts, the system sets the service status to In Progress and the vehicle status to Under "
              "Maintenance. A service cannot start while the vehicle has a trip in progress. When the service ends, the system sets the "
              "service status to Completed. The vehicle returns to Active only if no other service is running and the Administrator has not "
              "made it Inactive. When a service completes, the system sets the next service date to the earlier of two dates: the day limit "
              "of the service type, and the date at which the distance limit is reached at the average daily distance of the vehicle over the "
              "last 90 days. A completed record cannot be changed. The parts of a record may not cost more than the record.")
    x.set_par("The analytics module reads the tables into Pandas data frames.",
              "The analytics module groups the data in SQL by month and by vehicle and reads only the small grouped result into Pandas data "
              "frames. The following code calculates monthly fuel use and cost.")
    x.set_block("fuel = pd.read_sql(", 5, [
        'month = func.date_format(FuelRecord.date, "%Y-%m").label("month")',
        'stmt = (select(month, func.sum(FuelRecord.quantity).label("litres"),',
        '               func.sum(FuelRecord.total_cost).label("cost"))',
        "        .where(FuelRecord.date.between(start, end)).group_by(month))",
        "monthly = pd.read_sql(stmt, db.session.connection())"])
    x.set_par("A vehicle has an active day on each day",
              "A vehicle has an active day on each day between the start date and the end date of one of its trips. Completed and In Progress "
              "trips count, the days are clipped to the reporting window, and the available days are the days of the window times the number "
              "of vehicles that are not Inactive. Distance, fuel and cost indicators count the Completed trips.")
    x.insert_after("The workflow has six steps:",
                   "The dummy labels come from a formula: 0.30 × vehicle age / 8, plus 0.25 × mileage / 250,000, plus 0.30 × distance since "
                   "service / 5,000, plus 0.15 × earlier breakdowns / 2, plus random noise with a standard deviation of 0.10. A score below 0.40 "
                   "is LOW, a score below 0.60 is MEDIUM, and any other score is HIGH. The author chose the thresholds from the distribution of "
                   "the features before training, and did not tune them to the accuracy. The model learns this formula, so the accuracy in "
                   "Chapter 7 shows how well the model recovers a known rule and not how well it predicts real failures.")
    x.set_par("The system saves the trained model with joblib.",
              "The training script saves the model with joblib, together with a file that holds the SHA-256 of the model file, the feature "
              "list, the library versions and a note about the labels. The application checks all three before it loads the model. If a check "
              "fails, the prediction route returns the status code 503 and the rest of the system keeps working. One function builds the "
              "features for training and for prediction, and it reads only records dated on or before the snapshot date. The prediction route "
              "returns the class and the probability of the HIGH class for each vehicle that is not Inactive.")

    # ---- chapter 6: testing ----------------------------------------------------------------------------------------
    x.set_par("The test plan has three levels: unit, integration and system.",
              "The tests have three levels: unit, integration and system.")
    x.set_par("Unit tests check single functions",
              "Unit tests check single functions, for example the fuel cost calculation, the licence-expiry check, the trip rules and the ML "
              "feature builder. They need no database. pytest runs them.")
    x.set_par("System tests use the full application in a browser.",
              "System tests use the full application in a browser. The tester signs in with each role and checks the cases of Table 6.1. "
              f"The author ran the 12 cases in a browser with the Administrator, Fleet Manager and Operator roles on {asof_text}, and all 12 "
              "passed. The cases that write data used a test vehicle that the author deleted afterwards. An automated test also compares "
              "every dashboard indicator with a direct SQL query on the full dummy dataset.")
    s61 = ["ID", "Function", "Input", "Expected result", "Status"]
    start, end, tbl = x._find_table(s61)
    rows61 = [[ptext(cc) for cc in x._cells(r)] for r in x._rows(tbl)][1:]
    x.set_table(s61, [r[:4] + ["Pass"] for r in rows61])
    x.set_table(["Test level", "Planned cases", "Expected pass", "Expected fail"], [
        ["Unit", str(unit), str(unit), "0"], ["Integration", str(integ), str(integ), "0"], ["System", "12", "12", "0"],
        ["Total", str(auto + 12), str(auto + 12), "0"]], new_header=["Test level", "Cases", "Passed", "Failed"])
    x.insert_after_table(["Test level", "Cases", "Passed", "Failed"],
                         f"pytest ran {unit} unit tests and {integ} integration tests, and all of them passed. The integration tests use the "
                         "Flask test client and a separate test database. They include tests in which two requests arrive at the same moment, "
                         "to show that two overlapping trips for one vehicle cannot both be saved and that a service and a trip cannot both "
                         "start on one vehicle. The run is recorded in report/numbers.json, and the system level is the 12 cases of Table 6.1.",
                         "Unit tests check single functions")

    # ---- chapter 7 ------------------------------------------------------------------------------------------------------
    x.set_par("This chapter shows the results that the system is expected to produce.",
              "This chapter shows the results that the system produced on a dummy dataset that the author generated with a fixed random seed. "
              "The values are not real fleet data. They show the format of the output and the type of conclusion that the system supports. "
              "The script scripts/make_report_figures.py regenerates every figure and every number in this chapter from the database and the model.")
    x.set_table(["Item", "Value"], [
        ["Fleet size", f"50 vehicles ({counts['by_type']['Truck']} trucks, {counts['by_type']['Van']} vans, {counts['by_type']['Pickup']} pickups, {counts['by_type']['Sedan']} sedans)"],
        ["Period", "October 2025 to September 2026 (12 months)"],
        ["Operational records", f"{n0(counts['trips'])} trips, {n0(counts['fuel_records'])} fuel records, {n0(counts['maintenance_records'])} maintenance records and {n0(counts['maintenance_parts'])} parts"],
        ["Drivers and users", f"{counts['drivers']} drivers, 54 vehicle assignments and 3 users (one for each role)"],
        ["Fuel price used", "1.66 SAR/L for diesel, 2.18 SAR/L for petrol"],
        ["Currency", "Saudi Riyal (SAR)"],
        ["ML dataset", "600 vehicle snapshots (12 month ends × 50 vehicles), 480 for training and 120 for testing"]])
    x.set_par("Table 7.2 lists the indicators that the dashboard is expected to show",
              "Table 7.2 lists the indicators that the dashboard shows for the dummy fleet.")
    x.set_table(["Indicator", "Expected value"], [
        ["Total vehicles", str(c["total_vehicles"])], ["Active vehicles", str(c["active"])],
        ["Vehicles under maintenance", str(c["under_maintenance"])],
        ["Total distance", f"{n0(c['total_distance_km'])} km"], ["Total fuel consumed", f"{n0(s['fuel_litres'])} L"],
        ["Fuel cost", f"{n0(c['fuel_cost'])} SAR"], ["Maintenance cost", f"{n0(c['maintenance_cost'])} SAR"],
        ["Fleet fuel efficiency", f"{n2(s['fuel_efficiency_km_per_l'])} km/L"], ["Cost per kilometre", f"{n3(s['cost_per_km'])} SAR/km"],
        ["Fleet utilisation", f"{s['utilisation_pct']:.0f}%"]], new_header=["Indicator", "Value"])
    x.set_par("Figure 7.1 shows monthly fuel use.",
              f"Figure 7.1 shows monthly fuel use. The highest value is {n0(top_fuel['fuel_litres'])} L in {month_name(top_fuel['month'])}. "
              f"The lowest value is {n0(low_fuel['fuel_litres'])} L in {month_name(low_fuel['month'])}. The dummy data includes a 7% rise in "
              "consumption per kilometre in June, July and August to represent air-conditioning load in summer.")
    x.set_table(["Month", "Distance (km)", "Fuel (L)", "Fuel cost (SAR)", "Maint. cost (SAR)"],
                [[short(r["month"]), n0(r["distance_km"]), n0(r["fuel_litres"]), n0(r["fuel_cost"]), n0(r["maintenance_cost"])] for r in months])
    if (sum_l, sum_fc, sum_mc) == (round(s["fuel_litres"]), round(c["fuel_cost"]), round(c["maintenance_cost"])):
        tail = f"The monthly rows add up to the totals in Table 7.2 ({n0(sum_l)} L and {n0(sum_fc)} SAR for fuel)."
    else:
        tail = (f"The monthly rows sum to {n0(sum_l)} L and {n0(sum_fc)} SAR. Table 7.2 shows {n0(s['fuel_litres'])} L and "
                f"{n0(c['fuel_cost'])} SAR because the totals come from unrounded values.")
    x.set_par("The monthly rows sum to", tail)
    x.set_par("Fuel is expected to make up",
              f"Fuel makes up {pct(fuel_share)} of the operating cost. Maintenance makes up {pct(1 - fuel_share)}. Fuel cost is stable from "
              f"month to month, between {n0(min(fuel_costs))} SAR and {n0(max(fuel_costs))} SAR. Maintenance cost varies more, from "
              f"{n0(min_maint['maintenance_cost'])} SAR in {month_name(min_maint['month'])} to {n0(max_maint['maintenance_cost'])} SAR in "
              f"{month_name(max_maint['month'])}. Figure 7.2 shows the two cost types.")
    x.set_par("Table 7.4 groups the results by vehicle type.",
              f"Table 7.4 groups the results by vehicle type. Trucks travel {pct(truck['distance_km'] / c['total_distance_km'])} of the total "
              f"distance and use {pct(truck['fuel_cost'] / c['fuel_cost'])} of the fuel cost. {best['type']}s have the best efficiency at "
              f"{n2(best['km_per_l'])} km/L. {van_like['type']}s have the highest maintenance cost among the four types at "
              f"{n0(van_like['maintenance_cost'])} SAR.")
    x.set_table(["Type", "Vehicles", "Distance (km)", "Fuel (L)", "km/L", "Cost per km (SAR)"],
                [[t["type"], str(t["vehicles"]), n0(t["distance_km"]), n0(t["fuel_litres"]), n2(t["km_per_l"]), n3(t["cost_per_km"])] for t in types])
    x.set_table(["Vehicle", "Type", "Cost per km (SAR)", "Efficiency (km/L)", "Maint. cost (SAR)"],
                [[t["vehicle"], t["type"], n3(t["cost_per_km"]), n2(t["km_per_l"]), n0(t["maintenance_cost"])] for t in d["top10"][:5]])
    x.set_par("The Random Forest model trains on 480 records",
              f"The Random Forest model trains on {strat['train_rows']} records and tests on {strat['test_rows']} records. The accuracy on the "
              f"test set is {100 * acc:.1f}%. Table 7.6 gives precision, recall and F1-score for each class.")
    x.set_table(["Class", "Precision", "Recall", "F1-score", "Support"],
                [[k, n2(pc[k]["precision"]), n2(pc[k]["recall"]), n2(pc[k]["f1-score"]), str(pc[k]["support"])] for k in ("LOW", "MEDIUM", "HIGH")]
                + [["Weighted average", n2(wp), n2(wr), n2(wf), str(strat["test_rows"])]])
    x.set_par("Figure 7.5 shows the confusion matrix.",
              f"Figure 7.5 shows the confusion matrix. Most errors occur between neighbouring classes. {confusions(cm[0][2], 'LOW', 'HIGH')}, "
              f"and {confusions(cm[2][0], 'HIGH', 'LOW', first=False)}. The MEDIUM class is the hardest, with a precision of {n2(pc['MEDIUM']['precision'])} "
              f"and a recall of {n2(pc['MEDIUM']['recall'])}, because a MEDIUM score lies between two thresholds and the noise in the labels moves "
              f"many snapshots across them. The recall of the HIGH class is {n2(pc['HIGH']['recall'])}. This means the model misses about one "
              "vehicle in three that needs urgent service. A real deployment must improve this value before staff rely on the output.")
    x.insert_after("Figure 7.5 shows the confusion matrix.",
                   "Snapshots of one vehicle are not independent, so the author also held out whole vehicles. With "
                   f"{grp['test_vehicles']} of the 50 vehicles held out, the accuracy is {100 * gacc:.1f}%. This test set is small, and a "
                   f"different choice of vehicles gives a different value, so the difference from {100 * acc:.1f}% is not a result in itself. "
                   "The report uses the stratified split as the main result.")
    x.set_par("Figure 7.6 shows feature importance.",
              f"Figure 7.6 shows feature importance. The three most important features are {top3[0][0]} ({n2(top3[0][1])}), {top3[1][0]} "
              f"({n2(top3[1][1])}) and {top3[2][0]} ({n2(top3[2][1])}). This agrees with the rule used to build the dummy labels, which uses "
              f"vehicle age, mileage, distance since service and earlier breakdowns. The four features that are not in the rule carry {n2(outside_sum)} "
              f"of the importance together, because they correlate with the features that are in it. Earlier breakdowns is in the rule but has the "
              f"lowest importance ({n2(importance['prev_breakdowns'])}), because its weight in the rule is small.")
    x.set_par("The prediction page classifies the 50 vehicles",
              f"The prediction page classifies the 50 vehicles of the dummy fleet as of {asof_text}. The result is {summ['LOW']} vehicles at LOW "
              f"risk, {summ['MEDIUM']} at MEDIUM risk and {summ['HIGH']} at HIGH risk. Figure 7.7 shows the distribution. Table 7.7 lists the six "
              "vehicles with the highest probability of the HIGH class.")
    x.set_table(["Vehicle", "Age (y)", "Mileage (km)", "Break-downs", "km since service", "Risk", "P(HIGH)"],
                [[r["vehicle"], n0(r["age"]), n0(r["mileage"]), n0(r["breakdowns"]), n0(r["km_since_service"]), r["risk"], f"{100 * r['p_high']:.0f}%"]
                 for r in rows[:6]])
    x.set_par("Figure 7.8 shows the expected layout",
              "Figure 7.8 shows the top of the dashboard. Six cards show total vehicles, active vehicles, vehicles under maintenance, distance, "
              "fuel cost and maintenance cost. A strip below the cards shows fuel consumed, fuel efficiency, cost per kilometre and utilisation. "
              "Together these ten values match Table 7.2. The charts from Figures 7.1 to 7.4 appear below. The dashboard builds its answer "
              f"in {seconds:.2f} s for the 50 vehicles, against the target of 3 s.")
    x.set_par("The dummy data follows simple rules that the author wrote.",
              "The dummy data follows simple rules that the author wrote. The labels come from a formula with added noise. The model therefore "
              "learns the formula, and the accuracy will differ on real data. The author must replace the dummy dataset with real maintenance "
              "history before it claims that the model predicts real failures. The report must state the training data and its limits. Section "
              "7.7 also classifies vehicles that appear in the training snapshots. That result is a demonstration and not an independent test. "
              "Section 7.6 uses held-out data, and it reports both a split of snapshots and a split of whole vehicles.")

    # ---- chapter 8 -------------------------------------------------------------------------------------------------------
    x.set_par("This project designs a web-based fleet management system.",
              "This project builds a web-based fleet management system. The system stores vehicle, driver, trip, fuel and maintenance records "
              "in one relational database. It gives role-based access, a dashboard and Python analytics. It also includes a Random Forest model "
              "that classifies the maintenance risk of each vehicle.")
    x.set_par("The author defined the requirements, the database and the modules.",
              "The author built the database, the Flask API with role-based access, and nine screens.")
    x.set_par("The author defined a machine-learning workflow with eight input features.",
              f"The author built a machine-learning workflow with eight input features. The trained model reaches {100 * acc:.1f}% accuracy on "
              "the dummy test set.")
    x.set_par("The author defined 12 detailed test cases and a plan of 48 cases at three levels.",
              f"The author tested the system with {auto} automated tests and 12 system test cases, and all of them passed.")
    x.set_par("The author produced expected results with a documented dummy dataset.",
              "The author produced results from a documented dummy dataset, and wrote the scripts that regenerate the figures and the numbers.")
    x.insert_after("The system does not track vehicles in real time.",
                   "The system runs on the Flask development server over HTTP. HTTPS, e-mail notifications and password reset are not included.")
    return x


def main():
    d = json.loads(NUMBERS.read_text(encoding="utf-8"))
    x = build(d)
    x.xml = unique_ids(x.xml)
    ET.fromstring(x.xml.encode("utf-8"))   # must still be well-formed XML
    with zipfile.ZipFile(SOURCE) as zin, zipfile.ZipFile(TARGET, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/document.xml":
                data = x.xml.encode("utf-8")
            name = pathlib.PurePosixPath(item.filename).name
            for figure, image in FIGURE_IMAGES.items():
                if item.filename == f"word/media/{image}":
                    data = (FIGURES / figure).read_bytes()
            zout.writestr(item, data)
    print(f"{x.edits} edits applied; wrote {TARGET.name}")


if __name__ == "__main__":
    main()
