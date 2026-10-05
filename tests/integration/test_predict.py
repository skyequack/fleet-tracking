import json
import pathlib
import subprocess
import sys
from datetime import date

import pytest

from app import models
from app.services.predictor import Predictor
from tests.ml_helpers import trained_predictor

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture
def model(world, tmp_path):
    """A freshly trained throwaway model loaded into the app for one test, then the original state restored."""
    saved = world.extensions["predictor"]
    world.extensions["predictor"] = trained_predictor(tmp_path)[0]
    yield world.extensions["predictor"]
    world.extensions["predictor"] = saved


@pytest.fixture
def fleet(seed):
    ids = [seed(models.Vehicle, registration_no=f"VEH {i}000", year=2014 + i, odometer=40_000 * (i + 1)) for i in range(4)]
    inactive = seed(models.Vehicle, registration_no="OFF 0001", status="Inactive")
    return ids, inactive


def test_roles(model, admin, manager, operator, anon, fleet):
    assert admin.get("/api/predict").status_code == 200 and manager.get("/api/predict").status_code == 200
    assert operator.get("/api/predict").status_code == 403 and anon.get("/api/predict").status_code == 401
    assert operator.get("/api/predict/1").status_code == 403 and anon.get("/api/predict/1").status_code == 401


def test_every_active_vehicle_gets_a_class_and_p_high(model, manager, fleet):
    ids, inactive = fleet
    r = manager.get("/api/predict").json
    assert r["total"] == 4 and {i["vehicle_id"] for i in r["items"]} == set(ids)       # the Inactive vehicle is left out
    for item in r["items"]:
        assert item["risk"] in ("LOW", "MEDIUM", "HIGH") and 0 <= item["p_high"] <= 1
        assert sum(item["probabilities"].values()) == pytest.approx(1, abs=1e-3)
        assert item["registration_no"].startswith("VEH") and "imputed_fuel" in item
    assert r["as_of"] == "2026-10-05"                                                   # the frozen clock


def test_items_are_ordered_by_p_high_and_the_summary_adds_up(model, manager, fleet):
    r = manager.get("/api/predict").json
    p = [i["p_high"] for i in r["items"]]
    assert p == sorted(p, reverse=True)
    assert sum(r["summary"].values()) == r["total"] and set(r["summary"]) == {"LOW", "MEDIUM", "HIGH"}
    assert r["summary"] == {c: sum(1 for i in r["items"] if i["risk"] == c) for c in ("LOW", "MEDIUM", "HIGH")}


def test_the_response_carries_the_label_source_note(model, manager, fleet):
    m = manager.get("/api/predict").json["model"]
    assert m["label_source"].startswith("SYNTHETIC") and "not real failure data" in m["label_source"]
    assert m["accuracy"] is not None and m["test_rows"] == 120


def test_an_older_vehicle_with_more_mileage_scores_higher_risk(model, manager, seed):
    new = seed(models.Vehicle, registration_no="NEW 0001", year=2025, odometer=20_000)
    old = seed(models.Vehicle, registration_no="OLD 0001", year=2016, odometer=290_000)
    items = {i["vehicle_id"]: i for i in manager.get("/api/predict").json["items"]}
    assert items[old]["p_high"] > items[new]["p_high"]


def test_a_vehicle_without_fuel_history_is_flagged_not_failed(model, manager, fleet):
    items = manager.get("/api/predict").json["items"]
    assert all(i["imputed_fuel"] for i in items)                                        # nobody here has two full fills


def test_single_vehicle_returns_the_features_it_was_scored_on(model, manager, fleet):
    ids, _ = fleet
    r = manager.get(f"/api/predict/{ids[0]}").json
    assert r["item"]["vehicle_id"] == ids[0] and set(r["item"]["features"]) == {
        "vehicle_age", "current_mileage", "km_since_service", "prev_breakdowns", "maintenance_events", "avg_monthly_km",
        "avg_fuel_l_per_100km", "prev_maint_cost"}
    assert r["item"]["features"]["vehicle_age"] == 2026 - 2014 and r["item"]["features"]["current_mileage"] == 40_000
    assert r["item"]["risk"] == next(i["risk"] for i in manager.get("/api/predict").json["items"] if i["vehicle_id"] == ids[0])


def test_single_vehicle_errors(model, manager, fleet):
    _, inactive = fleet
    assert manager.get("/api/predict/9999").status_code == 404
    r = manager.get(f"/api/predict/{inactive}")
    assert r.status_code == 422 and "inactive" in r.json["error"]


def test_no_vehicles_is_an_empty_answer_not_an_error(model, manager):
    r = manager.get("/api/predict")
    assert r.status_code == 200 and r.json["items"] == [] and r.json["total"] == 0


def test_a_missing_model_gives_503_and_the_rest_of_the_app_keeps_working(world, manager, fleet):
    saved = world.extensions["predictor"]
    world.extensions["predictor"] = "model.joblib is missing; run ml/train.py"
    try:
        for url in ("/api/predict", "/api/predict/1"):
            r = manager.get(url)
            assert r.status_code == 503 and r.json["error"].startswith("model_unavailable: model.joblib is missing")
        assert manager.get("/api/vehicles").status_code == 200 and manager.get("/api/dashboard").status_code == 200
        assert manager.get("/predict").status_code == 200                                # the screen itself still loads
    finally:
        world.extensions["predictor"] = saved


def test_the_app_starts_without_a_model_file(tmp_path):
    from app import create_app
    from app.config import TestConfig

    class NoModel(TestConfig):
        MODEL_PATH = tmp_path / "nope.joblib"
        MODEL_META_PATH = tmp_path / "nope.meta.json"

    app = create_app(NoModel)
    assert isinstance(app.extensions["predictor"], str) and "missing" in app.extensions["predictor"]


# --- the whole offline pipeline: generated data -> ml/train.py -> files -> predictor ---------------------------------

def test_train_script_end_to_end_on_the_generated_dataset(world, tmp_path):
    import ml.generate_dummy_data as gen
    gen.load(gen.generate(), "test")                                                    # replaces the fleet_test rows
    done = subprocess.run([sys.executable, str(ROOT / "ml" / "train.py"), "--db", "test", "--out-dir", str(tmp_path)],
                          capture_output=True, text=True, cwd=ROOT, timeout=300)
    assert done.returncode == 0, done.stderr[-2000:]
    for needed in ("accuracy", "precision", "recall", "f1", "confusion matrix", "feature importance"):
        assert needed in done.stdout
    meta = json.loads((tmp_path / "model.meta.json").read_text(encoding="utf-8"))
    assert (meta["snapshots"]["rows"], meta["snapshots"]["vehicles"]) == (600, 50)
    assert (meta["stratified_split"]["train_rows"], meta["stratified_split"]["test_rows"]) == (480, 120)
    assert meta["label_source"].startswith("SYNTHETIC") and meta["random_state"] == 42
    assert sum(meta["snapshots"]["class_counts"].values()) == 600
    assert (tmp_path / "figures" / "confusion_matrix.png").exists() and (tmp_path / "figures" / "feature_importance.png").exists()
    predictor = Predictor.load(tmp_path / "model.joblib", tmp_path / "model.meta.json")     # checksum, features, versions
    assert predictor.meta["sha256"] == meta["sha256"]
