"""ML Model Promotion Gate"""
import logging
from dataclasses import dataclass
from tokocrypto_bot.quant.performance_evaluator import PerformanceReport
logger = logging.getLogger("NVRA.MLPromotionGate")
@dataclass(frozen=True)
class ModelPromotionResult:
    champion_version: str
    challenger_version: str
    is_promoted: bool
    champion_expectancy: float
    challenger_expectancy: float
    expectancy_lift_pct: float
    reason: str
class ModelPromotionGate:
    def __init__(self, min_expectancy_lift_pct: float = 0.10):
        self.min_lift = min_expectancy_lift_pct
    def evaluate_promotion(self, champion_report, challenger_report, champion_version, challenger_version):
        if challenger_report.total_trades < 30:
            return ModelPromotionResult(champion_version, challenger_version, False, champion_report.net_expectancy, challenger_report.net_expectancy, 0.0, "sample < 30")
        if challenger_report.net_expectancy <= 0:
            return ModelPromotionResult(champion_version, challenger_version, False, champion_report.net_expectancy, challenger_report.net_expectancy, 0.0, "expectancy <= 0")
        champ_exp = max(1e-8, champion_report.net_expectancy)
        lift = (challenger_report.net_expectancy - champ_exp) / champ_exp
        if lift >= self.min_lift and challenger_report.max_drawdown_pct <= champion_report.max_drawdown_pct:
            return ModelPromotionResult(champion_version, challenger_version, True, champion_report.net_expectancy, challenger_report.net_expectancy, lift, f"lift {lift*100:.1f}%")
        return ModelPromotionResult(champion_version, challenger_version, False, champion_report.net_expectancy, challenger_report.net_expectancy, lift, f"lift insufficient")
