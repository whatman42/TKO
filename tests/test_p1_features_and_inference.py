"""MODULE: tests.test_p1_features_and_inference"""
import pytest, pandas as pd
from tokocrypto_bot.strategy.market_data import DataSource
from tokocrypto_bot.strategy.features import FeatureEngine
from tokocrypto_bot.ml.inference import MLInferenceEngine
def generate_dummy_klines(count=210):
    data=[]
    for i in range(count):
        price=50000.0+i*1.5
        data.append({"timestamp":1700000000000+i*60000,"open":price,"high":price+5,"low":price-5,"close":price+2,"volume":100.0+i,"source":DataSource.TOKOCRYPTO.value,"is_complete":i<(count-1)})
    return pd.DataFrame(data)
def test_feature_engine_prevents_unclosed_candles_and_validates_minimum_history():
    fe=FeatureEngine(); ff=fe.compute_features(generate_dummy_klines(100),"BTCUSDT")
    assert ff.is_valid is False and "Insufficient history" in ff.error_reason
    df=generate_dummy_klines(210); ff=fe.compute_features(df,"BTCUSDT")
    assert ff.is_valid and ff.timestamp==int(df.iloc[-2]["timestamp"])
def test_feature_engine_handles_nan_inf():
    df=generate_dummy_klines(210); df["close"]=0.0
    ff=FeatureEngine().compute_features(df,"BTCUSDT"); assert ff.is_valid is False
def test_ml_inference_model_unavailable_policy(tmp_path):
    engine=MLInferenceEngine(model_path=str(tmp_path/"missing.pkl"))
    ff=FeatureEngine().compute_features(generate_dummy_klines(210),"BTCUSDT")
    r=engine.predict(ff); assert r.is_valid is False and r.status_code=="MODEL_UNAVAILABLE"
def test_ml_inference_success_with_mock_model(tmp_path):
    from tests.conftest import BinaryProbaModel, _write_artifact
    path=tmp_path/"mock.pkl"; _write_artifact(path, BinaryProbaModel([0,1],[0.3,0.7]), version="2026.1.1")
    r=MLInferenceEngine(model_path=str(path), allow_test_override=True).predict(FeatureEngine().compute_features(generate_dummy_klines(210),"BTCUSDT"))
    assert r.is_valid and r.probability_up==0.7 and round(r.confidence,2)==0.40
