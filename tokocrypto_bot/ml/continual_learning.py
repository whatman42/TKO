"""
MODULE: tokocrypto_bot.ml.continual_learning
DESCRIPTION: CL-1 self-learning — PnL-only labels, walk-forward validation,
anti-regression vs champion, promote rate limit, light drift gate.
Fail-closed on insufficient data, weak metrics, or errors.
"""
from __future__ import annotations

import json
import logging
import pickle
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from tokocrypto_bot.strategy.features import EXPECTED_FEATURE_COLUMNS, FEATURE_VERSION
from tokocrypto_bot.ml.model_loader import resolve_model_path, MODEL_FILENAME

logger = logging.getLogger("NVRA.ContinualLearning")

# CL-1 gates (stricter than CL-0)
MIN_SAMPLES_DEFAULT = 80
MIN_ACCURACY_DEFAULT = 0.55
MIN_PRECISION_DEFAULT = 0.50
MIN_LIFT_DEFAULT = 0.02          # challenger must beat champion holdout acc by ≥2pp absolute... 
# actually use absolute accuracy lift
MIN_ACC_LIFT_DEFAULT = 0.02
RETRAIN_EVERY_CYCLES_DEFAULT = 200
MAX_PROMOTES_PER_DAY = 1
DRIFT_Z_THRESHOLD = 3.5
MIN_HOLDOUT = 15


@dataclass
class RetrainResult:
    attempted: bool
    promoted: bool
    reason: str
    n_samples: int = 0
    accuracy: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    champion_accuracy: float = 0.0
    model_path: Optional[str] = None
    model_version: Optional[str] = None


