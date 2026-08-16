"""MODULE: tokocrypto_bot.recovery.live_gate"""
import logging
from dataclasses import dataclass
from typing import List
from tokocrypto_bot.persistence.database import DatabaseManager
from tokocrypto_bot.persistence.state_manager import StateManager
from tokocrypto_bot.security.credential_manager import SecureCredentialStore

logger = logging.getLogger("NVRA.LiveGate")

@dataclass(frozen=True)
class LiveGateVerificationResult:
    live_allowed: bool
    failed_checks: List[str]
    passed_checks: List[str]
    summary: str

class HardLiveGate:
    def __init__(self, db_mgr: DatabaseManager):
        self.db = db_mgr
        self.state_mgr = StateManager(db_mgr)
        self.cred_store = SecureCredentialStore()

    def verify_all_live_conditions(self, is_p0_passed: bool, is_p1_paper_passed: bool, is_model_valid: bool, is_strategy_healthy: bool, is_kill_switch_active: bool = False) -> LiveGateVerificationResult:
        passed, failed = [], []
        if is_p0_passed: passed.append("P0_RELIABILITY_PASS")
        else: failed.append("P0_RELIABILITY_FAIL")
        if is_p1_paper_passed: passed.append("P1_PAPER_SHADOW_PASS")
        else: failed.append("P1_PAPER_SHADOW_FAIL")
        conn = self.db.get_connection()
        try:
            res = conn.cursor().execute("PRAGMA integrity_check;").fetchone()[0]
            if res.lower() == "ok": passed.append("DB_INTEGRITY_PASS")
            else: failed.append("DB_INTEGRITY_FAIL")
        except Exception:
            failed.append("DB_INTEGRITY_EXCEPTION")
        finally:
            conn.close()
        unresolved = self.state_mgr.get_unresolved_orders()
        if len(unresolved) == 0: passed.append("ZERO_UNRESOLVED_ORDERS_PASS")
        else: failed.append(f"UNRESOLVED_ORDERS_EXIST_COUNT_{len(unresolved)}")
        if is_model_valid: passed.append("MODEL_VALIDATION_PASS")
        else: failed.append("MODEL_VALIDATION_FAIL")
        if is_strategy_healthy: passed.append("STRATEGY_HEALTH_PASS")
        else: failed.append("STRATEGY_HEALTH_FAIL")
        if not is_kill_switch_active: passed.append("KILL_SWITCH_IDLE_PASS")
        else: failed.append("KILL_SWITCH_ACTIVE_FAIL")
        key, secret = self.cred_store.load_api_credentials()
        if key and secret: passed.append("CREDENTIAL_DPAPI_PASS")
        else: failed.append("CREDENTIAL_MISSING_FAIL")
        live_allowed = len(failed) == 0
        summary = "LIVE TRADING UNLOCKED" if live_allowed else f"LIVE TRADING BLOCKED ({len(failed)} critical checks failed)"
        return LiveGateVerificationResult(live_allowed=live_allowed, failed_checks=failed, passed_checks=passed, summary=summary)
