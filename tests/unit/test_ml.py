"""Unit tests for the label formula, the training helpers and the predictor's checks. No database."""
import json
import pathlib

import numpy as np
import pandas as pd
import pytest

import ml.train as train
from app.config import Config
from app.services import features as F
from app.services.predictor import ModelUnavailable, Predictor
from tests.ml_helpers import synthetic_dataset, trained_predictor


def test_one_conversion_between_km_per_litre_and_litres_per_100km():
    assert F.l_per_100km(10.0) == 10.0
    assert F.l_per_100km(4.0) == 25.0
    assert F.l_per_100km(8.0) == 12.5


def test_risk_score_is_the_documented_weighted_sum():
    row = pd.DataFrame([{"vehicle_age": 8.0, "current_mileage": 250_000.0, "km_since_service": 5_000.0,
                         "prev_breakdowns": 2.0}])
    assert F.risk_score(row).iloc[0] == pytest.approx(0.30 + 0.25 + 0.30 + 0.15)   # each term at its scale = its weight
    assert F.risk_score(row * 0).iloc[0] == 0.0


def test_labels_follow_the_thresholds_when_there_is_no_noise(monkeypatch):
    monkeypatch.setattr(F, "LABEL_NOISE_SD", 0.0)
    low, high = F.LABEL_THRESHOLDS
    rows = pd.DataFrame({"vehicle_age": [0.0, 0.0, 0.0], "current_mileage": [0.0, 0.0, 0.0],
                         "km_since_service": [(low - 0.01) * 5_000 / 0.30, (low + 0.01) * 5_000 / 0.30,
                                              (high + 0.01) * 5_000 / 0.30], "prev_breakdowns": [0.0, 0.0, 0.0]})
    assert F.risk_labels(rows, np.random.default_rng(1)).tolist() == ["LOW", "MEDIUM", "HIGH"]


def test_labels_are_reproducible_and_noise_actually_matters():
    data = synthetic_dataset()
    a = F.risk_labels(data, np.random.default_rng(42))
    b = F.risk_labels(data, np.random.default_rng(42))
    c = F.risk_labels(data, np.random.default_rng(7))
    assert a.equals(b) and not a.equals(c)
    assert set(a) == {"LOW", "MEDIUM", "HIGH"}
    assert a.value_counts(normalize=True).min() > 0.15      # no class needs reweighting with these thresholds


def test_label_source_travels_with_a_clear_synthetic_warning():
    text = F.label_source()
    assert "SYNTHETIC" in text and "not real failure data" in text and str(F.LABEL_NOISE_SD) in text


@pytest.mark.parametrize("shares, expected", [([50, 30, 20], None), ([85, 10, 5], "balanced"), ([70, 15, 15], None)])
def test_class_weight_rule(shares, expected):
    y = pd.Series(["LOW"] * shares[0] + ["MEDIUM"] * shares[1] + ["HIGH"] * shares[2])
    assert train.class_weight_for(y) == expected


def test_training_uses_the_documented_split_and_settings():
    data = synthetic_dataset()
    model, meta = train.train_model(data)
    s, g = meta["stratified_split"], meta["grouped_split"]
    assert (s["train_rows"], s["test_rows"]) == (480, 120)                      # 80:20 on 600 snapshots
    assert sum(c["support"] for c in s["per_class"].values()) == 120
    assert np.array(s["confusion_matrix"]).sum() == 120
    assert g["test_vehicles"] == 10 and g["test_rows"] == 120                    # whole vehicles held out (G3)
    params = model.get_params()
    assert (params["n_estimators"], params["max_depth"], params["random_state"]) == (200, 8, 42)
    assert list(meta["feature_importance"]) != [] and set(meta["feature_importance"]) == set(F.FEATURES)


def test_stratified_split_keeps_class_proportions_in_the_test_set():
    s = train.train_model(synthetic_dataset())[1]["stratified_split"]
    expected = synthetic_dataset()["label"].value_counts(normalize=True)
    for c in F.CLASSES:
        assert s["per_class"][c]["support"] / 120 == pytest.approx(expected[c], abs=0.03)


def test_training_is_deterministic():
    data = synthetic_dataset()
    a, b = train.train_model(data)[1], train.train_model(data)[1]
    assert a == b
    assert train.dataset_fingerprint(data) == train.dataset_fingerprint(synthetic_dataset())


def test_snapshot_dates_are_the_last_days_of_twelve_complete_months():
    from datetime import date
    dates = train.snapshot_dates(date(2026, 10, 5))
    assert len(dates) == 12 and dates[0] == date(2025, 10, 31) and dates[-1] == date(2026, 9, 30)
    assert date(2026, 2, 28) in train.snapshot_dates(date(2026, 3, 9))


