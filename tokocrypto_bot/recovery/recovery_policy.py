"""MODULE: tokocrypto_bot.recovery.recovery_policy"""
from enum import Enum
from dataclasses import dataclass
class RecoveryDecision(str, Enum):
    PROCEED_TO_READY="PROCEED_TO_READY"; ENTER_PAUSED="ENTER_PAUSED"; ENTER_SAFE_MODE="ENTER_SAFE_MODE"; ABORT_EXIT="ABORT_EXIT"
@dataclass(frozen=True)
class PolicyEvaluationResult:
    decision: RecoveryDecision; reason: str
class RecoveryPolicy:
    @staticmethod
    def evaluate_startup_conditions(db_integrity_ok, unclean_shutdown, unresolved_orders_count, reconciliation_success, balance_match, position_match, exchange_available):
        if not db_integrity_ok: return PolicyEvaluationResult(RecoveryDecision.ENTER_SAFE_MODE, "DB integrity failed")
        if not exchange_available: return PolicyEvaluationResult(RecoveryDecision.ENTER_PAUSED, "Exchange unreachable")
        if unresolved_orders_count > 0 and not reconciliation_success: return PolicyEvaluationResult(RecoveryDecision.ENTER_SAFE_MODE, "Unresolved orders")
        if not balance_match or not position_match: return PolicyEvaluationResult(RecoveryDecision.ENTER_SAFE_MODE, "State mismatch")
        return PolicyEvaluationResult(RecoveryDecision.PROCEED_TO_READY, "All checks passed")
