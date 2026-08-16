"""MODULE: tests.test_reconciliation — P0-C Reconciliation Engine tests"""
import os, pytest, tempfile
from typing import List, Dict, Any, Optional
from tokocrypto_bot.persistence.database import DatabaseManager
from tokocrypto_bot.persistence.migrations import run_migrations
from tokocrypto_bot.persistence.state_manager import StateManager
from tokocrypto_bot.execution.order_state_machine import OrderStateMachine
from tokocrypto_bot.execution.reconciliation import ReconciliationEngine, SystemRecoveryStatus
class MockExchangeAdapter:
    def __init__(self):
        self.open_orders_response=[]; self.order_detail_response={}; self.recent_trades_response=[]
    def fetch_open_orders(self, symbol=None): return self.open_orders_response
    def fetch_order_by_client_id(self, symbol, client_order_id): return self.order_detail_response.get(client_order_id)
    def fetch_recent_trades(self, symbol, limit=50): return self.recent_trades_response
    def fetch_account_balances(self): return {"USDT":{"free":1000.0,"locked":0.0}}
@pytest.fixture
def setup_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        db=DatabaseManager(db_path=os.path.join(tmpdir,"recon.db")); run_migrations(db)
        sm=StateManager(db); mock=MockExchangeAdapter()
        # ReconciliationEngine may take (state, lifecycle, exchange) or (state, exchange)
        try:
            from tokocrypto_bot.persistence.lifecycle_state import LifecycleManager
            engine=ReconciliationEngine(sm, LifecycleManager(db), mock)
        except TypeError:
            engine=ReconciliationEngine(sm, mock)
        yield sm, mock, engine
def test_reconcile_filled_order_via_order_detail(setup_env):
    sm, mock, engine = setup_env
    cid=OrderStateMachine.generate_client_order_id("EXEC-001","SIG-01","BTCUSDT","BUY")
    sm.create_order_intent(cid,"EXEC-001","SIG-01","BTCUSDT","BUY","LIMIT",60000.0,0.1)
    sm.transition_order_state(cid,"CREATED","SUBMITTING","POST_SENT")
    sm.transition_order_state(cid,"SUBMITTING","UNKNOWN","NETWORK_TIMEOUT")
    mock.order_detail_response[cid]={"orderId":"12345678","clientOrderId":cid,"symbol":"BTCUSDT","status":"FILLED","price":"60000.0","origQty":"0.1","executedQty":"0.1","cummulativeQuoteQty":"6000.0"}
    status=engine.reconcile_all_unresolved_orders("EXEC-001")
    assert status==SystemRecoveryStatus.RECOVERY_COMPLETE
    assert sm.get_order(cid)["status"]=="FILLED"
def test_reconcile_not_found_triggers_safe_mode(setup_env):
    sm, mock, engine = setup_env
    cid=OrderStateMachine.generate_client_order_id("EXEC-002","SIG-02","ETHUSDT","BUY")
    sm.create_order_intent(cid,"EXEC-002","SIG-02","ETHUSDT","BUY","LIMIT",3000.0,1.0)
    sm.transition_order_state(cid,"CREATED","SUBMITTING","POST_SENT")
    sm.transition_order_state(cid,"SUBMITTING","UNKNOWN","NETWORK_TIMEOUT")
    status=engine.reconcile_all_unresolved_orders("EXEC-002")
    assert status==SystemRecoveryStatus.SAFE_MODE
    assert sm.get_order(cid)["status"]=="UNKNOWN"
def test_reconcile_api_error_preserves_unknown(setup_env):
    sm, mock, engine = setup_env
    cid=OrderStateMachine.generate_client_order_id("EXEC-003","SIG-03","SOLUSDT","BUY")
    sm.create_order_intent(cid,"EXEC-003","SIG-03","SOLUSDT","BUY","LIMIT",150.0,2.0)
    sm.transition_order_state(cid,"CREATED","SUBMITTING","POST_SENT")
    mock.fetch_open_orders=lambda *a,**k: (_ for _ in ()).throw(ConnectionError("502"))
    status=engine.reconcile_all_unresolved_orders("EXEC-003")
    assert status==SystemRecoveryStatus.SAFE_MODE
    assert sm.get_order(cid)["status"]=="UNKNOWN"
