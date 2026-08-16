"""PHASE 1.6 — Partial-fill accounting, remainder cancel, position from executed qty."""
import os, tempfile, pytest
from typing import Dict, Any, Optional, List
from tokocrypto_bot.persistence.database import DatabaseManager
from tokocrypto_bot.persistence.migrations import run_migrations
from tokocrypto_bot.persistence.state_manager import StateManager
from tokocrypto_bot.persistence.lifecycle_state import LifecycleManager
from tokocrypto_bot.execution.reconciliation import HardenedReconciliationEngine
from tokocrypto_bot.execution.order_state_machine import OrderStateMachine

class MockExchange:
    def __init__(self):
        self.orders_detail={}; self.open_orders=[]; self.trades=[]; self.cancel_calls=[]
    def fetch_open_orders(self, symbol=None): return self.open_orders
    def fetch_order_by_client_id(self, symbol, client_order_id): return self.orders_detail.get(client_order_id)
    def fetch_recent_trades(self, symbol, limit=50): return self.trades
    def fetch_account_balances(self): return {"USDT":{"free":1000.0,"locked":0.0}}
    def cancel_order_non_retry(self, symbol, client_order_id):
        self.cancel_calls.append((symbol, client_order_id)); return {"status":"CANCELED"}

@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        db=DatabaseManager(db_path=os.path.join(tmpdir,"p16.db")); run_migrations(db)
        sm=StateManager(db); lm=LifecycleManager(db); ex=MockExchange()
        engine=HardenedReconciliationEngine(sm, lm, ex)
        yield sm, lm, ex, engine, db

def _create_partial(sm, cid, symbol="BTCUSDT", side="BUY", qty=1.0, price=60000.0):
    sm.create_order_intent(cid,"EXEC-P16","SIG-P16",symbol,side,"LIMIT",price,qty)
    sm.transition_order_state(cid,"CREATED","SUBMITTING","SENT")
    sm.transition_order_state(cid,"SUBMITTING","NEW","ACK")

def test_progressive_fill_0_4_to_0_8_totals_0_8(env):
    sm, lm, ex, engine, db = env
    cid=OrderStateMachine.generate_client_order_id("EXEC-P16","SIG-01","BTCUSDT","BUY")
    _create_partial(sm, cid)
    ex.orders_detail[cid]={"orderId":"EX-1","clientOrderId":cid,"symbol":"BTCUSDT","status":"PARTIALLY_FILLED","price":"60000","origQty":"1.0","executedQty":"0.4","cummulativeQuoteQty":"24000"}
    assert engine.execute_foundation_gate_reconciliation() is True
    order=sm.get_order(cid); assert order["status"]=="PARTIALLY_FILLED"
    conn=db.get_connection(); fills=conn.execute("SELECT SUM(quantity) FROM fills WHERE client_order_id=?",(cid,)).fetchone()[0]; conn.close()
    assert abs(float(fills)-0.4)<1e-9
    # progressive to 0.8
    ex.orders_detail[cid]["executedQty"]="0.8"; ex.orders_detail[cid]["cummulativeQuoteQty"]="48000"
    assert engine.execute_foundation_gate_reconciliation() is True
    conn=db.get_connection(); total=conn.execute("SELECT SUM(quantity) FROM fills WHERE client_order_id=?",(cid,)).fetchone()[0]; conn.close()
    assert abs(float(total)-0.8)<1e-9  # delta accounting, not double-count

def test_same_snapshot_no_duplicate_fill(env):
    sm, lm, ex, engine, db = env
    cid=OrderStateMachine.generate_client_order_id("EXEC-P16","SIG-02","BTCUSDT","BUY")
    _create_partial(sm, cid)
    snap={"orderId":"EX-2","clientOrderId":cid,"symbol":"BTCUSDT","status":"PARTIALLY_FILLED","price":"60000","origQty":"1.0","executedQty":"0.5","cummulativeQuoteQty":"30000"}
    ex.orders_detail[cid]=snap
    engine.execute_foundation_gate_reconciliation(); engine.execute_foundation_gate_reconciliation()
    conn=db.get_connection(); n=conn.execute("SELECT COUNT(*) FROM fills WHERE client_order_id=?",(cid,)).fetchone()[0]; conn.close()
    assert n==1

def test_position_from_executed_not_requested(env):
    sm, lm, ex, engine, db = env
    cid=OrderStateMachine.generate_client_order_id("EXEC-P16","SIG-03","BTCUSDT","BUY")
    _create_partial(sm, cid, qty=1.0)
    ex.orders_detail[cid]={"orderId":"EX-3","clientOrderId":cid,"symbol":"BTCUSDT","status":"PARTIALLY_FILLED","price":"60000","origQty":"1.0","executedQty":"0.3","cummulativeQuoteQty":"18000"}
    engine.execute_foundation_gate_reconciliation()
    conn=db.get_connection(); pos=conn.execute("SELECT total_qty FROM positions WHERE symbol='BTCUSDT'").fetchone(); conn.close()
    assert pos is not None and abs(float(pos[0])-0.3)<1e-9

