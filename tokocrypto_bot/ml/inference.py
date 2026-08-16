"""
MODULE: tokocrypto_bot.ml.inference
DESCRIPTION: Multi-Pair ML Inference Engine with Strict Fallback to NO_TRADE (P1-C).
             Uses cross-platform model_loader for model resolution and integrity validation.
HARDENED Phase 1.2:
  - allow_test_override required for explicit model_path
  - classes_ mapping for [0,1], [1,0], ["DOWN","UP"], ["UP","DOWN"]
  - probability range / sum / dimension validation
  - never clamps malformed outputs into valid probabilities
  - fail-closed on every validation/inference error
"""

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple, List, Union
import numpy as np

from tokocrypto_bot.strategy.features import FeatureFrame, EXPECTED_FEATURE_COLUMNS, FEATURE_VERSION
from tokocrypto_bot.ml.model_loader import resolve_and_validate_model_path

logger = logging.getLogger("NVRA.MLInference")

PROB_SUM_TOLERANCE = 1e-3


@dataclass(frozen=True)
class PredictionResult:
    symbol: str
    timestamp: int
    model_version: str
    feature_version: str
    probability_up: float
    probability_down: float
    confidence: float
    is_valid: bool
    status_code: str
    reason: str = ""


def _normalize_class_label(label: Any) -> Optional[str]:
    if label is None:
        return None
    if isinstance(label, (int, np.integer)):
        if int(label) == 1:
            return "UP"
        if int(label) == 0:
            return "DOWN"
        return None
    if isinstance(label, (float, np.floating)):
        if abs(float(label) - 1.0) < 1e-9:
            return "UP"
        if abs(float(label) - 0.0) < 1e-9:
            return "DOWN"
        return None
    s = str(label).strip().upper()
    if s in ("UP", "1", "TRUE", "LONG", "BUY"):
        return "UP"
    if s in ("DOWN", "0", "FALSE", "SHORT", "SELL"):
        return "DOWN"
    return None


def _extract_probabilities(model: Any, X: np.ndarray) -> Tuple[float, float]:
    if hasattr(model, "predict_proba"):
        raw = model.predict_proba(X)
        probs = np.asarray(raw, dtype=np.float64).reshape(-1)
        if probs.size != 2:
            raise ValueError(f"Unsupported class dimensions: expected 2 probabilities, got {probs.size}")
        if not np.all(np.isfinite(probs)):
            raise ValueError("Non-finite probability in model output")
        if np.any(probs < 0.0) or np.any(probs > 1.0):
            raise ValueError(f"Probability outside [0,1]: {probs.tolist()}")
        if abs(float(np.sum(probs)) - 1.0) > PROB_SUM_TOLERANCE:
            raise ValueError(f"Probability sum != 1.0 (got {float(np.sum(probs)):.6f})")
        classes = getattr(model, "classes_", None)
        if classes is not None:
            classes_arr = list(classes)
            if len(classes_arr) != 2:
                raise ValueError(f"Unsupported classes_ length: {len(classes_arr)}")
            mapped = [_normalize_class_label(c) for c in classes_arr]
            if None in mapped or set(mapped) != {"UP", "DOWN"}:
                raise ValueError(f"Unknown or incomplete class layout: {classes_arr}")
            idx_up = mapped.index("UP")
            idx_down = mapped.index("DOWN")
            return float(probs[idx_up]), float(probs[idx_down])
        return float(probs[1]), float(probs[0])
    if not hasattr(model, "predict"):
        raise ValueError("Model has neither predict_proba nor predict")
    raw_pred = model.predict(X)
    pred_arr = np.asarray(raw_pred, dtype=np.float64).reshape(-1)
    if pred_arr.size != 1:
        raise ValueError(f"predict() returned unexpected size: {pred_arr.size}")
    pred = float(pred_arr[0])
    if not np.isfinite(pred):
        raise ValueError("Non-finite predict() output")
    if pred < 0.0 or pred > 1.0:
        raise ValueError(f"predict() output outside [0,1]: {pred}")
    return pred, 1.0 - pred


