"""MODULE: tokocrypto_bot.strategy.decision"""
import logging
from enum import Enum
from dataclasses import dataclass
from typing import List, Dict, Optional
from tokocrypto_bot.strategy.features import FeatureFrame
from tokocrypto_bot.ml.inference import PredictionResult
logger = logging.getLogger("NVRA.DecisionEngine")
STRATEGY_VERSION = "2026.1.0"
class DecisionAction(str, Enum):
    BUY="BUY"; SELL="SELL"; HOLD="HOLD"; NO_TRADE="NO_TRADE"
@dataclass(frozen=True)
class Decision:
    symbol: str; timestamp: int; action: DecisionAction; probability: float; confidence: float
    expected_value: float; stop_loss: Optional[float]; take_profit: Optional[float]
    reason_codes: List[str]; strategy_version: str; model_version: str
@dataclass(frozen=True)
class DecisionThresholds:
    min_buy_probability: float=0.65; min_sell_probability: float=0.65; min_confidence: float=0.30
    min_expected_value: float=0.015; rsi_overbought: float=70.0; rsi_oversold: float=30.0
class DecisionEngine:
    def __init__(self, thresholds=None):
        self.thresholds = thresholds or DecisionThresholds()
    def evaluate(self, symbol, feature_frame, prediction, current_position_qty=0.0, current_price=0.0):
        reasons=[]
        if not prediction.is_valid:
            return self._build(symbol, feature_frame.timestamp, DecisionAction.NO_TRADE, 0,0,0,None,None,[f"ML_INVALID_{prediction.status_code}"], prediction.model_version)
        if not feature_frame.is_valid:
            return self._build(symbol, feature_frame.timestamp, DecisionAction.NO_TRADE, 0,0,0,None,None,["FEATURES_INVALID"], prediction.model_version)
        prob_up, prob_down, confidence = prediction.probability_up, prediction.probability_down, prediction.confidence
        feats = feature_frame.features
        rsi, ema_ratio, atr = feats.get("RSI14",50.0), feats.get("ema_ratio",1.0), feats.get("ATR",0.0)
        if prob_up >= self.thresholds.min_buy_probability:
            reasons.append("ML_BUY_PROBABILITY_PASS")
            tech_pass = True
            if rsi > self.thresholds.rsi_overbought: reasons.append("RSI_OVERBOUGHT_FAIL"); tech_pass=False
            if ema_ratio < 0.98: reasons.append("BEARISH_EMA_TREND_FAIL"); tech_pass=False
            if tech_pass:
                reasons.append("TECHNICAL_CONFIRMATION_PASS")
                sl = current_price - 2*atr if current_price>0 and atr>0 else None
                tp = current_price + 3*atr if current_price>0 and atr>0 else None
                reward = (3*atr)/current_price if current_price>0 else 0.03
                risk = (2*atr)/current_price if current_price>0 else 0.02
                ev = (prob_up*reward) - (prob_down*risk)
                if ev >= self.thresholds.min_expected_value:
                    reasons.append("EV_PASS")
                    return self._build(symbol, feature_frame.timestamp, DecisionAction.BUY, prob_up, confidence, ev, sl, tp, reasons, prediction.model_version)
                reasons.append("EV_THRESHOLD_FAIL")
        elif prob_down >= self.thresholds.min_sell_probability or (current_position_qty>0 and rsi>self.thresholds.rsi_overbought):
            reasons.append("ML_OR_TECH_SELL_SIGNAL")
            sl = current_price + 2*atr if current_price>0 and atr>0 else None
            tp = current_price - 3*atr if current_price>0 and atr>0 else None
            ev = (prob_down*0.03)-(prob_up*0.02)
            return self._build(symbol, feature_frame.timestamp, DecisionAction.SELL, prob_down, confidence, ev, sl, tp, reasons, prediction.model_version)
        reasons.append("SIGNAL_NEUTRAL")
        action = DecisionAction.HOLD if current_position_qty>0 else DecisionAction.NO_TRADE
        return self._build(symbol, feature_frame.timestamp, action, max(prob_up,prob_down), confidence, 0.0, None, None, reasons, prediction.model_version)
    def evaluate_multi_pair(self, feature_frames, predictions, current_positions=None, current_prices=None):
        positions, prices = current_positions or {}, current_prices or {}
        return {sym: self.evaluate(sym, ff, predictions.get(sym, PredictionResult(sym,0,"NONE","NONE",0,0,0,False,"MISSING")), positions.get(sym,0), prices.get(sym,0)) for sym, ff in feature_frames.items()}
    def _build(self, symbol, timestamp, action, prob, conf, ev, sl, tp, reasons, model_ver):
        return Decision(symbol, timestamp, action, prob, conf, ev, sl, tp, reasons, STRATEGY_VERSION, model_ver)
