#!/usr/bin/env python3
"""Rebuild models/champion_model.pkl (neutral structural model)."""
import pickle
from pathlib import Path
from tokocrypto_bot.ml.neutral_model import NeutralBinaryModel
from tokocrypto_bot.strategy.features import EXPECTED_FEATURE_COLUMNS, FEATURE_VERSION

def main():
    root = Path(__file__).resolve().parent.parent
    out = root / "models" / "champion_model.pkl"
    out.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "model": NeutralBinaryModel(),
        "version": "DEV_NEUTRAL_0.5_2026.1.0",
        "feature_version": FEATURE_VERSION,
        "feature_columns": list(EXPECTED_FEATURE_COLUMNS),
        "notes": "Neutral structural model. Replace for production alpha.",
    }
    with open(out, "wb") as f:
        pickle.dump(artifact, f, protocol=4)
    print("Wrote", out, out.stat().st_size, "bytes")

if __name__ == "__main__":
    main()