def test_figures_are_written(tmp_path):
    meta = train.train_model(synthetic_dataset())[1]
    folder = train.save_figures(meta, tmp_path)
    for name in ("confusion_matrix.png", "feature_importance.png"):
        assert (folder / name).stat().st_size > 5_000


# --- the predictor refuses anything that does not check out ---------------------------------------------------------

def test_a_good_model_loads_and_scores(tmp_path):
    predictor, *_ = trained_predictor(tmp_path)
    frame = synthetic_dataset().iloc[:5].set_index("vehicle_id")
    out = predictor.predict(frame)
    assert {"risk", "p_high", "p_low", "p_medium"} <= set(out.columns)
    assert set(out["risk"]) <= set(F.CLASSES)
    assert ((out[["p_low", "p_medium", "p_high"]].sum(axis=1) - 1).abs() < 1e-9).all()
    assert predictor.summary()["label_source"].startswith("SYNTHETIC")


def test_high_probability_agrees_with_the_class_for_obvious_cases(tmp_path):
    predictor, *_ = trained_predictor(tmp_path)
    worn = pd.DataFrame([dict(vehicle_age=10, current_mileage=300_000, km_since_service=3_000, prev_breakdowns=2,
                              maintenance_events=14, avg_monthly_km=4_000, avg_fuel_l_per_100km=30, prev_maint_cost=7_000)])
    fresh = pd.DataFrame([dict(vehicle_age=1, current_mileage=20_000, km_since_service=0, prev_breakdowns=0,
                               maintenance_events=6, avg_monthly_km=900, avg_fuel_l_per_100km=9, prev_maint_cost=1_000)])
    assert predictor.predict(worn).iloc[0]["risk"] == "HIGH" and predictor.predict(fresh).iloc[0]["risk"] == "LOW"
    assert predictor.predict(worn).iloc[0]["p_high"] > 0.5 > predictor.predict(fresh).iloc[0]["p_high"]


def _fails(model_path, meta_path, fragment):
    with pytest.raises(ModelUnavailable) as e:
        Predictor.load(model_path, meta_path)
    assert fragment in str(e.value)


def test_missing_files(tmp_path):
    _, model_path, meta_path, _ = trained_predictor(tmp_path)
    meta_path.rename(tmp_path / "gone.json")
    _fails(model_path, meta_path, "model.meta.json is missing")
    (tmp_path / "gone.json").rename(meta_path)
    model_path.unlink()
    _fails(model_path, meta_path, "model.joblib is missing")


def test_a_modified_model_file_fails_its_checksum_and_is_never_unpickled(tmp_path, monkeypatch):
    _, model_path, meta_path, _ = trained_predictor(tmp_path)
    model_path.write_bytes(model_path.read_bytes() + b"\x00")
    import joblib
    monkeypatch.setattr(joblib, "load", lambda *a, **k: pytest.fail("a file that fails its checksum must not be loaded"))
    _fails(model_path, meta_path, "checksum")


def test_feature_mismatch_is_refused(tmp_path):
    _, model_path, meta_path, meta = trained_predictor(tmp_path)
    meta_path.write_text(json.dumps(dict(meta, features=list(reversed(F.FEATURES)))), encoding="utf-8")
    _fails(model_path, meta_path, "different features")


def test_library_version_mismatch_is_refused(tmp_path):
    _, model_path, meta_path, meta = trained_predictor(tmp_path)
    meta_path.write_text(json.dumps(dict(meta, libraries={"scikit-learn": "0.0.1"})), encoding="utf-8")
    _fails(model_path, meta_path, "scikit-learn 0.0.1")


def test_a_corrupt_pickle_with_a_matching_checksum_is_still_refused_cleanly(tmp_path):
    _, model_path, meta_path, meta = trained_predictor(tmp_path)
    model_path.write_bytes(b"not a pickle")
    meta_path.write_text(json.dumps(dict(meta, sha256=train.sha256_of(model_path))), encoding="utf-8")
    _fails(model_path, meta_path, "cannot be loaded")


def test_unreadable_metadata_is_refused(tmp_path):
    _, model_path, meta_path, _ = trained_predictor(tmp_path)
    meta_path.write_text("{ not json", encoding="utf-8")
    _fails(model_path, meta_path, "cannot be read")


def test_the_committed_model_loads_with_the_current_code():
    """Fails when the features, scikit-learn or the files drift apart: retrain with ml/train.py."""
    assert pathlib.Path(Config.MODEL_PATH).exists() and pathlib.Path(Config.MODEL_META_PATH).exists()
    predictor = Predictor.load(Config.MODEL_PATH, Config.MODEL_META_PATH)
    meta = predictor.meta
    assert meta["snapshots"]["rows"] == 600 and meta["stratified_split"]["test_rows"] == 120
    assert meta["label_source"].startswith("SYNTHETIC") and meta["hyperparameters"]["n_estimators"] == 200
