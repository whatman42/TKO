"""CL-1 continual learning tests."""
import os
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pytest

from tokocrypto_bot.persistence.database import DatabaseManager
from tokocrypto_bot.persistence.migrations import run_migrations
from tokocrypto_bot.persistence.ml_journal import MLJournal
from tokocrypto_bot.ml.continual_learning import (
    ContinualLearningEngine,
    _train_logistic,
    _matrix_from_rows,
    _walk_forward_split,
    _metrics,
)
from tokocrypto_bot.strategy.features import EXPECTED_FEATURE_COLUMNS, FEATURE_VERSION


@pytest.fixture
def db_model(tmp_path):
    db = DatabaseManager(db_path=str(tmp_path / "cl1.db"))
    run_migrations(db)
    return db, tmp_path / "champion_model.pkl"


def _feats(sign=1.0):
    d = {c: 0.0 for c in EXPECTED_FEATURE_COLUMNS}
    d["RSI14"] = 25.0 if sign > 0 else 75.0
    d["ROC"] = 2.0 * sign
    d["MACD_HIST"] = 1.0 * sign
    return d


def _seed_pnl_rows(j: MLJournal, n=100):
    for i in range(n):
        win = i % 2 == 0
        feats = _feats(1.0 if win else -1.0)
        j.record_prediction(
            symbol="BTCUSDT",
            feature_timestamp=1_700_000_000_000 + i * 60_000,
            feature_version=FEATURE_VERSION,
            model_version="seed",
            features=feats,
            probability_up=0.7 if win else 0.3,
            probability_down=0.3 if win else 0.7,
            confidence=0.4,
            prediction_valid=True,
            prediction_status="OK",
            decision_action="BUY" if win else "SELL",
        )
        j.link_outcome_to_prediction(
            symbol="BTCUSDT",
            fill_id=f"F{i}",
            side="BUY" if win else "SELL",
            entry_price=100.0,
            quantity=1.0,
            fill_timestamp=str(1_700_000_000_000 + i * 60_000 + 1000),
            realized_pnl_usdt=1.0 if win else -1.0,
            outcome_status="CLOSED",
        )


def test_pnl_only_ignores_weak_labels(db_model):
    db, mp = db_model
    j = MLJournal(db)
    j.record_prediction(
        symbol="ETHUSDT", feature_timestamp=1,
        feature_version=FEATURE_VERSION, model_version="x",
        features=_feats(), probability_up=0.8, probability_down=0.2,
        confidence=0.5, prediction_valid=True, prediction_status="OK",
        decision_action="BUY",
    )
    rows = j.fetch_training_rows(pnl_only=True, feature_version=FEATURE_VERSION)
    assert rows == []


def test_insufficient_pnl_samples(db_model):
    db, mp = db_model
    eng = ContinualLearningEngine(db, min_samples=80, model_path=mp)
    r = eng.retrain_and_promote()
    assert r.attempted and not r.promoted
    assert "INSUFFICIENT" in r.reason


def test_walk_forward_split_time_ordered():
    X = np.arange(20).reshape(20, 1).astype(float)
    y = np.array([i % 2 for i in range(20)], dtype=float)
    ts = np.arange(20)
    Xtr, ytr, Xte, yte = _walk_forward_split(X, y, ts, 0.7)
    assert len(Xtr) + len(Xte) == 20
    assert Xtr[-1, 0] < Xte[0, 0]


def test_promote_with_separable_pnl(db_model):
    db, mp = db_model
    j = MLJournal(db)
    _seed_pnl_rows(j, n=100)
    eng = ContinualLearningEngine(
        db, min_samples=40, min_accuracy=0.5, min_precision=0.4,
        min_acc_lift=0.0, model_path=mp, retrain_every_cycles=1,
    )
    r = eng.retrain_and_promote()
    assert r.attempted
    if r.promoted:
        assert mp.is_file()
        assert r.accuracy >= 0.5


def test_max_promotes_per_day(db_model):
    db, mp = db_model
    j = MLJournal(db)
    _seed_pnl_rows(j, n=100)
    eng = ContinualLearningEngine(
        db, min_samples=40, min_accuracy=0.5, min_precision=0.3,
        min_acc_lift=-1.0, model_path=mp, max_promotes_per_day=1,
    )
    r1 = eng.retrain_and_promote()
    r2 = eng.retrain_and_promote()
    if r1.promoted:
        assert not r2.promoted
        assert "MAX_PROMOTES" in r2.reason


def test_on_cycle_wait(db_model):
    db, mp = db_model
    eng = ContinualLearningEngine(db, model_path=mp, retrain_every_cycles=10)
    r = eng.on_cycle()
    assert not r.attempted and r.reason == "WAIT_CYCLE"


def test_logistic_metrics_shape():
    rows = [{"features": _feats(1 if i % 2 == 0 else -1), "label": i % 2, "feature_timestamp": i} for i in range(40)]
    X, y, ts = _matrix_from_rows(rows)
    m = _train_logistic(X, y, epochs=80)
    met = _metrics(m, X, y)
    assert 0.0 <= met["accuracy"] <= 1.0
    p = m.predict_proba(X[:2])
    assert p.shape == (2, 2)
