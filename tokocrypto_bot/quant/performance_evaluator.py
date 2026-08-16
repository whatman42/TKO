"""MODULE: tokocrypto_bot.quant.performance_evaluator"""
import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass
logger = logging.getLogger("NVRA.PerformanceEvaluator")
@dataclass(frozen=True)
class PerformanceReport:
    total_trades: int; win_rate: float; gross_edge: float; net_expectancy: float; profit_factor: float
    average_r: float; max_drawdown_pct: float; sharpe_ratio: float; sortino_ratio: float
    total_fee_cost_usdt: float; total_slippage_usdt: float; signal_to_fill_ratio: float
class PerformanceEvaluator:
    def __init__(self, default_fee_pct=0.001, default_slippage_pct=0.0005):
        self.fee_pct=default_fee_pct; self.slippage_pct=default_slippage_pct
    def evaluate_trades(self, df_trades):
        if df_trades is None or df_trades.empty:
            return PerformanceReport(0,0,0,0,0,0,0,0,0,0,0,0)
        total=len(df_trades); pnl=df_trades["pnl_usdt"].values; returns=df_trades["return_pct"].values; notionals=df_trades["notional_usdt"].values
        wins=pnl[pnl>0]; losses=pnl[pnl<0]
        win_rate=len(wins)/total if total else 0.0
        gross_profit=np.sum(wins) if len(wins) else 0.0
        gross_loss=abs(np.sum(losses)) if len(losses) else 1e-8
        fee_costs=np.sum(notionals*(self.fee_pct*2.0)); slippage_costs=np.sum(notionals*self.slippage_pct)
        net_pnl=np.sum(pnl)-fee_costs-slippage_costs
        cum=np.cumsum(returns); peak=np.maximum.accumulate(cum); max_dd=float(np.max((peak-cum)/np.maximum(1e-8,peak))) if len(cum) else 0.0
        std=np.std(returns) if len(returns)>1 else 1e-8
        sharpe=(np.mean(returns)/std)*np.sqrt(252) if std>0 else 0.0
        down=returns[returns<0]; dstd=np.std(down) if len(down)>1 else 1e-8
        sortino=(np.mean(returns)/dstd)*np.sqrt(252) if dstd>0 else 0.0
        return PerformanceReport(total, win_rate, np.sum(pnl)/total if total else 0, net_pnl/total if total else 0, gross_profit/gross_loss, np.mean(pnl)/max(1e-8,abs(np.mean(losses))) if len(losses) else 0, max_dd, sharpe, sortino, fee_costs, slippage_costs, 1.0)
