"""
MODULE: tests.test_foundation_gate
DESCRIPTION: Comprehensive Crash & Idempotency Test Suite for Foundation Gate (P0-FIX).
"""

import os
import pytest
import tempfile
from tokocrypto_bot.persistence.database import DatabaseManager
from tokocrypto_bot.persistence.migrations import run_migrations
from tokocrypto_bot.persistence.state_manager import StateManager
from tokocrypto_bot.persistence.lifecycle_state import LifecycleManager, ApplicationState
from tokocrypto_bot.execution.order_state_machine import OrderStateMachine, OrderStatus, InvalidStateTransitionException, InvalidClientOrderIdException
from tokocrypto_bot.execution.reconciliation import HardenedReconciliationEngine, ReconciliationDecision


class MockExchange:
    def __init__(self):
        self.orders_detail = {}
        self.open_orders = []
        self.trades = []

    def fetch_open_orders(self, symbol=None): return self.open_orders
    def fetch_order_by_client_id(self, symbol, client_order_id): return self.orders_detail.get(client_order_id)
    def fetch_recent_trades(self, symbol, limit=50): return self.trades
    def fetch_account_balances(self): return {"USDT": {"free": 1000.0, "locked": 0.0}}


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "foundation_gate.db")
        db_mgr = DatabaseManager(db_path=db_path)
        run_migrations(db_mgr)
        state_mgr = StateManager(db_mgr)
        lifecycle_mgr = LifecycleManager(db_mgr)
        mock_ex = MockExchange()
        engine = HardenedReconciliationEngine(state_mgr, lifecycle_mgr, mock_ex)
        yield state_mgr, lifecycle_mgr, mock_ex, engine, db_mgr


def test_sqlite_integrity_check(env):
    _, lifecycle_mgr, _, _, _ = env
    assert lifecycle_mgr.verify_database_integrity() is True


def test_client_order_id_length_limit():
    with pytest.raises(InvalidClientOrderIdException):
        OrderStateMachine.generate_client_order_id("EXEC-WAY-TOO-LONG-NAME-THAT-EXCEEDS-TOKOCRYPTO-LIMIT-FOR-SURE", "SIG-01", "BTCUSDT", "BUY")


def test_terminal_state_immutability():
    with pytest.raises(InvalidStateTransitionException):
        OrderStateMachine.validate_transition(OrderStatus.FILLED, OrderStatus.SUBMITTING)
    with pytest.raises(InvalidStateTransitionException):
        OrderStateMachine.validate_transition(OrderStatus.CANCELED, OrderStatus.NEW)


def test_partial_fill_precision_and_deduplication(env):
    state_mgr, lifecycle_mgr, mock_ex, engine, _ = env
    cid = OrderStateMachine.generate_client_order_id("EXEC-01", "SIG-01", "BTCUSDT", "BUY")

    # Intent
    state_mgr.create_order_intent(cid, "EXEC-01", "SIG-01", "BTCUSDT", "BUY", "LIMIT", 60000.0, 1.0)
    state_mgr.transition_order_state(cid, "CREATED", "SUBMITTING", "SENT")

    # Mock Partial Fill (0.4 BTC executed of 1.0 BTC)
    mock_ex.orders_detail[cid] = {
        "orderId": "EX-99", "clientOrderId": cid, "symbol": "BTCUSDT",
        "status": "PARTIALLY_FILLED", "price": "60000.0", "origQty": "1.0", "executedQty": "0.4", "cummulativeQuoteQty": "24000.0"
    }

    # First Reconciliation
    assert engine.execute_foundation_gate_reconciliation() is True
    order = state_mgr.get_order(cid)
    assert order["status"] == "PARTIALLY_FILLED"
    assert lifecycle_mgr.current_state == ApplicationState.READY

    # Second Reconciliation (Idempotency Check - Fills must not duplicate)
    assert engine.execute_foundation_gate_reconciliation() is True
    conn = state_mgr.db.get_connection()
    fills_count = conn.execute("SELECT COUNT(*) FROM fills WHERE client_order_id=?", (cid,)).fetchone()[0]
    conn.close()
    assert fills_count == 1, "Fill record must be deduplicated on repeated reconciliation!"
