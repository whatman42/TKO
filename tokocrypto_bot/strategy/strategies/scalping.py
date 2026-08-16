"""MODULE: tokocrypto_bot.strategy.strategies.scalping"""
from typing import Set, Optional
from tokocrypto_bot.strategy.strategies.base import BaseStrategy, CandidateSignal, StrategySignalSide
from tokocrypto_bot.strategy.features import FeatureFrame
from tokocrypto_bot.strategy.regime import MarketRegime
class ScalpingStrategy(BaseStrategy):
    def __init__(self):
        super().__init__(name="ScalpingStrategy", version="2026.1.0", timeframe="1m")
    @property
    def applicable_regimes(self) -> Set[MarketRegime]:
        return {MarketRegime.TRENDING_HIGH_VOL, MarketRegime.RANGE_HIGH_VOL}
    def generate_candidate_signal(self, feature_frame: FeatureFrame) -> Optional[CandidateSignal]:
        if not feature_frame.is_valid: return None
        feats = feature_frame.features
        rsi, pband, vwma_dev = feats.get("RSI14",50.0), feats.get("bb_pband",0.5), feats.get("vwma_dev",0.0)
        if pband < 0.15 and rsi < 35.0 and vwma_dev < -0.01:
            return CandidateSignal(self.name, feature_frame.symbol, feature_frame.timestamp, StrategySignalSide.BUY, 0.80, 0.018, 0.008, 0.016, "Scalp Buy")
        elif pband > 0.85 and rsi > 65.0:
            return CandidateSignal(self.name, feature_frame.symbol, feature_frame.timestamp, StrategySignalSide.SELL, 0.75, 0.015, 0.008, 0.015, "Scalp Sell")
        return None
