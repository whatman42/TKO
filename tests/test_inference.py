"""TEST SUITE: tokocrypto_bot.ml.inference — Phase 1.2 fail-closed"""
import os, pytest, tempfile, hashlib, pickle
from pathlib import Path
from unittest.mock import patch
import numpy as np
from tokocrypto_bot.ml.inference import MLInferenceEngine, PredictionResult, _extract_probabilities
from tokocrypto_bot.strategy.features import FeatureFrame, FEATURE_VERSION
from tests.conftest import BinaryProbaModel, PredictOnlyModel, BadProbaModel, RaisingModel, _write_artifact

def _valid_features_dict():
    return {"EMA50":100.5,"EMA200":99.8,"RSI14":55.0,"ROC":0.05,"ATR":2.0,"volatility_regime":0.02,"MACD_HIST":0.1,"DI_plus":25.0,"DI_minus":20.0,"ema_ratio":1.007,"bb_pband":0.5,"obv_vs_ma":1000.0,"cmf":0.3,"vwma_dev":0.02,"drawdown_20":-0.05}

@pytest.fixture
def valid_feature_frame():
    return FeatureFrame(1693497600000,"BTCUSDT",FEATURE_VERSION,_valid_features_dict(),True)

@pytest.fixture
def invalid_feature_frame():
    return FeatureFrame(0,"BTCUSDT",FEATURE_VERSION,{},False,"Insufficient history")

def _make_temp_model(model, version="1.0.0", feature_version=None):
    tmpdir = tempfile.mkdtemp(); path = Path(tmpdir)/"champion_model.pkl"
    _write_artifact(path, model, version=version, feature_version=feature_version)
    return path, tmpdir

class TestMLInferenceEngineModelLoading:
    def test_model_loads_successfully_with_explicit_path_and_override(self, temp_model_file):
        engine = MLInferenceEngine(model_path=str(temp_model_file), allow_test_override=True)
        assert engine.model is not None and engine.model_version == "1.0.0"
    def test_explicit_model_path_rejected_without_override(self, temp_model_file):
        engine = MLInferenceEngine(model_path=str(temp_model_file), allow_test_override=False)
        assert engine.model is None and engine.model_version == "UNAVAILABLE"
    def test_model_unavailable_fails_closed(self):
        with patch("tokocrypto_bot.ml.inference.resolve_and_validate_model_path", return_value=(None, False)):
            engine = MLInferenceEngine()
            assert engine.model is None and engine.model_version == "UNAVAILABLE"
    def test_model_path_validation_fails(self, temp_model_file):
        with patch("tokocrypto_bot.ml.inference.resolve_and_validate_model_path", return_value=(temp_model_file, False)):
            engine = MLInferenceEngine()
            assert engine.model is None
    def test_corrupt_model_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            corrupt = Path(tmpdir)/"bad_model.pkl"; corrupt.write_bytes(b"not valid pickle")
            engine = MLInferenceEngine(model_path=str(corrupt), allow_test_override=True)
            assert engine.model is None and engine.model_version == "CORRUPT"
    def test_legacy_model_without_version_metadata(self):
        model = BinaryProbaModel(classes=[0,1], proba_row=[0.4,0.6])
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir)/"legacy.pkl"
            with open(path,"wb") as f: pickle.dump(model, f)
            engine = MLInferenceEngine(model_path=str(path), allow_test_override=True)
            assert engine.model is not None and engine.model_version == "LEGACY_1.0"
    def test_model_feature_version_mismatch_rejected(self):
        path,_ = _make_temp_model(BinaryProbaModel([0,1],[0.3,0.7]), feature_version="1999.0.0")
        engine = MLInferenceEngine(model_path=str(path), allow_test_override=True)
        assert engine.model is None and engine.model_version == "FEATURE_MISMATCH"

class TestMLInferenceEngineEnvironmentVariables:
    def test_nvra_model_path_override(self, temp_model_file):
        with patch.dict(os.environ, {"NVRA_MODEL_PATH": str(temp_model_file)}):
            engine = MLInferenceEngine(); assert engine.model is not None
    def test_sha256_validation_passed(self, temp_model_file):
        good = hashlib.sha256(temp_model_file.read_bytes()).hexdigest()
        with patch.dict(os.environ, {"NVRA_MODEL_PATH": str(temp_model_file), "NVRA_MODEL_SHA256": good}):
            engine = MLInferenceEngine(); assert engine.model is not None
    def test_sha256_validation_failed(self, temp_model_file):
        with patch.dict(os.environ, {"NVRA_MODEL_PATH": str(temp_model_file), "NVRA_MODEL_SHA256": "0"*64}):
            engine = MLInferenceEngine(); assert engine.model is None

