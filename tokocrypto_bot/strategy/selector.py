"""MODULE: tokocrypto_bot.strategy.selector"""
import logging
from typing import List, Dict, Optional, Tuple
from tokocrypto_bot.strategy.features import FeatureFrame
from tokocrypto_bot.strategy.regime import RegimeClassifier, RegimeContext, MarketRegime
from tokocrypto_bot.strategy.strategies.base import BaseStrategy, CandidateSignal
from tokocrypto_bot.strategy.strategies.scalping import ScalpingStrategy
from tokocrypto_bot.ml.inference import PredictionResult
logger = logging.getLogger("NVRA.StrategySelector")
class AdaptiveStrategySelector:
    def __init__(self, strategies: Optional[List[BaseStrategy]] = None):
        self.strategies: List[BaseStrategy] = strategies or [ScalpingStrategy()]
        self.regime_classifier = RegimeClassifier()
    def select_best_signal(self, symbol, feature_frame, prediction, is_liquid=True):
        regime_ctx = self.regime_classifier.classify(feature_frame, is_liquid)
        best_candidate, best_score = None, -1.0
        if not feature_frame.is_valid or not prediction.is_valid:
            return None, 0.0, regime_ctx
        for strategy in self.strategies:
            if not strategy.is_enabled: continue
            if regime_ctx.regime not in strategy.applicable_regimes: continue
            candidate = strategy.generate_candidate_signal(feature_frame)
            if not candidate: continue
            score = 1.0 * (0.4 * prediction.probability_up + 0.3 * prediction.confidence + 0.3 * (max(0.0, candidate.expected_value) * 10)) * (1.0 if is_liquid else 0.5)
            if score > best_score:
                best_score = score; best_candidate = candidate
        return best_candidate, best_score, regime_ctx
