"""MODULE: tokocrypto_bot.recovery.startup_recovery"""
import logging
from tokocrypto_bot.persistence.database import DatabaseManager
from tokocrypto_bot.persistence.migrations import run_migrations
from tokocrypto_bot.persistence.state_manager import StateManager
from tokocrypto_bot.persistence.lifecycle_state import LifecycleManager, ApplicationState
from tokocrypto_bot.execution.reconciliation import HardenedReconciliationEngine
from tokocrypto_bot.recovery.single_instance import SingleInstanceLock
from tokocrypto_bot.recovery.recovery_policy import RecoveryPolicy, RecoveryDecision
from tokocrypto_bot.recovery.shutdown_manager import ShutdownManager
logger = logging.getLogger("NVRA.StartupRecovery")
class StartupRecoveryOrchestrator:
    def __init__(self, db_mgr, exchange_adapter, lock_name="NVRA_TOKOCRYPTO_TRADING_INSTANCE"):
        self.db_mgr=db_mgr; self.exchange=exchange_adapter
        self.instance_lock=SingleInstanceLock(lock_name)
        run_migrations(self.db_mgr)
        self.state_mgr=StateManager(self.db_mgr)
        self.lifecycle_mgr=LifecycleManager(self.db_mgr)
        self.reconciler=HardenedReconciliationEngine(self.state_mgr, self.lifecycle_mgr, self.exchange)
        self.shutdown_mgr=ShutdownManager(self.lifecycle_mgr, self.instance_lock)
    def _verify_position_consistency(self):
        try:
            conn=self.db_mgr.get_connection()
            try:
                pos_rows=conn.execute("SELECT symbol, total_qty FROM positions WHERE exchange_id='TOKOCRYPTO' AND total_qty > 0").fetchall()
                fill_rows=conn.execute("SELECT symbol, side, COALESCE(SUM(quantity),0) FROM fills WHERE exchange_id='TOKOCRYPTO' GROUP BY symbol, side").fetchall()
            finally: conn.close()
            from_fills={}
            for sym, side, qty in fill_rows:
                signed=float(qty) if str(side).upper()=="BUY" else -float(qty)
                from_fills[str(sym)]=from_fills.get(str(sym),0.0)+signed
            pos_map={str(r[0]): float(r[1]) for r in pos_rows}
            for sym, total_qty in pos_map.items():
                if abs(total_qty - from_fills.get(sym,0.0)) > 1e-6: return False
            for sym, net in from_fills.items():
                if net > 1e-6 and sym not in pos_map: return False
            return True
        except Exception: return False
    def run_startup_recovery_gate(self):
        self.instance_lock.acquire()
        db_ok=self.lifecycle_mgr.verify_database_integrity()
        if not db_ok:
            self.lifecycle_mgr.set_state(ApplicationState.SAFE_MODE, "DB Integrity Failed"); return ApplicationState.SAFE_MODE
        unclean = self.lifecycle_mgr.current_state not in (ApplicationState.STOPPED, ApplicationState.STARTING)
        self.lifecycle_mgr.set_state(ApplicationState.RECOVERING, "Startup Reconciliation")
        unresolved=self.state_mgr.get_unresolved_orders()
        recon_ok=True
        if unresolved: recon_ok=self.reconciler.execute_foundation_gate_reconciliation()
        exchange_ok=True
        try: self.exchange.fetch_account_balances()
        except Exception: exchange_ok=False
        
        try:
            from tokocrypto_bot.execution.position_protection import PositionProtectionManager
            ppm = PositionProtectionManager(self.state_mgr, self.exchange)
            ppm.reconcile_pending_protections()
            conn = self.db_mgr.get_connection()
            try:
                rows = conn.execute(
                    "SELECT client_order_id, symbol, stop_price FROM orders WHERE status='FILLED' AND stop_price IS NOT NULL AND stop_price > 0"
                ).fetchall()
            finally:
                conn.close()
            for cid, symbol, stop in rows:
                conn = self.db_mgr.get_connection()
                try:
                    pos = conn.execute(
                        "SELECT total_qty FROM positions WHERE exchange_id=? AND symbol=?",
                        ("TOKOCRYPTO", symbol),
                    ).fetchone()
                finally:
                    conn.close()
                if pos and float(pos[0] or 0) > 0:
                    ppm.ensure_protection(symbol, cid, float(pos[0]), float(stop))
            naked = ppm.list_unprotected_positions()
            if naked:
                logger.critical(f"Unprotected open positions: {naked}")
                self.lifecycle_mgr.set_state(ApplicationState.SAFE_MODE, "UNPROTECTED_POSITIONS")
                return ApplicationState.SAFE_MODE
        except Exception as e:
            logger.error(f"Protection gate error: {e}")
            self.lifecycle_mgr.set_state(ApplicationState.SAFE_MODE, "PROTECTION_GATE_ERROR")
            return ApplicationState.SAFE_MODE

        policy=RecoveryPolicy.evaluate_startup_conditions(db_ok, unclean, len(unresolved), recon_ok, True, self._verify_position_consistency(), exchange_ok)
        if policy.decision==RecoveryDecision.PROCEED_TO_READY:
            self.lifecycle_mgr.set_state(ApplicationState.READY, policy.reason); return ApplicationState.READY
        if policy.decision==RecoveryDecision.ENTER_PAUSED:
            self.lifecycle_mgr.set_state(ApplicationState.PAUSED, policy.reason); return ApplicationState.PAUSED
        self.lifecycle_mgr.set_state(ApplicationState.SAFE_MODE, policy.reason); return ApplicationState.SAFE_MODE
