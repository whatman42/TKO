"""PHASE 1.3 — Execution Safety: duplicate protection, retry/backoff, UNKNOWN→EXPIRED, pair isolation."""
import os, time, pytest, tempfile
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from unittest.mock import patch
from tokocrypto_bot.persistence.database import DatabaseManager
from tokocrypto_bot.persistence.migrations import run_migrations
from tokocrypto_bot.persistence.state_manager import StateManager
from tokocrypto_bot.persistence.lifecycle_state import LifecycleManager, ApplicationState
from tokocrypto_bot.execution.order_state_machine import OrderStateMachine, OrderStatus
from tokocrypto_bot.execution.reconciliation import ReconciliationEngine, ReconciliationDecision, SystemRecoveryStatus
from tokocrypto_bot.application import AutonomousTradingWorker, ExecutionMode
from tokocrypto_bot.strategy.portfolio import PositionPlan, RiskDecision, RiskAction
from tokocrypto_bot.strategy.decision import DecisionAction
class ControllableExchange:
    def __init__(self):
        self.open_orders_response=[]; self.order_detail_response={}; self.recent_trades_response=[]
        self.lookup_calls=0; self.post_calls=0; self.raise_on_lookup=None; self.raise_on_fetch_open=None; self._attempt=0
    def fetch_open_orders(self, symbol=None):
        self.lookup_calls+=1; self._attempt+=1
        if self.raise_on_fetch_open: raise self.raise_on_fetch_open
        return self.open_orders_response
    def fetch_order_by_client_id(self, symbol, client_order_id):
        self.lookup_calls+=1
        if self.raise_on_lookup: raise self.raise_on_lookup
        return self.order_detail_response.get(client_order_id)
    def fetch_recent_trades(self, symbol, limit=50): return self.recent_trades_response
    def fetch_account_balances(self): return {"USDT":{"free":1000.0,"locked":0.0}}
    def post_order_non_retry(self, **kwargs):
        self.post_calls+=1; return {"orderId":"POSTED-1","status":"NEW","clientOrderId":kwargs.get("client_order_id")}
def _make_plan(symbol="BTCUSDT", price=50000.0, qty=0.01):
    rd=RiskDecision(symbol,RiskAction.ALLOW,price*qty,price*qty,0.01,0.1,0.02,100.0,0.1,[], ["TEST"])
    return PositionPlan(symbol,0,DecisionAction.BUY,price*qty,qty,price,price*0.96,price*1.04,rd)
@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        db=DatabaseManager(db_path=os.path.join(tmpdir,"p13.db")); run_migrations(db)
        sm=StateManager(db); lm=LifecycleManager(db); ex=ControllableExchange()
        yield sm, ex, ReconciliationEngine(sm,lm,ex), lm, db
def _live_worker(db_path, exchange, state_mgr):
    w=AutonomousTradingWorker(mode=ExecutionMode.PAPER, db_path=db_path)
    w.mode=ExecutionMode.LIVE; w.exchange=exchange; w.state_mgr=state_mgr; w._live_unlocked=True; return w
def _create_unknown(state_mgr, cid, symbol="BTCUSDT", age_days=0, execution_id="EXEC-1"):
    state_mgr.create_order_intent(cid,execution_id,"SIG-1",symbol,"BUY","LIMIT",100.0,0.1)
    state_mgr.transition_order_state(cid,"CREATED","SUBMITTING","POST_SENT")
    state_mgr.transition_order_state(cid,"SUBMITTING","UNKNOWN","NETWORK_TIMEOUT")
    if age_days:
        old=(datetime.now(timezone.utc)-timedelta(days=age_days)).isoformat()
        with state_mgr.db.get_connection() as conn:
            conn.execute("UPDATE orders SET created_at=?,updated_at=? WHERE client_order_id=?",(old,old,cid)); conn.commit()
def test_existing_client_order_id_suppresses_post(env):
    sm,ex,_,_,db=env; w=_live_worker(db.db_path,ex,sm)
    ex.fetch_order_by_client_id=lambda symbol,client_order_id:{"orderId":"EX-99","clientOrderId":client_order_id,"symbol":symbol,"status":"NEW","price":"50000","origQty":"0.01","executedQty":"0"}
    w._execute_single_position_plan(_make_plan()); assert ex.post_calls==0
def test_lookup_failure_suppresses_post_fail_closed(env):
    sm,ex,_,_,db=env; w=_live_worker(db.db_path,ex,sm)
    ex.raise_on_lookup=ConnectionError("lookup timeout"); ex.raise_on_fetch_open=ConnectionError("lookup timeout")
    w._execute_single_position_plan(_make_plan("ETHUSDT",3000,0.1)); assert ex.post_calls==0
    assert any(o["status"]=="UNKNOWN" for o in sm.get_unresolved_orders())