class TestMLInferencePredictionFailClosed:
    def test_predict_model_unavailable_returns_invalid(self, valid_feature_frame):
        r = MLInferenceEngine().predict(valid_feature_frame)
        assert r.is_valid is False and r.status_code == "MODEL_UNAVAILABLE" and r.confidence == 0.0
    def test_predict_feature_frame_invalid_returns_invalid(self, temp_model_file, invalid_feature_frame):
        r = MLInferenceEngine(model_path=str(temp_model_file), allow_test_override=True).predict(invalid_feature_frame)
        assert r.is_valid is False and r.status_code == "INVALID_INPUT"
    def test_predict_feature_version_mismatch_returns_invalid(self, temp_model_file, valid_feature_frame):
        bad = FeatureFrame(valid_feature_frame.timestamp, valid_feature_frame.symbol, "0.0.0", valid_feature_frame.features, True)
        r = MLInferenceEngine(model_path=str(temp_model_file), allow_test_override=True).predict(bad)
        assert r.is_valid is False and r.status_code == "FEATURE_MISMATCH"
    def test_predict_missing_feature_column_returns_invalid(self, temp_model_file):
        incomplete = dict(_valid_features_dict()); del incomplete["RSI14"]
        ff = FeatureFrame(1,"BTCUSDT",FEATURE_VERSION,incomplete,True)
        r = MLInferenceEngine(model_path=str(temp_model_file), allow_test_override=True).predict(ff)
        assert r.is_valid is False and r.status_code == "FEATURE_MISMATCH"
    def test_predict_nan_in_features_returns_invalid(self, temp_model_file):
        feats = dict(_valid_features_dict()); feats["ATR"] = float("nan")
        r = MLInferenceEngine(model_path=str(temp_model_file), allow_test_override=True).predict(FeatureFrame(1,"BTCUSDT",FEATURE_VERSION,feats,True))
        assert r.is_valid is False and r.status_code == "INVALID_INPUT"
    def test_predict_inf_in_features_returns_invalid(self, temp_model_file):
        feats = dict(_valid_features_dict()); feats["ATR"] = float("inf")
        r = MLInferenceEngine(model_path=str(temp_model_file), allow_test_override=True).predict(FeatureFrame(1,"BTCUSDT",FEATURE_VERSION,feats,True))
        assert r.is_valid is False and r.status_code == "INVALID_INPUT"

class TestMLInferenceValidPredictions:
    def test_sklearn_model_with_predict_proba(self, temp_model_file, valid_feature_frame):
        r = MLInferenceEngine(model_path=str(temp_model_file), allow_test_override=True).predict(valid_feature_frame)
        assert r.is_valid and abs(r.probability_up-0.7)<1e-9 and abs(r.probability_down-0.3)<1e-9
    def test_predict_only_model_inside_range(self, valid_feature_frame):
        path,_ = _make_temp_model(PredictOnlyModel(0.75))
        r = MLInferenceEngine(model_path=str(path), allow_test_override=True).predict(valid_feature_frame)
        assert r.is_valid and abs(r.probability_up-0.75)<1e-9
    def test_predict_only_model_outside_range_rejected(self, valid_feature_frame):
        path,_ = _make_temp_model(PredictOnlyModel(1.5))
        r = MLInferenceEngine(model_path=str(path), allow_test_override=True).predict(valid_feature_frame)
        assert r.is_valid is False and r.status_code == "INFERENCE_ERROR"
    def test_inference_exception_caught_returns_invalid(self, valid_feature_frame):
        path,_ = _make_temp_model(RaisingModel())
        r = MLInferenceEngine(model_path=str(path), allow_test_override=True).predict(valid_feature_frame)
        assert r.is_valid is False and r.status_code == "INFERENCE_ERROR"

