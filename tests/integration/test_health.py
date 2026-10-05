from tests.ml_helpers import trained_predictor


def test_health_is_public_and_reports_both_parts(anon, world, tmp_path):
    saved = world.extensions["predictor"]
    world.extensions["predictor"] = trained_predictor(tmp_path)[0]
    try:
        r = anon.get("/api/health")
    finally:
        world.extensions["predictor"] = saved
    assert r.status_code == 200 and r.json == {"status": "ok", "database": "ok", "model": "ok"}


def test_health_exposes_no_data_and_sets_no_cookie(anon, seed):
    from app import models
    seed(models.Vehicle)
    r = anon.get("/api/health")
    assert set(r.json) == {"status", "database", "model"} and "ABC 1234" not in r.text
    assert "Set-Cookie" not in r.headers


def test_a_missing_model_is_degraded_not_down_and_the_reason_is_not_published(anon, world):
    saved = world.extensions["predictor"]
    world.extensions["predictor"] = "model.joblib is missing; run ml/train.py"
    try:
        r = anon.get("/api/health")
    finally:
        world.extensions["predictor"] = saved
    assert r.status_code == 200 and r.json == {"status": "degraded", "database": "ok", "model": "unavailable"}
    assert "joblib" not in r.text


def test_an_unreachable_database_gives_503(anon, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app.extensions import db

    def boom(*a, **k):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))
    monkeypatch.setattr(db.session, "execute", boom)
    r = anon.get("/api/health")
    assert r.status_code == 503 and r.json["status"] == "down" and r.json["database"] == "unavailable"
    assert "refused" not in r.text


def test_health_ignores_the_session_and_only_allows_get(admin):
    assert admin.get("/api/health").status_code == 200
    assert admin.post("/api/health").status_code == 405