class MLInferenceEngine:
    def __init__(self, model_path: Optional[str] = None, allow_test_override: bool = False):
        self.model: Optional[Any] = None
        self.model_version: str = "UNLOADED"
        self.model_path: Optional[str] = None
        self._model_feature_version: Optional[str] = None
        if model_path is not None and not allow_test_override:
            logger.critical("Explicit model_path rejected: allow_test_override=False.")
            self.model = None
            self.model_version = "UNAVAILABLE"
            return
        if model_path is not None and allow_test_override:
            resolved_path, is_valid = model_path, True
        else:
            resolved_path, is_valid = resolve_and_validate_model_path()
        if resolved_path and is_valid:
            self.model_path = str(resolved_path)
            self._load_model()
        else:
            self.model = None
            self.model_version = "UNAVAILABLE"

    def _load_model(self) -> None:
        if not self.model_path:
            return
        try:
            import pickle
            with open(self.model_path, "rb") as f:
                artifact = pickle.load(f)
            if isinstance(artifact, dict) and "model" in artifact:
                self.model = artifact["model"]
                self.model_version = str(artifact.get("version", "1.0.0"))
                meta_fv = artifact.get("feature_version")
                if meta_fv is not None and str(meta_fv) != FEATURE_VERSION:
                    self.model = None
                    self.model_version = "FEATURE_MISMATCH"
                    return
                self._model_feature_version = str(meta_fv) if meta_fv else None
            else:
                self.model = artifact
                self.model_version = "LEGACY_1.0"
        except Exception as e:
            logger.error(f"Failed loading model: {e}")
            self.model = None
            self.model_version = "CORRUPT"

    def is_model_ready(self) -> bool:
        """True only when a champion artifact is loaded and usable for inference."""
        return self.model is not None and self.model_version not in (
            "UNLOADED", "UNAVAILABLE", "CORRUPT", "FEATURE_MISMATCH",
        )

    def _invalid_result(self, feature_frame, status_code, reason):
        return PredictionResult(
            symbol=feature_frame.symbol, timestamp=feature_frame.timestamp,
            model_version=self.model_version, feature_version=feature_frame.feature_version,
            probability_up=0.0, probability_down=0.0, confidence=0.0,
            is_valid=False, status_code=status_code, reason=reason,
        )

    def predict(self, feature_frame: FeatureFrame) -> PredictionResult:
        if self.model is None:
            return self._invalid_result(feature_frame, "MODEL_UNAVAILABLE", "Champion model not loaded.")
        if not feature_frame.is_valid:
            return self._invalid_result(feature_frame, "INVALID_INPUT", f"FeatureFrame invalid: {feature_frame.error_reason}")
        if feature_frame.feature_version != FEATURE_VERSION:
            return self._invalid_result(feature_frame, "FEATURE_MISMATCH", "Feature version mismatch")
        try:
            vector = [feature_frame.features[col] for col in EXPECTED_FEATURE_COLUMNS]
        except KeyError as e:
            return self._invalid_result(feature_frame, "FEATURE_MISMATCH", f"Missing feature: {e}")
        X = np.array([vector], dtype=np.float64)
        if np.isnan(X).any() or np.isinf(X).any():
            return self._invalid_result(feature_frame, "INVALID_INPUT", "NaN or Inf in features")
        try:
            prob_up, prob_down = _extract_probabilities(self.model, X)
            return PredictionResult(
                symbol=feature_frame.symbol, timestamp=feature_frame.timestamp,
                model_version=self.model_version, feature_version=feature_frame.feature_version,
                probability_up=prob_up, probability_down=prob_down,
                confidence=abs(prob_up - prob_down), is_valid=True, status_code="OK", reason="Inference OK",
            )
        except Exception as e:
            return self._invalid_result(feature_frame, "INFERENCE_ERROR", f"Model error: {e}")

    def predict_multi_pair(self, feature_frames):
        return {sym: self.predict(ff) for sym, ff in feature_frames.items()}
