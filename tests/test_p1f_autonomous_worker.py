"""MODULE: tests.test_p1f_autonomous_worker"""
import os, pytest, tempfile
from tokocrypto_bot.persistence.migrations import run_migrations
from tokocrypto_bot.persistence.lifecycle_state import ApplicationState
from tokocrypto_bot.application import AutonomousTradingWorker, ExecutionMode
from tokocrypto_bot.strategy.decision import DecisionAction
from tokocrypto_bot.strategy.portfolio import PositionPlan, RiskDecision, RiskAction
@pytest.fixture
def worker_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        worker=AutonomousTradingWorker(mode=ExecutionMode.PAPER, db_path=os.path.join(tmpdir,"p1f.db"))
        run_migrations(worker.db_mgr); yield worker
def test_paper_worker_cycle_execution(worker_env):
    worker_env._run_single_autonomous_cycle()
    assert worker_env.orchestrator.lifecycle_mgr.current_state==ApplicationState.READY
def test_network_timeout_transitions_to_unknown_without_retry(worker_env):
    worker=worker_env; worker.mode=ExecutionMode.LIVE
    worker.exchange.post_order_non_retry=lambda *a,**k: (_ for _ in ()).throw(ConnectionError("Timeout"))
    plan=PositionPlan("BTCUSDT",1700000000000,DecisionAction.BUY,100.0,0.002,50000.0,48000.0,54000.0,
        RiskDecision("BTCUSDT",RiskAction.ALLOW,100,100,0.01,0.01,0.04,4.0,0.9,[], ["PASS"]))
    worker._execute_single_position_plan(plan)
    unresolved=worker.state_mgr.get_unresolved_orders()
    assert len(unresolved)==1 and unresolved[0]["status"]=="UNKNOWN"
