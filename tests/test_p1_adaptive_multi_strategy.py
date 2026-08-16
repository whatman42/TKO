"""MODULE: tests.test_p1_adaptive_multi_strategy"""
import pytest, pandas as pd
from tokocrypto_bot.strategy.features import FeatureEngine
from tokocrypto_bot.strategy.regime import MarketRegime
from tokocrypto_bot.strategy.selector import AdaptiveStrategySelector
from tokocrypto_bot.ml.inference import PredictionResult
def create_high_volatility_df(count=210):
    data=[]
    for i in range(count):
        price=1000.0+(10 if i%2==0 else -10); price=950.0 if i==count-2 else price
        data.append({"timestamp":1700000000000+i*60000,"open":price,"high":price+5,"low":price-5,"close":price,"volume":2000.0,"is_complete":i<(count-1)})
    return pd.DataFrame(data)
def test_regime_classification_and_strategy_selection():
    ff=FeatureEngine().compute_features(create_high_volatility_df(),"SOLUSDT"); assert ff.is_valid
    pred=PredictionResult("SOLUSDT",ff.timestamp,"2026.1",ff.feature_version,0.78,0.22,0.56,True,"OK")
    cand,score,regime=AdaptiveStrategySelector().select_best_signal("SOLUSDT",ff,pred,True)
    assert regime.regime in (MarketRegime.TRENDING_HIGH_VOL, MarketRegime.RANGE_HIGH_VOL)
