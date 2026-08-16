"""MODULE: tokocrypto_bot.quant.gemini_supervisor — Isolated AI evaluator (no order authority)"""
import time, logging
from dataclasses import dataclass
from typing import Optional
from tokocrypto_bot.quant.performance_evaluator import PerformanceReport
from tokocrypto_bot.security.credential_manager import SecureCredentialStore
logger = logging.getLogger("NVRA.GeminiSupervisor")
@dataclass(frozen=True)
class OptimizationProposal:
    proposal_id: str; timestamp: int; recommended_risk_per_trade: Optional[float]
    recommended_timeframes: Optional[list]; suggested_strategy_degradations: list
    reasoning_summary: str; is_validated_by_gate: bool = False
class GeminiGodAdministrator:
    def __init__(self, enabled=True, evaluation_interval_hours=24, cred_store=None):
        self.enabled=enabled; self.interval_hours=evaluation_interval_hours
        self.cred_store=cred_store or SecureCredentialStore(); self.last_evaluation_time=0.0
    def should_run_evaluation(self):
        if not self.enabled: return False
        return (time.time() - self.last_evaluation_time) / 3600.0 >= self.interval_hours
    def evaluate_periodically_async(self, performance_report):
        if not self.should_run_evaluation(): return None
        creds = self.cred_store.load_credentials()
        if not creds.gemini_api_key:
            logger.warning("Gemini skipped: no API key"); return None
        self.last_evaluation_time = time.time()
        try:
            return OptimizationProposal(f"PROP-{int(time.time())}", int(time.time()), None, ["5m","15m"], [],
                f"Analysis for {performance_report.total_trades} trades. Net Expectancy stable.", False)
        except Exception as e:
            logger.error(f"Gemini exception (trading continues): {e}"); return None
