"""Regenerate the report's Figures 7.1 to 7.8 and every number Chapters 6 and 7 quote, from the real build.

    python scripts/make_report_figures.py                 # figures + report/numbers.json (reads fleet_db and the model)
    python scripts/make_report_figures.py --with-tests    # also run pytest and record the pass/fail counts per level

Nothing in the report should be typed by hand (ARCHITECTURE.md G1): the figures are written to report/figures/ at the
pixel sizes of the images in the Word report, and report/numbers.json holds the values for the tables and the text.
"""
import argparse
import json
import pathlib
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

BLUE, ORANGE, INK, MUTED, GRID = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e1e0d9"
OUT = ROOT / "report"
# pixel size of each figure's image in the Word report (so the new picture is not stretched)
SIZES = {"7_1": (1280, 640), "7_2": (1280, 640), "7_3": (1280, 600), "7_4": (1280, 640), "7_5": (880, 760),
         "7_6": (1280, 680), "7_7": (919, 600), "7_8": (1320, 740)}


def canvas(key):
    w, h = SIZES[key]
    fig = plt.figure(figsize=(w / 100, h / 100), dpi=100)
    return fig, fig.add_subplot(111)


def tidy(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors=MUTED)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def save(fig, key):
    path = OUT / "figures" / f"figure_{key}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=100, facecolor="white")
    plt.close(fig)
    w, h = SIZES[key]
    return path


