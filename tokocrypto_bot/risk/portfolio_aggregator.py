"""MODULE: tokocrypto_bot.risk.portfolio_aggregator"""
import logging
from dataclasses import dataclass
from typing import Dict, List
logger = logging.getLogger("NVRA.PortfolioAggregatorRisk")
@dataclass(frozen=True)
class MacroRiskEvaluation:
    is_allowed: bool; total_crypto_exposure_pct: float; usdt_concentration_pct: float
    idr_concentration_pct: float; high_correlation_cluster_detected: bool; rejection_reasons: List[str]
class PortfolioLevelRiskController:
    def __init__(self, max_aggregate_crypto_exposure_pct=0.40, max_quote_asset_concentration_pct=0.70):
        self.max_crypto_exp=max_aggregate_crypto_exposure_pct; self.max_quote_conc=max_quote_asset_concentration_pct
    def evaluate_macro_portfolio_risk(self, total_equity_usdt, active_positions):
        if total_equity_usdt <= 0: return MacroRiskEvaluation(False,0,0,0,False,["INVALID_EQUITY"])
        total_exp=sum(active_positions.values()); pct=total_exp/total_equity_usdt
        reasons=[]
        if pct > self.max_crypto_exp: reasons.append(f"AGGREGATE_CRYPTO_EXPOSURE_LIMIT_EXCEEDED")
        usdt=sum(v for s,v in active_positions.items() if s.endswith("USDT"))
        idr=sum(v for s,v in active_positions.items() if s.endswith("BIDR") or s.endswith("IDR"))
        usdt_c=usdt/max(1.0,total_exp) if total_exp else 0; idr_c=idr/max(1.0,total_exp) if total_exp else 0
        if usdt_c > self.max_quote_conc: reasons.append("USDT_QUOTE_CONCENTRATION_EXCEEDED")
        return MacroRiskEvaluation(len(reasons)==0, pct, usdt_c, idr_c, False, reasons)