def test_api_error_retries_then_safe_mode(env):
    sm,ex,engine,_,_=env; cid=OrderStateMachine.generate_client_order_id("EXEC-R1","SIG-R1","BTCUSDT","BUY")
    _create_unknown(sm,cid,execution_id="EXEC-R1"); ex.raise_on_fetch_open=ConnectionError("502")
    with patch("time.sleep",return_value=None): status=engine.reconcile_all_unresolved_orders("EXEC-R1")
    assert status==SystemRecoveryStatus.SAFE_MODE and sm.get_order(cid)["status"]=="UNKNOWN"
def test_api_error_recovers_after_retry(env):
    sm,ex,engine,_,_=env; cid=OrderStateMachine.generate_client_order_id("EXEC-R2","SIG-R2","BTCUSDT","BUY")
    _create_unknown(sm,cid,execution_id="EXEC-R2")
    ex.order_detail_response[cid]={"orderId":"100","clientOrderId":cid,"symbol":"BTCUSDT","status":"FILLED","price":"100","origQty":"0.1","executedQty":"0.1","cummulativeQuoteQty":"10"}
    calls={"n":0}
    def flaky(symbol=None):
        calls["n"]+=1
        if calls["n"]==1: raise ConnectionError("502")
        return []
    ex.fetch_open_orders=flaky
    with patch("time.sleep",return_value=None): status=engine.reconcile_all_unresolved_orders("EXEC-R2")
    assert status==SystemRecoveryStatus.RECOVERY_COMPLETE and sm.get_order(cid)["status"]=="FILLED"
def test_not_found_stays_unknown_young(env):
    sm,ex,engine,_,_=env; cid=OrderStateMachine.generate_client_order_id("EXEC-NF1","SIG-NF1","ETHUSDT","BUY")
    _create_unknown(sm,cid,age_days=1,execution_id="EXEC-NF1")
    assert engine.reconcile_all_unresolved_orders("EXEC-NF1")==SystemRecoveryStatus.SAFE_MODE
    assert sm.get_order(cid)["status"]=="UNKNOWN"
def test_unknown_young_not_expired(env):
    sm,ex,engine,_,_=env; cid=OrderStateMachine.generate_client_order_id("EXEC-AGE1","SIG-A1","SOLUSDT","BUY")
    _create_unknown(sm,cid,age_days=3); res=engine.reconcile_single_order(sm.get_order(cid))
    assert res.decision==ReconciliationDecision.NOT_FOUND and res.target_order_status==OrderStatus.UNKNOWN
def test_unknown_aged_not_found_expires(env):
    sm,ex,engine,_,_=env; cid=OrderStateMachine.generate_client_order_id("EXEC-AGE2","SIG-A2","SOLUSDT","BUY")
    _create_unknown(sm,cid,age_days=8); order=sm.get_order(cid); res=engine.reconcile_single_order(order)
    assert res.decision==ReconciliationDecision.FOUND_EXPIRED and res.target_order_status==OrderStatus.EXPIRED
    assert engine._apply_reconciliation_result(order,res) and sm.get_order(cid)["status"]=="EXPIRED"
def test_unknown_aged_but_exists_on_exchange_not_expired(env):
    sm,ex,engine,_,_=env; cid=OrderStateMachine.generate_client_order_id("EXEC-AGE3","SIG-A3","SOLUSDT","BUY")
    _create_unknown(sm,cid,age_days=10)
    ex.order_detail_response[cid]={"orderId":"777","clientOrderId":cid,"symbol":"SOLUSDT","status":"NEW","price":"100","origQty":"0.1","executedQty":"0","cummulativeQuoteQty":"0"}
    res=engine.reconcile_single_order(sm.get_order(cid))
    assert res.decision!=ReconciliationDecision.FOUND_EXPIRED and res.target_order_status!=OrderStatus.EXPIRED
def test_pair_timeout_isolates_and_cycle_reaches_ready(env):
    sm,ex,engine,lm,db=env; w=AutonomousTradingWorker(mode=ExecutionMode.PAPER,db_path=db.db_path); w.pair_processing_timeout_sec=0.01
    class SlowMarket:
        def get_klines_dataframe(self,symbol,interval="5m",limit=210):
            time.sleep(0.05); import pandas as pd; return pd.DataFrame()
    class FakeRule: symbol="BTCUSDT"
    w.market_data_engine=SlowMarket(); w.universe_engine.get_active_universe=lambda:[FakeRule()]
    w._run_single_autonomous_cycle(); assert w.orchestrator.lifecycle_mgr.current_state==ApplicationState.READY
def test_post_timeout_no_automatic_retry(env):
    sm,ex,_,_,db=env; w=_live_worker(db.db_path,ex,sm)
    def boom(**kwargs): ex.post_calls+=1; raise TimeoutError("POST timeout")
    ex.fetch_order_by_client_id=lambda **kw:None; ex.fetch_open_orders=lambda **kw:[]; ex.post_order_non_retry=boom
    w._execute_single_position_plan(_make_plan()); assert ex.post_calls==1