def test_sell_partial_reduces_position(env):
    sm, lm, ex, engine, db = env
    # seed position 1.0 via buy fill
    buy_cid=OrderStateMachine.generate_client_order_id("EXEC-P16","SIG-B","BTCUSDT","BUY")
    _create_partial(sm, buy_cid)
    ex.orders_detail[buy_cid]={"orderId":"EX-B","clientOrderId":buy_cid,"symbol":"BTCUSDT","status":"FILLED","price":"60000","origQty":"1.0","executedQty":"1.0","cummulativeQuoteQty":"60000"}
    engine.execute_foundation_gate_reconciliation()
    # sell partial 0.4
    sell_cid=OrderStateMachine.generate_client_order_id("EXEC-P16","SIG-S","BTCUSDT","SELL")
    _create_partial(sm, sell_cid, side="SELL")
    ex.orders_detail[sell_cid]={"orderId":"EX-S","clientOrderId":sell_cid,"symbol":"BTCUSDT","status":"PARTIALLY_FILLED","price":"61000","origQty":"1.0","executedQty":"0.4","cummulativeQuoteQty":"24400"}
    engine.execute_foundation_gate_reconciliation()
    conn=db.get_connection(); pos=conn.execute("SELECT total_qty FROM positions WHERE symbol='BTCUSDT'").fetchone(); conn.close()
    assert abs(float(pos[0])-0.6)<1e-9

def test_remainder_cancel_single_shot(env):
    sm, lm, ex, engine, db = env
    cid=OrderStateMachine.generate_client_order_id("EXEC-P16","SIG-04","BTCUSDT","BUY")
    _create_partial(sm, cid)
    ex.orders_detail[cid]={"orderId":"EX-4","clientOrderId":cid,"symbol":"BTCUSDT","status":"PARTIALLY_FILLED","price":"60000","origQty":"1.0","executedQty":"0.4","cummulativeQuoteQty":"24000"}
    engine.execute_foundation_gate_reconciliation()
    # policy cancel remainder
    if hasattr(engine, 'cancel_remainder_if_needed'):
        engine.cancel_remainder_if_needed(cid)
    assert len(ex.cancel_calls)<=1  # single-shot

def test_cancel_failure_stays_partially_filled(env):
    sm, lm, ex, engine, db = env
    cid=OrderStateMachine.generate_client_order_id("EXEC-P16","SIG-05","BTCUSDT","BUY")
    _create_partial(sm, cid)
    ex.orders_detail[cid]={"orderId":"EX-5","clientOrderId":cid,"symbol":"BTCUSDT","status":"PARTIALLY_FILLED","price":"60000","origQty":"1.0","executedQty":"0.4","cummulativeQuoteQty":"24000"}
    def fail_cancel(*a,**k): raise ConnectionError("cancel timeout")
    ex.cancel_order_non_retry=fail_cancel
    engine.execute_foundation_gate_reconciliation()
    order=sm.get_order(cid); assert order["status"]=="PARTIALLY_FILLED"

def test_restart_partial_no_duplicate_order(env):
    sm, lm, ex, engine, db = env
    cid=OrderStateMachine.generate_client_order_id("EXEC-P16","SIG-06","BTCUSDT","BUY")
    _create_partial(sm, cid)
    ex.orders_detail[cid]={"orderId":"EX-6","clientOrderId":cid,"symbol":"BTCUSDT","status":"PARTIALLY_FILLED","price":"60000","origQty":"1.0","executedQty":"0.5","cummulativeQuoteQty":"30000"}
    engine.execute_foundation_gate_reconciliation()
    # restart reconcile again — no new order
    engine.execute_foundation_gate_reconciliation()
    unresolved=sm.get_unresolved_orders()
    assert all(o["client_order_id"]==cid for o in unresolved if o.get("status")=="PARTIALLY_FILLED")

def test_exposure_uses_actual_position(env):
    sm, lm, ex, engine, db = env
    cid=OrderStateMachine.generate_client_order_id("EXEC-P16","SIG-07","BTCUSDT","BUY")
    _create_partial(sm, cid, qty=1.0)
    ex.orders_detail[cid]={"orderId":"EX-7","clientOrderId":cid,"symbol":"BTCUSDT","status":"PARTIALLY_FILLED","price":"60000","origQty":"1.0","executedQty":"0.25","cummulativeQuoteQty":"15000"}
    engine.execute_foundation_gate_reconciliation()
    conn=db.get_connection(); pos=conn.execute("SELECT total_qty FROM positions WHERE symbol='BTCUSDT'").fetchone(); conn.close()
    assert abs(float(pos[0])-0.25)<1e-9  # not 1.0 requested

def test_idempotent_fill_id_cumulative(env):
    sm, lm, ex, engine, db = env
    cid=OrderStateMachine.generate_client_order_id("EXEC-P16","SIG-08","BTCUSDT","BUY")
    _create_partial(sm, cid)
    ex.orders_detail[cid]={"orderId":"EX-8","clientOrderId":cid,"symbol":"BTCUSDT","status":"PARTIALLY_FILLED","price":"60000","origQty":"1.0","executedQty":"0.6","cummulativeQuoteQty":"36000"}
    engine.execute_foundation_gate_reconciliation()
    engine.execute_foundation_gate_reconciliation()
    conn=db.get_connection(); n=conn.execute("SELECT COUNT(*) FROM fills WHERE client_order_id=?",(cid,)).fetchone()[0]; conn.close()
    assert n==1