class _LogisticBinaryModel:
    """Pure-numpy L2 logistic regression. predict_proba → [P0, P1]."""

    def __init__(
        self,
        weights: np.ndarray,
        bias: float,
        feature_order: List[str],
        *,
        mu: Optional[np.ndarray] = None,
        sigma: Optional[np.ndarray] = None,
    ):
        self.weights = np.asarray(weights, dtype=np.float64)
        self.bias = float(bias)
        self.feature_order = list(feature_order)
        self.classes_ = np.array([0, 1])
        self.mu = np.asarray(mu if mu is not None else np.zeros(len(feature_order)), dtype=np.float64)
        self.sigma = np.asarray(sigma if sigma is not None else np.ones(len(feature_order)), dtype=np.float64)

    def _x(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        return (X - self.mu) / self.sigma

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        Xs = self._x(X)
        z = np.clip(Xs @ self.weights + self.bias, -30, 30)
        p1 = 1.0 / (1.0 + np.exp(-z))
        p0 = 1.0 - p1
        return np.column_stack([p0, p1])

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.predict_proba(X)[:, 1]


def _matrix_from_rows(rows: List[Dict[str, Any]]) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    X, y, ts, w = [], [], [], []
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
        ts.append(int(r.get("feature_timestamp") or 0))
        pnl = r.get("realized_pnl_usdt")
        try:
            # weight larger |PnL| outcomes more (capped)
            wt = 1.0 + min(5.0, abs(float(pnl))) if pnl is not None else 1.0
        except (TypeError, ValueError):
            wt = 1.0
        w.append(wt)
    if not X:
        d = len(EXPECTED_FEATURE_COLUMNS)
        return np.zeros((0, d)), np.zeros((0,)), np.zeros((0,)), np.zeros((0,))
    return (
        np.asarray(X, dtype=np.float64),
        np.asarray(y, dtype=np.float64),
        np.asarray(ts, dtype=np.int64),
        np.asarray(w, dtype=np.float64),
    )


def _train_logistic(
    X: np.ndarray,
    y: np.ndarray,
    *,
    sample_weight: Optional[np.ndarray] = None,
    lr: float = 0.05,
    epochs: int = 600,
    l2: float = 0.01,
) -> _LogisticBinaryModel:
    n, d = X.shape
    mu = X.mean(axis=0)
    sigma = X.std(axis=0)
    sigma = np.where(sigma < 1e-8, 1.0, sigma)
    Xs = (X - mu) / sigma
    n1 = max(1.0, float(y.sum()))
    n0 = max(1.0, float(n - y.sum()))
    w1 = n / (2.0 * n1)
    w0 = n / (2.0 * n0)
    sample_w = np.where(y >= 0.5, w1, w0)
    if sample_weight is not None and len(sample_weight) == n:
        sample_w = sample_w * np.asarray(sample_weight, dtype=np.float64)
        sample_w = sample_w * (n / max(1e-8, float(sample_w.sum())))

    w = np.zeros(d, dtype=np.float64)
    b = 0.0
    for _ in range(epochs):
        z = np.clip(Xs @ w + b, -30, 30)
        p = 1.0 / (1.0 + np.exp(-z))
        err = (p - y) * sample_w
        w -= lr * ((Xs.T @ err) / n + l2 * w)
        b -= lr * float(err.mean())
    return _LogisticBinaryModel(w, b, list(EXPECTED_FEATURE_COLUMNS), mu=mu, sigma=sigma)


def _metrics(model: _LogisticBinaryModel, X: np.ndarray, y: np.ndarray) -> Dict[str, float]:
    if len(y) == 0:
        return {"accuracy": 0.0, "precision": 0.0, "recall": 0.0}
    proba = model.predict_proba(X)[:, 1]
    pred = (proba >= 0.5).astype(np.float64)
    acc = float((pred == y).mean())
    tp = float(((pred == 1) & (y == 1)).sum())
    fp = float(((pred == 1) & (y == 0)).sum())
    fn = float(((pred == 0) & (y == 1)).sum())
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    return {"accuracy": acc, "precision": precision, "recall": recall}


def _walk_forward_split(
    X: np.ndarray, y: np.ndarray, ts: np.ndarray, train_frac: float = 0.7
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Time-ordered split (walk-forward style holdout on the most recent window)."""
    order = np.argsort(ts, kind="mergesort")
    X, y, ts = X[order], y[order], ts[order]
    split = max(1, int(len(y) * train_frac))
    if split >= len(y):
        split = max(1, len(y) - 1)
    return X[:split], y[:split], X[split:], y[split:]


def _feature_drift_z(X_train: np.ndarray, X_hold: np.ndarray) -> float:
    """Max abs z-score of holdout mean vs train mean/std (simple drift detector)."""
    if len(X_train) < 5 or len(X_hold) < 5:
        return 0.0
    mu = X_train.mean(axis=0)
    sd = X_train.std(axis=0)
    sd = np.where(sd < 1e-8, 1.0, sd)
    z = np.abs((X_hold.mean(axis=0) - mu) / sd)
    return float(np.nanmax(z)) if z.size else 0.0


def default_champion_path() -> Path:
    resolved = resolve_model_path()
    if resolved is not None:
        return Path(resolved)
    return Path(__file__).resolve().parent.parent.parent / "models" / MODEL_FILENAME


def _load_model_artifact(path: Path) -> Optional[Any]:
    try:
        if not path.is_file():
            return None
        with open(path, "rb") as f:
            obj = pickle.load(f)
        if isinstance(obj, dict) and "model" in obj:
            return obj["model"]
        return obj
    except Exception as e:
        logger.warning("could not load champion for comparison: %s", e)
        return None


def _promote_state_path(model_path: Path) -> Path:
    return model_path.parent / "cl_promote_state.json"


def _read_promote_state(path: Path) -> Dict[str, Any]:
    try:
        if path.is_file():
            return json.loads(path.read_text())
    except Exception:
        pass
    return {}


def _write_promote_state(path: Path, data: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True))
    except OSError as e:
        logger.warning("write promote state failed: %s", e)


class ContinualLearningEngine:
    def __init__(
        self,
        db_manager,
        *,
        min_samples: int = MIN_SAMPLES_DEFAULT,
        min_accuracy: float = MIN_ACCURACY_DEFAULT,
        min_precision: float = MIN_PRECISION_DEFAULT,
        min_acc_lift: float = MIN_ACC_LIFT_DEFAULT,
        model_path: Optional[Path] = None,
        retrain_every_cycles: int = RETRAIN_EVERY_CYCLES_DEFAULT,
        max_promotes_per_day: int = MAX_PROMOTES_PER_DAY,
        pnl_only: bool = True,
        allow_promote: bool = True,
    ):
        self.db = db_manager
        self.min_samples = int(min_samples)
        self.min_accuracy = float(min_accuracy)
        self.min_precision = float(min_precision)
        self.min_acc_lift = float(min_acc_lift)
        self.model_path = Path(model_path) if model_path else default_champion_path()
        self._cycles_since_train = 0
        self.retrain_every_cycles = int(retrain_every_cycles)
        self.max_promotes_per_day = int(max_promotes_per_day)
        self.pnl_only = bool(pnl_only)
        self.allow_promote = bool(allow_promote)

    def on_cycle(self, ml_engine=None, *, execution_mode: str = "PAPER") -> RetrainResult:
        """Periodic retrain. Promote rate-limited; safe to call every worker cycle."""
        self._cycles_since_train += 1
        if self._cycles_since_train < self.retrain_every_cycles:
            return RetrainResult(False, False, "WAIT_CYCLE", n_samples=0)
        self._cycles_since_train = 0
        return self.retrain_and_promote(ml_engine=ml_engine, execution_mode=execution_mode)

    def _promote_allowed_today(self) -> Tuple[bool, str]:
        st_path = _promote_state_path(self.model_path)
        st = _read_promote_state(st_path)
        today = date.today().isoformat()
        if st.get("last_promote_date") == today:
            count = int(st.get("promotes_today", 0))
            if count >= self.max_promotes_per_day:
                return False, f"MAX_PROMOTES_PER_DAY_{self.max_promotes_per_day}"
        return True, "OK"

    def _record_promote(self) -> None:
        st_path = _promote_state_path(self.model_path)
        st = _read_promote_state(st_path)
        today = date.today().isoformat()
        if st.get("last_promote_date") != today:
            st = {"last_promote_date": today, "promotes_today": 0}
        st["promotes_today"] = int(st.get("promotes_today", 0)) + 1
        st["last_promote_date"] = today
        st["last_promote_at"] = datetime.now(timezone.utc).isoformat()
        _write_promote_state(st_path, st)

    def retrain_and_promote(
        self, ml_engine=None, *, execution_mode: str = "PAPER"
    ) -> RetrainResult:
        try:
            from tokocrypto_bot.persistence.ml_journal import MLJournal

            if not self.allow_promote:
                return RetrainResult(True, False, "PROMOTE_DISABLED", n_samples=0)

            ok_day, day_reason = self._promote_allowed_today()
            if not ok_day:
                return RetrainResult(True, False, day_reason, n_samples=0)

            journal = MLJournal(self.db)
            rows = journal.fetch_training_rows(
                pnl_only=self.pnl_only,
                feature_version=FEATURE_VERSION,
                allow_weak_labels=False,
            )
            n = len(rows)
            if n < self.min_samples:
                return RetrainResult(
                    True, False,
                    f"INSUFFICIENT_PNL_SAMPLES_{n}<{self.min_samples}",
                    n_samples=n,
                )

            X, y, ts, sw = _matrix_from_rows(rows)
            if len(y) < self.min_samples:
                return RetrainResult(True, False, "INSUFFICIENT_CLEAN_SAMPLES", n_samples=len(y))
            if len(np.unique(y)) < 2:
                return RetrainResult(True, False, "SINGLE_CLASS_LABELS", n_samples=len(y))

            order = np.argsort(ts, kind="mergesort")
            X, y, ts, sw = X[order], y[order], ts[order], sw[order]
            Xtr, ytr, Xte, yte = _walk_forward_split(X, y, ts, train_frac=0.7)
            split = max(1, int(len(y) * 0.7))
            if split >= len(y):
                split = max(1, len(y) - 1)
            sw_tr = sw[:split]
            if len(yte) < MIN_HOLDOUT:
                return RetrainResult(
                    True, False, f"HOLDOUT_TOO_SMALL_{len(yte)}<{MIN_HOLDOUT}", n_samples=len(y)
                )

            drift_z = _feature_drift_z(Xtr, Xte)
            if drift_z > DRIFT_Z_THRESHOLD:
                return RetrainResult(
                    True, False, f"FEATURE_DRIFT_Z_{drift_z:.2f}", n_samples=len(y)
                )

            model = _train_logistic(Xtr, ytr, sample_weight=sw_tr)
            m = _metrics(model, Xte, yte)
            acc, prec, rec = m["accuracy"], m["precision"], m["recall"]

            if acc < self.min_accuracy:
                return RetrainResult(
                    True, False, f"ACCURACY_BELOW_GATE_{acc:.3f}",
                    n_samples=len(y), accuracy=acc, precision=prec, recall=rec,
                )
            if prec < self.min_precision:
                return RetrainResult(
                    True, False, f"PRECISION_BELOW_GATE_{prec:.3f}",
                    n_samples=len(y), accuracy=acc, precision=prec, recall=rec,
                )

            # Anti-regression vs current champion on same holdout
            champ_acc = 0.0
            champ = _load_model_artifact(self.model_path)
            if champ is not None and hasattr(champ, "predict_proba"):
                try:
                    champ_acc = _metrics(champ, Xte, yte)["accuracy"]  # type: ignore[arg-type]
                except Exception:
                    # Neutral or incompatible model — treat as 0.5 baseline
                    champ_acc = 0.5
            else:
                champ_acc = 0.5  # neutral baseline

            if acc < champ_acc + self.min_acc_lift:
                return RetrainResult(
                    True, False,
                    f"NO_LIFT_vs_CHAMPION_acc={acc:.3f}_champ={champ_acc:.3f}_need+{self.min_acc_lift}",
                    n_samples=len(y), accuracy=acc, precision=prec, recall=rec,
                    champion_accuracy=champ_acc,
                )

            version = (
                f"CL1_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
                f"_acc{acc:.3f}_p{prec:.3f}"
            )
            artifact = {
                "model": model,
                "version": version,
                "feature_version": FEATURE_VERSION,
                "feature_columns": list(EXPECTED_FEATURE_COLUMNS),
                "notes": "CL-1 PnL-only walk-forward logistic (numpy L2).",
                "train_samples": int(len(ytr)),
                "holdout_samples": int(len(yte)),
                "holdout_accuracy": float(acc),
                "holdout_precision": float(prec),
                "holdout_recall": float(rec),
                "champion_holdout_accuracy": float(champ_acc),
                "drift_z": float(drift_z),
                "execution_mode_at_promote": str(execution_mode),
                "pnl_only": True,
            }

            path = self.model_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_file():
                bak = path.with_suffix(
                    path.suffix + f".bak_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
                )
                try:
                    shutil.copy2(path, bak)
                    artifact["backup_path"] = str(bak)
                except OSError as e:
                    logger.warning("backup failed: %s", e)

            with open(path, "wb") as f:
                pickle.dump(artifact, f, protocol=4)

            self._record_promote()

            reload_ok = True
            if ml_engine is not None and hasattr(ml_engine, "reload"):
                try:
                    reload_ok = bool(ml_engine.reload())
                except Exception as e:
                    logger.error("ml_engine.reload failed after promote: %s", e)
                    reload_ok = False

            reason = "PROMOTED" if reload_ok else "PROMOTED_RELOAD_FAILED"
            logger.warning(
                "CL-1 promote OK version=%s acc=%.3f prec=%.3f champ=%.3f n=%d path=%s",
                version, acc, prec, champ_acc, len(y), path,
            )
            return RetrainResult(
                True, True, reason,
                n_samples=len(y), accuracy=acc, precision=prec, recall=rec,
                champion_accuracy=champ_acc, model_path=str(path), model_version=version,
            )
        except Exception as e:
            logger.error("retrain_and_promote failed (fail-closed): %s", e)
            return RetrainResult(True, False, f"ERROR_{type(e).__name__}", n_samples=0)
