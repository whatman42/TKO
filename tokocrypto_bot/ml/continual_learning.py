"""
MODULE: tokocrypto_bot.ml.continual_learning
DESCRIPTION: Minimal safe self-learning — train from journal, validate, promote champion.
Fail-closed: never promote on insufficient data or training errors.
"""
from __future__ import annotations

import logging
import pickle
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from tokocrypto_bot.strategy.features import EXPECTED_FEATURE_COLUMNS, FEATURE_VERSION
from tokocrypto_bot.ml.model_loader import resolve_model_path, MODEL_FILENAME

logger = logging.getLogger("NVRA.ContinualLearning")

MIN_SAMPLES_DEFAULT = 50
MIN_ACCURACY_DEFAULT = 0.55


@dataclass
class RetrainResult:
    attempted: bool
    promoted: bool
    reason: str
    n_samples: int = 0
    accuracy: float = 0.0
    model_path: Optional[str] = None


class _LogisticBinaryModel:
    """Pure-numpy logistic regression (no sklearn). Compatible with inference.predict_proba."""

    def __init__(self, weights: np.ndarray, bias: float, feature_order: List[str]):
        self.weights = np.asarray(weights, dtype=np.float64)
        self.bias = float(bias)
        self.feature_order = list(feature_order)
        self.classes_ = np.array([0, 1])

    def _x(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        return X

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        X = self._x(X)
        z = X @ self.weights + self.bias
        z = np.clip(z, -30, 30)
        p1 = 1.0 / (1.0 + np.exp(-z))
        p0 = 1.0 - p1
        return np.column_stack([p0, p1])

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.predict_proba(X)[:, 1]


def _matrix_from_rows(rows: List[Dict[str, Any]]) -> Tuple[np.ndarray, np.ndarray]:
    X, y = [], []
    for r in rows:
        feats = r["features"]
        try:
            vec = [float(feats.get(c, 0.0) or 0.0) for c in EXPECTED_FEATURE_COLUMNS]
        except (TypeError, ValueError):
            continue
        if not np.all(np.isfinite(vec)):
            continue
        X.append(vec)
        y.append(int(r["label"]))
    if not X:
        return np.zeros((0, len(EXPECTED_FEATURE_COLUMNS))), np.zeros((0,))
    return np.asarray(X, dtype=np.float64), np.asarray(y, dtype=np.float64)


def _train_logistic(X: np.ndarray, y: np.ndarray, lr: float = 0.1, epochs: int = 400) -> _LogisticBinaryModel:
    n, d = X.shape
    mu = X.mean(axis=0)
    sigma = X.std(axis=0)
    sigma = np.where(sigma < 1e-8, 1.0, sigma)
    Xs = (X - mu) / sigma
    w = np.zeros(d, dtype=np.float64)
    b = 0.0
    for _ in range(epochs):
        z = np.clip(Xs @ w + b, -30, 30)
        p = 1.0 / (1.0 + np.exp(-z))
        err = p - y
        w -= lr * (Xs.T @ err) / n
        b -= lr * float(err.mean())
    w_eff = w / sigma
    b_eff = b - float((mu / sigma) @ w)
    return _LogisticBinaryModel(w_eff, b_eff, list(EXPECTED_FEATURE_COLUMNS))


def _accuracy(model: _LogisticBinaryModel, X: np.ndarray, y: np.ndarray) -> float:
    if len(y) == 0:
        return 0.0
    pred = (model.predict_proba(X)[:, 1] >= 0.5).astype(np.float64)
    return float((pred == y).mean())


def default_champion_path() -> Path:
    resolved = resolve_model_path()
    if resolved is not None:
        return Path(resolved)
    return Path(__file__).resolve().parent.parent.parent / "models" / MODEL_FILENAME


class ContinualLearningEngine:
    def __init__(
        self,
        db_manager,
        *,
        min_samples: int = MIN_SAMPLES_DEFAULT,
        min_accuracy: float = MIN_ACCURACY_DEFAULT,
        model_path: Optional[Path] = None,
    ):
        self.db = db_manager
        self.min_samples = int(min_samples)
        self.min_accuracy = float(min_accuracy)
        self.model_path = Path(model_path) if model_path else default_champion_path()
        self._cycles_since_train = 0
        self.retrain_every_cycles = 50

    def on_cycle(self, ml_engine=None) -> RetrainResult:
        self._cycles_since_train += 1
        if self._cycles_since_train < self.retrain_every_cycles:
            return RetrainResult(False, False, "WAIT_CYCLE", n_samples=0)
        self._cycles_since_train = 0
        return self.retrain_and_promote(ml_engine=ml_engine)

    def retrain_and_promote(self, ml_engine=None) -> RetrainResult:
        try:
            from tokocrypto_bot.persistence.ml_journal import MLJournal
            journal = MLJournal(self.db)
            rows = journal.fetch_training_rows()
            n = len(rows)
            if n < self.min_samples:
                return RetrainResult(True, False, f"INSUFFICIENT_SAMPLES_{n}<{self.min_samples}", n_samples=n)

            X, y = _matrix_from_rows(rows)
            if len(y) < self.min_samples:
                return RetrainResult(True, False, "INSUFFICIENT_CLEAN_SAMPLES", n_samples=len(y))
            if len(np.unique(y)) < 2:
                return RetrainResult(True, False, "SINGLE_CLASS_LABELS", n_samples=len(y))

            split = max(1, int(len(y) * 0.8))
            Xtr, ytr = X[:split], y[:split]
            Xte, yte = X[split:], y[split:]
            if len(yte) < 5:
                Xte, yte = Xtr, ytr

            model = _train_logistic(Xtr, ytr)
            acc = _accuracy(model, Xte, yte)
            if acc < self.min_accuracy:
                return RetrainResult(True, False, f"ACCURACY_BELOW_GATE_{acc:.3f}", n_samples=len(y), accuracy=acc)

            version = f"CL_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_acc{acc:.3f}"
            artifact = {
                "model": model,
                "version": version,
                "feature_version": FEATURE_VERSION,
                "feature_columns": list(EXPECTED_FEATURE_COLUMNS),
                "notes": "Continual-learning promote; pure-numpy logistic.",
                "train_samples": int(len(y)),
                "holdout_accuracy": float(acc),
            }
            path = self.model_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_file():
                bak = path.with_suffix(path.suffix + f".bak_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
                try:
                    shutil.copy2(path, bak)
                except OSError as e:
                    logger.warning("backup failed: %s", e)
            with open(path, "wb") as f:
                pickle.dump(artifact, f, protocol=4)

            if ml_engine is not None and hasattr(ml_engine, "reload"):
                try:
                    ml_engine.reload()
                except Exception as e:
                    logger.error("ml_engine.reload failed after promote: %s", e)
                    return RetrainResult(True, True, f"PROMOTED_RELOAD_FAILED_{e}", n_samples=len(y), accuracy=acc, model_path=str(path))

            logger.warning("CL promote OK version=%s acc=%.3f n=%d path=%s", version, acc, len(y), path)
            return RetrainResult(True, True, "PROMOTED", n_samples=len(y), accuracy=acc, model_path=str(path))
        except Exception as e:
            logger.error("retrain_and_promote failed (fail-closed): %s", e)
            return RetrainResult(True, False, f"ERROR_{type(e).__name__}", n_samples=0)
