import numpy as np


class NeutralBinaryModel:
    """Structural placeholder: always P(UP)=P(DOWN)=0.5."""
    classes_ = np.array([0, 1])

    def predict_proba(self, X):
        X = np.asarray(X, dtype=np.float64)
        n = X.shape[0] if X.ndim == 2 else 1
        return np.tile(np.array([[0.5, 0.5]], dtype=np.float64), (n, 1))

    def predict(self, X):
        n = np.asarray(X).shape[0] if np.asarray(X).ndim == 2 else 1
        return np.full((n,), 0.5, dtype=np.float64)
