"""MODULE: tokocrypto_bot.strategy.regime"""
import logging
from enum import Enum
from dataclasses import dataclass
from tokocrypto_bot.strategy.features import FeatureFrame
logger = logging.getLogger("NVRA.RegimeClassifier")
class MarketRegime(str, Enum):
    TRENDING_HIGH_VOL="TRENDING_HIGH_VOL"; TRENDING_LOW_VOL="TRENDING_LOW_VOL"
    RANGE_HIGH_VOL="RANGE_HIGH_VOL"; RANGE_LOW_VOL="RANGE_LOW_VOL"; NEUTRAL="NEUTRAL"
@dataclass(frozen=True)
class RegimeContext:
    symbol: str; timestamp: int; regime: MarketRegime; adx_value: float; volatility_score: float; is_liquid: bool; summary: str
class RegimeClassifier:
    def __init__(self, adx_trending_threshold=25.0, high_vol_threshold=0.02):
        self.adx_threshold=adx_trending_threshold; self.high_vol_threshold=high_vol_threshold
    def classify(self, feature_frame, is_liquid=True):
        if not feature_frame.is_valid:
            return RegimeContext(feature_frame.symbol, feature_frame.timestamp, MarketRegime.NEUTRAL, 0.0, 0.0, is_liquid, "Invalid")
        feats=feature_frame.features
        di_plus, di_minus = feats.get("DI_plus",0.0), feats.get("DI_minus",0.0)
        volatility = feats.get("volatility_regime",0.0)
        adx_approx = (abs(di_plus-di_minus)/max(1e-8, di_plus+di_minus))*100.0
        is_trending = adx_approx >= self.adx_threshold; is_high_vol = volatility >= self.high_vol_threshold
        if is_trending and is_high_vol: regime=MarketRegime.TRENDING_HIGH_VOL
        elif is_trending: regime=MarketRegime.TRENDING_LOW_VOL
        elif is_high_vol: regime=MarketRegime.RANGE_HIGH_VOL
        else: regime=MarketRegime.RANGE_LOW_VOL
        return RegimeContext(feature_frame.symbol, feature_frame.timestamp, regime, adx_approx, volatility, is_liquid, f"ADX={adx_approx:.1f}")
