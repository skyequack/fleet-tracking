"""Loads the trained model and scores vehicles (ARCHITECTURE.md 10).

Joblib files are pickles, and unpickling runs code, so the file is loaded only from the configured path and only
after its SHA-256 matches model.meta.json, the feature list matches the builder and the scikit-learn version
matches the one that trained it. Any failure leaves the predictor unavailable: /api/predict answers 503 and the
rest of the app keeps working. The checksum catches corruption and stale or mismatched pairs; it is not a defence
against someone who can write both files.
"""
import hashlib
import json

import joblib
import pandas as pd
import sklearn

from .features import CLASSES, FEATURES


class ModelUnavailable(Exception):
    pass


class Predictor:
    def __init__(self, model, meta):
        self.model, self.meta = model, meta
        self._classes = list(model.classes_)
        missing = set(CLASSES) - set(self._classes)
        if missing:
            raise ModelUnavailable(f"the model does not know the classes {sorted(missing)}")

    @classmethod
    def load(cls, model_path, meta_path):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ModelUnavailable("model.meta.json is missing; run ml/train.py")
        except (OSError, ValueError) as e:
            raise ModelUnavailable(f"model.meta.json cannot be read ({e})")
        try:
            raw = model_path.read_bytes()
        except OSError:
            raise ModelUnavailable("model.joblib is missing; run ml/train.py")
        if hashlib.sha256(raw).hexdigest() != meta.get("sha256"):
            raise ModelUnavailable("model.joblib does not match its checksum in model.meta.json; retrain with ml/train.py")
        if meta.get("features") != FEATURES:
            raise ModelUnavailable("the model was trained on different features than the app builds; retrain")
        trained_with = meta.get("libraries", {}).get("scikit-learn")
        if trained_with != sklearn.__version__:
            raise ModelUnavailable(f"the model was trained with scikit-learn {trained_with}, this is {sklearn.__version__}; retrain")
        try:
            model = joblib.load(model_path)
        except Exception as e:  # a corrupt or incompatible pickle can raise almost anything
            raise ModelUnavailable(f"model.joblib cannot be loaded ({type(e).__name__})")
        return cls(model, meta)

    def predict(self, features):
        """DataFrame indexed by vehicle_id: risk, p_high, and the probability of each class."""
        X = features[FEATURES]
        proba = pd.DataFrame(self.model.predict_proba(X), index=X.index, columns=self._classes)[CLASSES]
        out = pd.DataFrame({"risk": proba.idxmax(axis=1), "p_high": proba["HIGH"]})
        for c in CLASSES:
            out[f"p_{c.lower()}"] = proba[c]
        return out

    def summary(self):
        s = self.meta.get("stratified_split", {})
        g = self.meta.get("grouped_split", {})
        return {"trained_at": self.meta.get("trained_at"), "accuracy": s.get("accuracy"),
                "grouped_accuracy": g.get("accuracy"), "test_rows": s.get("test_rows"),
                "label_source": self.meta.get("label_source")}


def init_predictor(app):
    """Load once at start-up. The result (a Predictor or a reason string) is kept in app.extensions['predictor']."""
    try:
        app.extensions["predictor"] = Predictor.load(app.config["MODEL_PATH"], app.config["MODEL_META_PATH"])
    except ModelUnavailable as e:
        app.extensions["predictor"] = str(e)
        app.logger.warning("prediction model unavailable: %s", e)