class TestProbabilityValidation:
    def test_probability_outside_range_rejected(self):
        with pytest.raises(ValueError, match="outside"): _extract_probabilities(BadProbaModel([1.2,-0.2],[0,1]), np.zeros((1,15)))
    def test_nan_probability_rejected(self):
        with pytest.raises(ValueError, match="Non-finite"): _extract_probabilities(BadProbaModel([np.nan,0.5],[0,1]), np.zeros((1,15)))
    def test_inf_probability_rejected(self):
        with pytest.raises(ValueError, match="Non-finite"): _extract_probabilities(BadProbaModel([np.inf,0.0],[0,1]), np.zeros((1,15)))
    def test_probability_sum_not_one_rejected(self):
        with pytest.raises(ValueError, match="sum"): _extract_probabilities(BadProbaModel([0.4,0.4],[0,1]), np.zeros((1,15)))
    def test_three_class_output_rejected(self):
        with pytest.raises(ValueError, match="dimensions"): _extract_probabilities(BadProbaModel([0.2,0.3,0.5],[0,1,2]), np.zeros((1,15)))
    def test_classes_0_1(self):
        up,down = _extract_probabilities(BinaryProbaModel([0,1],[0.2,0.8]), np.zeros((1,15))); assert abs(up-0.8)<1e-9
    def test_classes_1_0_reversed(self):
        up,down = _extract_probabilities(BinaryProbaModel([1,0],[0.8,0.2]), np.zeros((1,15))); assert abs(up-0.8)<1e-9
    def test_classes_down_up_string(self):
        up,down = _extract_probabilities(BinaryProbaModel(["DOWN","UP"],[0.35,0.65]), np.zeros((1,15))); assert abs(up-0.65)<1e-9
    def test_classes_up_down_string_reversed(self):
        up,down = _extract_probabilities(BinaryProbaModel(["UP","DOWN"],[0.9,0.1]), np.zeros((1,15))); assert abs(up-0.9)<1e-9
    def test_unknown_classes_rejected(self):
        with pytest.raises(ValueError, match="Unknown"): _extract_probabilities(BinaryProbaModel(["FOO","BAR"],[0.5,0.5]), np.zeros((1,15)))
    def test_malformed_proba_cannot_produce_valid_prediction(self, valid_feature_frame):
        path,_ = _make_temp_model(BadProbaModel([0.9,0.2],[0,1]))
        r = MLInferenceEngine(model_path=str(path), allow_test_override=True).predict(valid_feature_frame)
        assert r.is_valid is False and r.confidence == 0.0

class TestMLInferenceMultiPair:
    def test_batch_predict_all_invalid_when_model_unavailable(self, valid_feature_frame):
        frames = {"ETHUSDT": FeatureFrame(1,"ETHUSDT",FEATURE_VERSION,_valid_features_dict(),True), "BTCUSDT": FeatureFrame(2,"BTCUSDT",FEATURE_VERSION,_valid_features_dict(),True)}
        results = MLInferenceEngine().predict_multi_pair(frames)
        assert set(results.keys())=={"ETHUSDT","BTCUSDT"} and all(not r.is_valid for r in results.values())
    def test_batch_predict_valid_model_processes_all_pairs(self, temp_model_file, valid_feature_frame):
        frames = {"ETHUSDT": FeatureFrame(1,"ETHUSDT",FEATURE_VERSION,_valid_features_dict(),True), "BTCUSDT": valid_feature_frame}
        results = MLInferenceEngine(model_path=str(temp_model_file), allow_test_override=True).predict_multi_pair(frames)
        assert all(r.is_valid for r in results.values())
    def test_mixed_valid_invalid_pairs(self, temp_model_file, valid_feature_frame, invalid_feature_frame):
        results = MLInferenceEngine(model_path=str(temp_model_file), allow_test_override=True).predict_multi_pair({"GOOD":valid_feature_frame,"BAD":invalid_feature_frame})
        assert results["GOOD"].is_valid and not results["BAD"].is_valid

class TestFailClosedContract:
    def test_no_exception_can_make_invalid_model_produce_valid_prediction(self, valid_feature_frame):
        r = MLInferenceEngine().predict(valid_feature_frame)
        assert r.is_valid is False and r.confidence==0.0 and r.probability_up==0.0
    def test_explicit_path_without_override_cannot_bypass(self, temp_model_file, valid_feature_frame):
        r = MLInferenceEngine(model_path=str(temp_model_file), allow_test_override=False).predict(valid_feature_frame)
        assert r.is_valid is False
