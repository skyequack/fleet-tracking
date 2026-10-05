"""Offline training of the maintenance-risk model (ARCHITECTURE.md 5.4 and 10). The web app never trains.

    python ml/train.py                    # train on fleet_db, write ml/model.joblib, ml/model.meta.json, ml/figures/
    python ml/train.py --db test          # train on fleet_test instead
    python ml/train.py --out-dir some/dir # write the files somewhere else

Steps: build the 8 features for 12 monthly snapshots of every vehicle (600 rows), add the synthetic labels, split
80:20 stratified (random_state=42), fit RandomForestClassifier(n_estimators=200, max_depth=8), print accuracy,
per-class precision/recall/F1 and the confusion matrix, and save the model, its metadata (with a SHA-256 of the
model file and the label_source note) and two figures. A vehicle-grouped split is also evaluated (gap G3).
"""
import argparse
import hashlib
import json
import pathlib
import platform
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import joblib  # noqa: E402
import matplotlib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import sklearn  # noqa: E402
from sklearn.ensemble import RandomForestClassifier  # noqa: E402
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix  # noqa: E402
from sklearn.model_selection import GroupShuffleSplit, train_test_split  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from app.services import features as F  # noqa: E402

SEED = 42
TEST_SIZE = 0.2
HYPERPARAMETERS = dict(n_estimators=200, max_depth=8, random_state=SEED)
MIN_CLASS_SHARE = 0.15   # below this, class_weight="balanced" is used and reported (R13)
BLUE = "#2a78d6"


def snapshot_dates(today):
    """Month ends of the 12 complete months before `today` (e.g. 31 Oct 2025 ... 30 Sep 2026)."""
    from app.services.analytics import default_window
    window = default_window(today)
    return [p.to_timestamp(how="end").date() for p in pd.period_range(window.start, window.end, freq="M")]


def build_dataset(dates, seed=SEED):
    """One row per vehicle per snapshot date: the 8 features, the label, the vehicle and the date."""
    frames = []
    for day in dates:
        f = F.build_features(day)
        f["as_of"] = day
        frames.append(f)
    data = pd.concat(frames).rename_axis("vehicle_id").reset_index()
    data["label"] = F.risk_labels(data, np.random.default_rng(seed)).values
    return data


def class_weight_for(y):
    shares = y.value_counts(normalize=True)
    return "balanced" if shares.min() < MIN_CLASS_SHARE else None


def evaluate(model, X_test, y_test):
    pred = model.predict(X_test)
    report = classification_report(y_test, pred, labels=F.CLASSES, output_dict=True, zero_division=0)
    return {
        "accuracy": round(float(accuracy_score(y_test, pred)), 4),
        "per_class": {c: {k: round(float(report[c][k]), 4) for k in ("precision", "recall", "f1-score")}
                      | {"support": int(report[c]["support"])} for c in F.CLASSES},
        "confusion_matrix": confusion_matrix(y_test, pred, labels=F.CLASSES).tolist(),
    }


def train_model(data, seed=SEED):
    """Fit and evaluate. Returns (model, meta pieces). The saved model is the one fitted on the stratified train set."""
    X, y, groups = data[F.FEATURES], data["label"], data["vehicle_id"]
    weight = class_weight_for(y)
    params = dict(HYPERPARAMETERS, random_state=seed, class_weight=weight)

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=TEST_SIZE, stratify=y, random_state=seed)
    model = RandomForestClassifier(**params).fit(X_train, y_train)
    stratified = evaluate(model, X_test, y_test)
    stratified.update(train_rows=int(len(X_train)), test_rows=int(len(X_test)))

    # Gap G3: snapshots of one vehicle are not independent, so also hold out whole vehicles
    train_idx, test_idx = next(GroupShuffleSplit(n_splits=1, test_size=TEST_SIZE, random_state=seed).split(X, y, groups))
    grouped_model = RandomForestClassifier(**params).fit(X.iloc[train_idx], y.iloc[train_idx])
    grouped = evaluate(grouped_model, X.iloc[test_idx], y.iloc[test_idx])
    grouped.update(train_rows=int(len(train_idx)), test_rows=int(len(test_idx)),
                   test_vehicles=int(groups.iloc[test_idx].nunique()))

    importances = dict(sorted(zip(F.FEATURES, (round(float(v), 4) for v in model.feature_importances_)),
                              key=lambda kv: -kv[1]))
    return model, {"class_weight": weight, "stratified_split": stratified, "grouped_split": grouped,
                   "feature_importance": importances}


