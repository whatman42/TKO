"""MODULE: tokocrypto_bot.strategy.portfolio - Risk Authority & Adaptive Position Sizing (Phase 1.5)"""
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from tokocrypto_bot.strategy.features import FeatureFrame
from tokocrypto_bot.strategy.decision import Decision, DecisionAction
from tokocrypto_bot.persistence.lifecycle_state import ApplicationState
logger = logging.getLogger("NVRA.PortfolioRisk")
class RiskState(str, Enum):
    NORMAL="NORMAL"; CAUTION="CAUTION"; RESTRICTED="RESTRICTED"; HALTED="HALTED"
class RiskAction(str, Enum):
    ALLOW="ALLOW"; REDUCE="REDUCE"; REJECT="REJECT"
@dataclass(frozen=True)
class PortfolioState:
    total_equity_usdt: float; available_balance_usdt: float; current_portfolio_exposure_usdt: float
    daily_realized_pnl_usdt: float; peak_equity_usdt: float; current_drawdown_pct: float
    cusum_statistic: float; consecutive_losses: int
    active_positions: Dict[str, float] = field(default_factory=dict)
    app_lifecycle_state: ApplicationState = ApplicationState.READY
    is_reconciliation_clean: bool = True; is_kill_switch_active: bool = False
@dataclass(frozen=True)
class RiskDecision:
    symbol: str; action: RiskAction; requested_notional: float; approved_notional: float
    risk_per_trade_pct: float; portfolio_exposure_pct: float; stop_distance_pct: float
    max_loss_usdt: float; risk_score: float; risk_flags: List[str]; reason_codes: List[str]
@dataclass(frozen=True)
class PositionPlan:
    symbol: str; timestamp: int; action: DecisionAction; approved_notional_usdt: float
    calculated_quantity: float; target_price: float; stop_loss_price: Optional[float]
    take_profit_price: Optional[float]; risk_decision: RiskDecision
@dataclass(frozen=True)
class PortfolioRiskConfig:
    max_portfolio_exposure_pct: float = 0.50; max_single_asset_exposure_pct: float = 0.15
    base_risk_per_trade_pct: float = 0.01; atr_multiplier_stop: float = 2.0
    max_daily_loss_pct: float = 0.04; max_drawdown_halt_pct: float = 0.10
    cusum_threshold: float = 5.0; max_consecutive_losses: int = 4; min_notional_usdt: float = 10.0
    taker_fee_pct: float = 0.001; slippage_pct: float = 0.0005
def compute_available_equity(p_state: PortfolioState) -> float:
    if not p_state.is_reconciliation_clean or p_state.is_kill_switch_active: return 0.0
    if p_state.app_lifecycle_state in (ApplicationState.SAFE_MODE, ApplicationState.PAUSED): return 0.0
    eq = float(p_state.available_balance_usdt or 0.0)
    return 0.0 if eq < 0 or eq != eq else eq
def compute_executable_notional(available_equity, fee_pct, slippage_pct):
    if available_equity <= 0: return 0.0
    denom = 1.0 + max(0.0, fee_pct) + max(0.0, slippage_pct)
    return available_equity / denom if denom > 0 else 0.0
def quantize_quantity(qty, step_size, min_qty=0.0):
    if qty <= 0: return 0.0
    if step_size and step_size > 0:
        import math; qty = math.floor(qty / step_size + 1e-12) * step_size
    if min_qty and qty + 1e-12 < min_qty: return 0.0
    return max(0.0, qty)
