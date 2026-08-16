"""MODULE: tests.test_p0d_recovery"""
import os, pytest, tempfile
from tokocrypto_bot.persistence.database import DatabaseManager
from tokocrypto_bot.persistence.lifecycle_state import ApplicationState
from tokocrypto_bot.recovery.startup_recovery import StartupRecoveryOrchestrator
from tokocrypto_bot.recovery.single_instance import InstanceAlreadyRunningException, SingleInstanceLock
from tokocrypto_bot.execution.order_state_machine import OrderStateMachine
class MockExchangeAdapter:
    def fetch_open_orders(self, symbol=None): return []
    def fetch_order_by_client_id(self, symbol, client_order_id): return None
    def fetch_recent_trades(self, symbol, limit=50): return []
    def fetch_account_balances(self): return {"USDT":{"free":1000.0,"locked":0.0}}
@pytest.fixture
def recovery_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        db=DatabaseManager(db_path=os.path.join(tmpdir,"recovery.db"))
        mock=MockExchangeAdapter(); orch=StartupRecoveryOrchestrator(db, mock, lock_name="NVRA_TEST_LOCK")
        yield orch, db, mock
def test_single_instance_lock_blocks_second_instance():
    l1,l2=SingleInstanceLock("NVRA_UNIQUE_TEST_MUTEX"),SingleInstanceLock("NVRA_UNIQUE_TEST_MUTEX")
    assert l1.acquire() is True
    with pytest.raises(InstanceAlreadyRunningException): l2.acquire()
    l1.release(); assert l2.acquire() is True; l2.release()
def test_startup_recovery_gate_clean_boot(recovery_env):
    orch,_,_=recovery_env; assert orch.run_startup_recovery_gate()==ApplicationState.READY; orch.instance_lock.release()
def test_startup_recovery_reconciles_unclean_shutdown(recovery_env):
    orch,_,mock=recovery_env
    cid=OrderStateMachine.generate_client_order_id("EXEC-UN","SIG-01","BTCUSDT","BUY")
    orch.state_mgr.create_order_intent(cid,"EXEC-UN","SIG-01","BTCUSDT","BUY","LIMIT",60000.0,0.5)
    orch.state_mgr.transition_order_state(cid,"CREATED","SUBMITTING","POST_SENT")
    mock.fetch_order_by_client_id=lambda s,c:{"orderId":"998","clientOrderId":cid,"symbol":"BTCUSDT","status":"FILLED","price":"60000","origQty":"0.5","executedQty":"0.5","cummulativeQuoteQty":"30000"}
    assert orch.run_startup_recovery_gate()==ApplicationState.READY
    assert orch.state_mgr.get_order(cid)["status"]=="FILLED"; orch.instance_lock.release()
