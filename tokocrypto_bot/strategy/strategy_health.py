"""MODULE: tokocrypto_bot.strategy.strategy_health"""
import logging
from enum import Enum
from dataclasses import dataclass
from tokocrypto_bot.quant.performance_evaluator import PerformanceReport
logger = logging.getLogger("NVRA.StrategyHealth")
class StrategyHealthState(str, Enum):
    HEALTHY="HEALTHY"; CAUTION="CAUTION"; DEGRADED="DEGRADED"; DISABLED="DISABLED"
@dataclass(frozen=True)
class StrategyHealthStatus:
    strategy_name: str; state: StrategyHealthState; net_expectancy: float; profit_factor: float
    drawdown_pct: float; consecutive_degradations: int; recommendation: str
class StrategyHealthMonitor:
    def __init__(self, min_expectancy_usdt=0.5, max_allowed_drawdown=0.08):
        self.min_expectancy=min_expectancy_usdt; self.max_dd=max_allowed_drawdown
    def evaluate_strategy_health(self, strategy_name, report):
        if report.total_trades < 10:
            return StrategyHealthStatus(strategy_name, StrategyHealthState.HEALTHY, report.net_expectancy, report.profit_factor, report.max_drawdown_pct, 0, "Insufficient sample")
        if report.net_expectancy < 0 or report.max_drawdown_pct >= self.max_dd:
            state, rec = StrategyHealthState.DEGRADED, "DEGRADED: negative expectancy or high DD"
        elif report.profit_factor < 1.1 or report.net_expectancy < self.min_expectancy:
            state, rec = StrategyHealthState.CAUTION, "CAUTION: low PF"
        else:
            state, rec = StrategyHealthState.HEALTHY, "HEALTHY"
        return StrategyHealthStatus(strategy_name, state, report.net_expectancy, report.profit_factor, report.max_drawdown_pct, 1 if state!=StrategyHealthState.HEALTHY else 0, rec)