class RiskGate:
    def __init__(self, config=None): self.config = config or PortfolioRiskConfig()
    def evaluate_risk_state(self, p_state):
        if (p_state.app_lifecycle_state in (ApplicationState.SAFE_MODE, ApplicationState.PAUSED)
            or not p_state.is_reconciliation_clean or p_state.is_kill_switch_active
            or p_state.current_drawdown_pct >= self.config.max_drawdown_halt_pct
            or (p_state.total_equity_usdt > 0 and abs(min(0.0, p_state.daily_realized_pnl_usdt)) / p_state.total_equity_usdt >= self.config.max_daily_loss_pct)
            or p_state.cusum_statistic >= self.config.cusum_threshold):
            return RiskState.HALTED
        if p_state.consecutive_losses >= self.config.max_consecutive_losses or p_state.current_drawdown_pct >= self.config.max_drawdown_halt_pct * 0.6:
            return RiskState.RESTRICTED
        if p_state.current_drawdown_pct >= self.config.max_drawdown_halt_pct * 0.3:
            return RiskState.CAUTION
        return RiskState.NORMAL
    def evaluate_trade_risk(self, decision, feature_frame, p_state, current_price, min_notional=None, step_size=0.0, min_qty=0.0):
        reasons, flags = [], []; symbol = decision.symbol
        if p_state.app_lifecycle_state not in (ApplicationState.READY, ApplicationState.TRADING):
            return self._reject(symbol, 0, [f"CRITICAL_LIFECYCLE_{p_state.app_lifecycle_state.value}"], flags)
        if not p_state.is_reconciliation_clean: return self._reject(symbol, 0, ["CRITICAL_RECONCILIATION_UNCLEAN"], flags)
        if p_state.is_kill_switch_active: return self._reject(symbol, 0, ["CRITICAL_KILL_SWITCH_ACTIVE"], flags)
        rs = self.evaluate_risk_state(p_state); flags.append(f"RISK_STATE_{rs.value}")
        if rs == RiskState.HALTED: return self._reject(symbol, 0, ["RISK_STATE_HALTED"], flags)
        if decision.action not in (DecisionAction.BUY, DecisionAction.SELL): return self._reject(symbol, 0, ["NO_TRADE_ACTION"], flags)
        if not feature_frame.is_valid: return self._reject(symbol, 0, ["INVALID_FEATURES"], flags)
        atr = feature_frame.features.get("ATR", 0.0)
        if atr <= 0 or current_price <= 0: return self._reject(symbol, 0, ["INVALID_PRICE_OR_ATR"], flags)
        available_equity = compute_available_equity(p_state)
        if available_equity <= 0: return self._reject(symbol, 0, ["EQUITY_UNVERIFIED_OR_ZERO"], flags)
        exchange_min = float(min_notional) if min_notional is not None else float(self.config.min_notional_usdt)
        if exchange_min <= 0: exchange_min = float(self.config.min_notional_usdt)
        executable = compute_executable_notional(available_equity, self.config.taker_fee_pct, self.config.slippage_pct)
        if executable + 1e-12 < exchange_min: return self._reject(symbol, 0, ["EXECUTABLE_BELOW_EXCHANGE_MINIMUM"], flags)
        total_eq = max(p_state.total_equity_usdt, available_equity, 1e-9)
        current_asset = p_state.active_positions.get(symbol, 0.0)
        avail_asset = max(0.0, total_eq * self.config.max_single_asset_exposure_pct - current_asset)
        avail_port = max(0.0, total_eq * self.config.max_portfolio_exposure_pct - p_state.current_portfolio_exposure_usdt)
        stop_dist = (atr * self.config.atr_multiplier_stop) / current_price
        risk_cap = total_eq * self.config.base_risk_per_trade_pct
        if rs == RiskState.CAUTION: risk_cap *= 0.50
        elif rs == RiskState.RESTRICTED: risk_cap *= 0.25
        calc = risk_cap / max(1e-4, stop_dist)
        approved = min(calc, avail_asset if avail_asset > 0 else calc, avail_port if avail_port > 0 else calc, executable)
        if approved + 1e-12 < exchange_min:
            if executable + 1e-12 >= exchange_min:
                approved = min(exchange_min, executable); reasons.append("MIN_ORDER_ADAPTATION_APPLIED"); flags.append("SMALL_EQUITY_MIN_ORDER_ADAPTATION")
            else:
                return self._reject(symbol, calc, ["APPROVED_NOTIONAL_BELOW_EXCHANGE_MINIMUM"], flags)
        raw_qty = approved / current_price; q_qty = quantize_quantity(raw_qty, step_size, min_qty)
        if q_qty <= 0: return self._reject(symbol, calc, ["QUANTITY_PRECISION_INVALID"], flags)
        approved = min(q_qty * current_price, executable)
        if approved + 1e-12 < exchange_min: return self._reject(symbol, calc, ["POST_QUANTIZE_BELOW_MINIMUM"], flags)
        action = RiskAction.ALLOW if approved >= calc * 0.95 or "MIN_ORDER_ADAPTATION_APPLIED" in reasons else RiskAction.REDUCE
        reasons.append("RISK_GATE_PASS")
        return RiskDecision(symbol, action, calc, approved, self.config.base_risk_per_trade_pct, (p_state.current_portfolio_exposure_usdt+approved)/max(1.0,total_eq), stop_dist, approved*stop_dist, 1.0-approved/max(1.0,total_eq), flags, reasons)
    def _reject(self, symbol, requested, reasons, flags):
        return RiskDecision(symbol, RiskAction.REJECT, requested, 0.0, 0, 0, 0, 0, 1.0, flags, reasons)
class PositionSizer:
    @staticmethod
    def create_position_plan(decision, risk_decision, current_price):
        if risk_decision.action == RiskAction.REJECT or risk_decision.approved_notional <= 0 or current_price <= 0:
            return PositionPlan(decision.symbol, decision.timestamp, DecisionAction.NO_TRADE, 0, 0, current_price, None, None, risk_decision)
        qty = risk_decision.approved_notional / current_price
        if qty <= 0:
            return PositionPlan(decision.symbol, decision.timestamp, DecisionAction.NO_TRADE, 0, 0, current_price, None, None, risk_decision)
        return PositionPlan(decision.symbol, decision.timestamp, decision.action, risk_decision.approved_notional, qty, current_price, decision.stop_loss, decision.take_profit, risk_decision)
class MultiPairPortfolioRanker:
    @staticmethod
    def rank_and_filter_plans(plans, available_capital_usdt):
        valid = [p for p in plans.values() if p.action in (DecisionAction.BUY, DecisionAction.SELL) and p.approved_notional_usdt > 0]
        valid.sort(key=lambda p: (p.risk_decision.risk_score, p.approved_notional_usdt), reverse=True)
        approved, remaining = [], available_capital_usdt
        for plan in valid:
            if remaining >= plan.approved_notional_usdt:
                approved.append(plan); remaining -= plan.approved_notional_usdt
            else: break
        return approved
