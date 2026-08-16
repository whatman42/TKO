"""MODULE: tests.test_p1_pipeline"""
import pytest, numpy as np, pandas as pd
from tokocrypto_bot.strategy.features import FeatureEngine
from tokocrypto_bot.ml.inference import MLInferenceEngine
from tokocrypto_bot.strategy.decision import DecisionEngine, DecisionAction
def create_mock_klines(symbol, count=210):
    now_ms=1700000000000; data=[]
    for i in range(count):
        direction=1.0 if i%2==0 else -1.0; price=1000.0+direction*(1.0+(i%5)*0.1)
        data.append({"timestamp":now_ms+i*60000,"open":price,"high":price+25,"low":price-25,"close":price+direction*5,"volume":500.0+(i%10),"is_complete":i<(count-1)})
    return pd.DataFrame(data)
def test_full_p1_pipeline_multi_pair_integration(tmp_path):
    from tests.conftest import BinaryProbaModel, _write_artifact
    model_path=tmp_path/"champion_model.pkl"; _write_artifact(model_path, BinaryProbaModel([0,1],[0.2,0.8]), version="2026.1_TEST")
    fe, ie, de = FeatureEngine(), MLInferenceEngine(model_path=str(model_path), allow_test_override=True), DecisionEngine()
    klines={"BTCUSDT":create_mock_klines("BTCUSDT"),"ETHUSDT":create_mock_klines("ETHUSDT")}
    frames=fe.compute_multi_pair_features(klines); preds=ie.predict_multi_pair(frames)
    decisions=de.evaluate_multi_pair(frames, preds, current_prices={"BTCUSDT":1000.0,"ETHUSDT":1000.0})
    assert decisions["BTCUSDT"].action==DecisionAction.BUY and "EV_PASS" in decisions["BTCUSDT"].reason_codes
def test_pipeline_fallback_on_missing_model(tmp_path):
    fe, ie, de = FeatureEngine(), MLInferenceEngine(model_path=str(tmp_path/"missing.pkl")), DecisionEngine()
    frames=fe.compute_multi_pair_features({"SOLUSDT":create_mock_klines("SOLUSDT")})
    decisions=de.evaluate_multi_pair(frames, ie.predict_multi_pair(frames))
    assert decisions["SOLUSDT"].action==DecisionAction.NO_TRADE
