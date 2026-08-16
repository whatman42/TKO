"""
PYTEST CONFIGURATION
Description: Pytest configuration and shared fixtures for NVRA trading bot tests.
"""

import os
import sys
import pytest
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def pytest_configure(config):
    """Configure pytest environment and register custom marks."""
    os.environ.setdefault("NVRA_ENV", "test")
    # Ensure no model is accidentally loaded from user's system
    os.environ.pop("NVRA_MODEL_PATH", None)
    os.environ.pop("NVRA_MODEL_SHA256", None)

    config.addinivalue_line("markers", "model_loader: model_loader unit tests")
    config.addinivalue_line("markers", "inference: ML inference unit tests")


def pytest_collection_modifyitems(config, items):
    """Modify test collection — attach markers by path."""
    for item in items:
        if "test_model_loader" in str(item.fspath):
            item.add_marker(pytest.mark.model_loader)
        elif "test_inference" in str(item.fspath):
            item.add_marker(pytest.mark.inference)


# ---------------------------------------------------------------------------
# Shared pickle-safe test model helpers (used by inference tests)
# ---------------------------------------------------------------------------
import tempfile
import pickle
import numpy as np


class BinaryProbaModel:
    """
    Minimal pickle-safe binary classifier with predict_proba + classes_.
    Top-level class so pickle works across modules.
    """

    def __init__(self, classes, proba_row):
        """
        Args:
            classes: sequence of length 2 (e.g. [0,1], [1,0], ["DOWN","UP"])
            proba_row: sequence of length 2, aligned with classes order
        """
        self.classes_ = np.array(classes)
        self._proba = np.asarray(proba_row, dtype=np.float64)

    def predict_proba(self, X):
        n = len(X) if hasattr(X, "__len__") else 1
        return np.tile(self._proba, (n, 1))


class PredictOnlyModel:
    """Pickle-safe model that only implements predict() returning a scalar in [0,1]."""

    def __init__(self, value: float):
        self._value = float(value)

    def predict(self, X):
        n = len(X) if hasattr(X, "__len__") else 1
        return np.full(n, self._value, dtype=np.float64)


class BadProbaModel:
    """Configurable broken predict_proba for negative tests."""

    def __init__(self, proba_row, classes=None):
        self._proba = np.asarray(proba_row, dtype=np.float64)
        if classes is not None:
            self.classes_ = np.array(classes)

    def predict_proba(self, X):
        n = len(X) if hasattr(X, "__len__") else 1
        return np.tile(self._proba, (n, 1))


class RaisingModel:
    """Model that always raises on predict/predict_proba."""

    def predict_proba(self, X):
        raise RuntimeError("simulated inference failure")

    def predict(self, X):
        raise RuntimeError("simulated inference failure")


@pytest.fixture(scope="session")
def temp_session_dir():
    """Session-scoped temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def _write_artifact(path: Path, model, version="1.0.0", feature_version=None):
    artifact = {"model": model, "version": version}
    if feature_version is not None:
        artifact["feature_version"] = feature_version
    with open(path, "wb") as f:
        pickle.dump(artifact, f)
    return path


@pytest.fixture
def pickle_safe_sklearn_model():
    """Binary model: classes [0,1], proba [0.3, 0.7] → DOWN=0.3, UP=0.7."""
    return BinaryProbaModel(classes=[0, 1], proba_row=[0.3, 0.7])


@pytest.fixture
def temp_model_file(pickle_safe_sklearn_model):
    """Temporary champion_model.pkl with a real pickle-safe binary model."""
    with tempfile.TemporaryDirectory() as tmpdir:
        model_path = Path(tmpdir) / "champion_model.pkl"
        _write_artifact(model_path, pickle_safe_sklearn_model, version="1.0.0")
        yield model_path


@pytest.fixture
def temp_model_pickle(pickle_safe_sklearn_model):
    """Alias used by older tests — same pattern as temp_model_file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        model_path = Path(tmpdir) / "test_model.pkl"
        _write_artifact(model_path, pickle_safe_sklearn_model, version="test_1.0")
        yield model_path
