"""Small synthetic datasets and a throwaway trained model for tests (no database needed)."""
from datetime import date

import numpy as np
import pandas as pd

from app.services import features as F

# Ranges resemble the seeded fleet (services every ~2,000 km), so all three classes occur in useful numbers.
_RANGES = {"vehicle_age": (1, 10), "current_mileage": (20_000, 300_000), "km_since_service": (0, 3_000),
           "prev_breakdowns": (0, 2), "maintenance_events": (6, 14), "avg_monthly_km": (800, 4_500),
           "avg_fuel_l_per_100km": (8, 32), "prev_maint_cost": (1_000, 7_000)}


def synthetic_dataset(vehicles=50, months=12, seed=0):
    """600 rows by default: `months` snapshots of `vehicles` vehicles, with the real label formula applied."""
    rng = np.random.default_rng(seed)
    n = vehicles * months
    data = pd.DataFrame({name: rng.uniform(lo, hi, n) for name, (lo, hi) in _RANGES.items()})
    data["prev_breakdowns"] = data["prev_breakdowns"].round()
    data["vehicle_id"] = np.tile(np.arange(1, vehicles + 1), months)
    data["as_of"] = np.repeat([date(2026, m, 28) for m in range(1, months + 1)], vehicles)
    data["label"] = F.risk_labels(data, np.random.default_rng(42)).values
    return data


_cache = {}


def trained_predictor(tmp_dir):
    """Train once on synthetic data, save it the way ml/train.py does, and load it through Predictor.load."""
    import json

    import joblib
    import sklearn

    import ml.train as train
    from app.services.predictor import Predictor

    if "bundle" not in _cache:
        data = synthetic_dataset()
        model, meta = train.train_model(data)
        _cache["bundle"] = (data, model, meta)
    data, model, meta = _cache["bundle"]
    model_path, meta_path = tmp_dir / "model.joblib", tmp_dir / "model.meta.json"
    joblib.dump(model, model_path)
    meta = dict(meta, sha256=train.sha256_of(model_path), features=F.FEATURES, classes=F.CLASSES,
                trained_at="2026-10-05T00:00:00+00:00", label_source=F.label_source(),
                libraries={"scikit-learn": sklearn.__version__})
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return Predictor.load(model_path, meta_path), model_path, meta_path, meta