def collect():
    from app import clock, create_app
    from app.extensions import db
    from app.models import Driver, FuelRecord, MaintenanceRecord, MaintenancePart, Trip, Vehicle
    from app.services import analytics as an
    from app.services import features as F

    app = create_app()
    with app.app_context():
        window = an.default_window(clock.today())
        frames = an.load_frames(window)
        table = an.vehicle_table(frames, window)
        fleet = an.fleet_indicators(table, window)
        types = an.by_type(table, window)
        months = an.monthly(frames, window)
        import time
        timings = []
        for _ in range(5):
            started = time.perf_counter()
            dash = an.build_dashboard(window)
            timings.append(time.perf_counter() - started)

        month = func.date_format(Trip.end_date, "%Y-%m").label("month")
        distance_by_month = {m: float(d) for m, d in db.session.execute(
            select(month, func.sum(Trip.distance)).where(
                Trip.status == "Completed", Trip.end_date.between(window.start, window.end)).group_by(month))}
        counts = {"vehicles": db.session.scalar(select(func.count()).select_from(Vehicle)),
                  "drivers": db.session.scalar(select(func.count()).select_from(Driver)),
                  "trips": db.session.scalar(select(func.count()).select_from(Trip)),
                  "fuel_records": db.session.scalar(select(func.count()).select_from(FuelRecord)),
                  "maintenance_records": db.session.scalar(select(func.count()).select_from(MaintenanceRecord)),
                  "maintenance_parts": db.session.scalar(select(func.count()).select_from(MaintenancePart)),
                  "by_type": {t["type"]: t["vehicles"] for t in types}}

        top = table[table["cost_per_km"].notna()].sort_values(["cost_per_km", "vehicle_id"], ascending=[False, True])
        top_rows = [{"vehicle": r.registration_no, "type": r.type, "cost_per_km": float(r.cost_per_km),
                     "km_per_l": None if r.km_per_l != r.km_per_l else float(r.km_per_l),
                     "maintenance_cost": float(r.maint_cost)} for r in top.head(10).itertuples()]

        predictor = app.extensions["predictor"]
        prediction = None
        if hasattr(predictor, "predict"):
            now = clock.today()
            feats = F.build_features(now)
            live = table[table["status"] != "Inactive"]["vehicle_id"].tolist()
            scored = predictor.predict(feats.loc[live])
            regs = table.set_index("vehicle_id")["registration_no"]
            rows = []
            for vid, r in scored.sort_values("p_high", ascending=False).iterrows():
                f = feats.loc[vid]
                rows.append({"vehicle": regs[vid], "risk": r["risk"], "p_high": float(r["p_high"]),
                             "age": float(f["vehicle_age"]), "mileage": float(f["current_mileage"]),
                             "breakdowns": float(f["prev_breakdowns"]), "km_since_service": float(f["km_since_service"])})
            prediction = {"as_of": now.isoformat(), "rows": rows,
                          "summary": {c: sum(1 for r in rows if r["risk"] == c) for c in F.CLASSES}}
        meta = getattr(predictor, "meta", None)

    return {"dashboard_seconds": round(sorted(timings)[len(timings) // 2], 3), "window": dash["window"], "counts": counts, "cards": dash["cards"], "strip": dash["strip"], "fleet": {
                k: (None if v is None else float(v)) for k, v in fleet.items()},
            "types": types, "months": [dict(m, distance_km=distance_by_month.get(m["month"], 0.0)) for m in months],
            "top10": top_rows, "model": meta, "prediction": prediction}


def figures(d):
    months = [m["month"] for m in d["months"]]
    labels = [m[2:] for m in months]
    # 7.1 monthly fuel
    fig, ax = canvas("7_1")
    ax.bar(labels, [m["fuel_litres"] / 1000 for m in d["months"]], color=BLUE, width=0.6)
    ax.set_ylabel("Fuel (thousand litres)", color=MUTED)
    ax.set_xlabel("Month (YY-MM)", color=MUTED)
    plt.setp(ax.get_xticklabels(), rotation=45)
    tidy(ax)
    fig.tight_layout()
    save(fig, "7_1")
    # 7.2 monthly fuel and maintenance cost
    fig, ax = canvas("7_2")
    x = np.arange(len(months))
    ax.bar(x - 0.2, [m["fuel_cost"] / 1000 for m in d["months"]], 0.38, label="Fuel cost", color=BLUE)
    ax.bar(x + 0.2, [m["maintenance_cost"] / 1000 for m in d["months"]], 0.38, label="Maintenance cost", color=ORANGE)
    ax.set_xticks(x, labels, rotation=45)
    ax.set_ylabel("Cost (thousand SAR)", color=MUTED)
    ax.set_xlabel("Month (YY-MM)", color=MUTED)
    ax.legend(frameon=False, loc="upper left", ncol=2, bbox_to_anchor=(0, 1.12))
    tidy(ax)
    fig.tight_layout()
    save(fig, "7_2")
    # 7.3 efficiency by type
    fig, ax = canvas("7_3")
    names = [t["type"] for t in d["types"]]
    vals = [t["km_per_l"] for t in d["types"]]
    bars = ax.bar(names, vals, color=BLUE, width=0.55)
    ax.bar_label(bars, labels=[f"{v:.2f}" for v in vals], padding=3, color=INK)
    ax.set_ylabel("Efficiency (km/L)", color=MUTED)
    ax.set_ylim(0, max(vals) * 1.15)
    tidy(ax)
    fig.tight_layout()
    save(fig, "7_3")
    # 7.4 top ten cost per km
    fig, ax = canvas("7_4")
    top = d["top10"][::-1]
    bars = ax.barh([t["vehicle"] for t in top], [t["cost_per_km"] for t in top], color=BLUE, height=0.65)
    ax.bar_label(bars, labels=[f"{t['cost_per_km']:.3f}" for t in top], padding=3, color=INK)
    ax.set_xlabel("Cost per km (SAR)", color=MUTED)
    ax.set_xlim(0, top[-1]["cost_per_km"] * 1.12)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.yaxis.grid(False)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors=MUTED)
    fig.tight_layout()
    save(fig, "7_4")
    # 7.5 confusion matrix and 7.6 feature importance, from the model's own evaluation
    m = d["model"]
    cm = np.array(m["stratified_split"]["confusion_matrix"])
    fig, ax = canvas("7_5")
    ax.imshow(cm, cmap="Blues")
    classes = ["LOW", "MEDIUM", "HIGH"]
    ax.set_xticks(range(3), classes)
    ax.set_yticks(range(3), classes)
    ax.set_xlabel("Predicted", color=MUTED)
    ax.set_ylabel("Actual", color=MUTED)
    for i in range(3):
        for j in range(3):
            ax.text(j, i, int(cm[i, j]), ha="center", va="center", fontsize=16,
                    color="white" if cm[i, j] > cm.max() / 2 else INK)
    fig.tight_layout()
    save(fig, "7_5")
    fig, ax = canvas("7_6")
    items = list(m["feature_importance"].items())[::-1]
    bars = ax.barh([k for k, _ in items], [v for _, v in items], color=BLUE, height=0.65)
    ax.bar_label(bars, labels=[f"{v:.2f}" for _, v in items], padding=3, color=INK)
    ax.set_xlabel("Importance (mean decrease in impurity)", color=MUTED)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.yaxis.grid(False)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors=MUTED)
    fig.tight_layout()
    save(fig, "7_6")
    # 7.7 predicted risk distribution
    fig, ax = canvas("7_7")
    counts = d["prediction"]["summary"]
    bars = ax.bar(classes, [counts[c] for c in classes], color=BLUE, width=0.55)
    ax.bar_label(bars, padding=3, color=INK)
    ax.set_ylabel("Number of vehicles", color=MUTED)
    ax.set_ylim(0, max(counts.values()) * 1.15)
    tidy(ax)
    fig.tight_layout()
    save(fig, "7_7")
    # 7.8 dashboard indicators (same layout as the original figure)
    w, h = SIZES["7_8"]
    fig = plt.figure(figsize=(w / 100, h / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.axis("off")
    c, s = d["cards"], d["strip"]
    top_row = [("Total vehicles", f"{c['total_vehicles']:,}"), ("Active vehicles", f"{c['active']:,}"),
               ("Under maintenance", f"{c['under_maintenance']:,}"), ("Total distance", f"{c['total_distance_km']:,.0f} km"),
               ("Fuel cost", f"{c['fuel_cost']:,.0f} SAR"), ("Maintenance cost", f"{c['maintenance_cost']:,.0f} SAR")]
    strip = [("Fuel consumed", f"{s['fuel_litres']:,.0f} L"), ("Efficiency", f"{s['fuel_efficiency_km_per_l']:.2f} km/L"),
             ("Cost per km", f"{s['cost_per_km']:.3f} SAR"), ("Utilisation", f"{s['utilisation_pct']:.1f}%")]

    def tile(x, y, tw, th, label, value, edge, fill, ink):
        ax.add_patch(FancyBboxPatch((x, y), tw, th, boxstyle="round,pad=0,rounding_size=14", fc=fill, ec=edge, lw=2.5))
        ax.text(x + tw / 2, y + th * 0.30, label, ha="center", va="center", fontsize=15, color="#333333")
        ax.text(x + tw / 2, y + th * 0.68, value, ha="center", va="center", fontsize=22, fontweight="bold", color=ink)
    for i, (label, value) in enumerate(top_row):
        tile(44 + (i % 3) * 420, 48 + (i // 3) * 200, 392, 188, label, value, "#1f4e79", "#eaf1f9", "#1f4e79")
    for i, (label, value) in enumerate(strip):
        tile(39 + i * 315, 476, 293, 216, label, value, "#b5791f", "#f6f1e6", "#7a5210")
    fig.savefig(OUT / "figures" / "figure_7_8.png", dpi=100, facecolor="white")
    plt.close(fig)


def run_tests():
    """Run pytest once and count the passes and failures for the unit and the integration level."""
    xml = OUT / "pytest_results.xml"
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "--junitxml", str(xml)], cwd=ROOT,
                          capture_output=True, text=True)
    levels = {"unit": {"passed": 0, "failed": 0, "skipped": 0}, "integration": {"passed": 0, "failed": 0, "skipped": 0}}
    for case in ET.parse(xml).getroot().iter("testcase"):
        level = "unit" if ".unit." in case.get("classname", "") else "integration"
        bad = case.find("failure") is not None or case.find("error") is not None
        skipped = case.find("skipped") is not None
        levels[level]["failed" if bad else "skipped" if skipped else "passed"] += 1
    return {"exit_code": done.returncode, "levels": levels, "summary_line": done.stdout.strip().splitlines()[-1]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--with-tests", action="store_true", help="also run the whole pytest suite and record the counts")
    args = ap.parse_args()
    data = collect()
    OUT.mkdir(exist_ok=True)
    previous = OUT / "numbers.json"
    if args.with_tests:
        data["tests"] = run_tests()
    elif previous.exists():   # keep the last recorded test counts when only the figures are refreshed
        data["tests"] = json.loads(previous.read_text(encoding="utf-8")).get("tests")
    figures(data)
    (OUT / "numbers.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(f"wrote {len(SIZES)} figures to {OUT / 'figures'} and {OUT / 'numbers.json'}")
    if args.with_tests:
        print("tests:", data["tests"]["summary_line"], data["tests"]["levels"])


if __name__ == "__main__":
    main()