def sha256_of(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def dataset_fingerprint(data):
    payload = data[F.FEATURES + ["label"]].round(6).to_csv(index=False).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def save_figures(meta, out_dir):
    fig_dir = pathlib.Path(out_dir) / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    cm = np.array(meta["stratified_split"]["confusion_matrix"])
    fig, ax = plt.subplots(figsize=(5, 4.2))
    ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(3), F.CLASSES)
    ax.set_yticks(range(3), F.CLASSES)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("Actual")
    ax.set_title("Confusion matrix (20% test split)")
    for i in range(3):
        for j in range(3):
            ax.text(j, i, int(cm[i, j]), ha="center", va="center", color="white" if cm[i, j] > cm.max() / 2 else "black")
    fig.tight_layout()
    fig.savefig(fig_dir / "confusion_matrix.png", dpi=150)
    plt.close(fig)

    items = list(meta["feature_importance"].items())[::-1]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.barh([k for k, _ in items], [v for _, v in items], color=BLUE)
    ax.set_xlabel("Importance (mean decrease in impurity)")
    ax.set_title("Feature importance")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(fig_dir / "feature_importance.png", dpi=150)
    plt.close(fig)
    return fig_dir


def print_report(meta, data):
    s, g = meta["stratified_split"], meta["grouped_split"]
    print("class counts:", data["label"].value_counts().reindex(F.CLASSES).to_dict(),
          "| class_weight:", meta["class_weight"])
    print(f"\nstratified 80:20 split ({s['train_rows']} train / {s['test_rows']} test)   accuracy {s['accuracy']:.4f}")
    print(f"{'class':<8}{'precision':>10}{'recall':>9}{'f1':>8}{'support':>9}")
    for c in F.CLASSES:
        m = s["per_class"][c]
        print(f"{c:<8}{m['precision']:>10.3f}{m['recall']:>9.3f}{m['f1-score']:>8.3f}{m['support']:>9}")
    print("confusion matrix (rows actual, columns predicted; LOW, MEDIUM, HIGH):")
    for row in s["confusion_matrix"]:
        print("   ", row)
    print(f"\nvehicle-grouped split ({g['test_vehicles']} vehicles held out, {g['test_rows']} rows)   "
          f"accuracy {g['accuracy']:.4f}")
    print("\nfeature importance:")
    for name, value in meta["feature_importance"].items():
        print(f"  {name:<22}{value:.4f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", choices=["dev", "test"], default="dev")
    ap.add_argument("--out-dir", default=str(pathlib.Path(__file__).resolve().parent))
    args = ap.parse_args()

    from app import clock, create_app
    from app.config import Config, TestConfig
    from app.extensions import db  # noqa: F401

    app = create_app(TestConfig if args.db == "test" else Config)
    with app.app_context():
        dates = snapshot_dates(clock.today())
        data = build_dataset(dates)
    model, meta = train_model(data)
    print_report(meta, data)

    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    model_path, meta_path = out / "model.joblib", out / "model.meta.json"
    joblib.dump(model, model_path)
    meta.update({
        "model_file": model_path.name, "sha256": sha256_of(model_path), "features": F.FEATURES, "classes": F.CLASSES,
        "hyperparameters": {k: v for k, v in HYPERPARAMETERS.items()}, "random_state": SEED,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "snapshots": {"dates": [d.isoformat() for d in dates], "rows": int(len(data)),
                      "vehicles": int(data["vehicle_id"].nunique()),
                      "class_counts": data["label"].value_counts().reindex(F.CLASSES).astype(int).to_dict()},
        "dataset_fingerprint": dataset_fingerprint(data),
        "label_source": F.label_source(),
        "libraries": {"python": platform.python_version(), "scikit-learn": sklearn.__version__,
                      "pandas": pd.__version__, "numpy": np.__version__, "joblib": joblib.__version__},
    })
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    figures = save_figures(meta, out)
    print(f"\nsaved {model_path.name} ({meta['sha256'][:12]}...), {meta_path.name}, and figures in {figures}")


if __name__ == "__main__":
    main()
