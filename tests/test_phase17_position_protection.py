"""Phase 1.7 — protective exit + reconciliation integration (TKO-native)."""
import os, tempfile, pytest
from datetime import datetime, timezone
from tokocrypto_bot.persistence.database import DatabaseManager, get_db_transaction
from tokocrypto_bot.persistence.migrations import run_migrations
from tokocrypto_bot.persistence.state_manager import StateManager
from tokocrypto_bot.execution.position_protection import PositionProtectionManager, PROTECTION_ACTIVE, PROTECTION_PENDING, PROTECTION_FILLED, PROTECTION_NONE
from tokocrypto_bot.execution.reconciliation import HardenedReconciliationEngine, ReconciliationResult, ReconciliationDecision
from tokocrypto_bot.execution.order_state_machine import OrderStatus
from tokocrypto_bot.persistence.lifecycle_state import LifecycleManager


class FakeExchange:
    def __init__(self):
        self.orders = {}
        self.post_calls = 0
        self.fail_post = False

    def fetch_order_by_client_id(self, symbol, client_order_id):
        return self.orders.get(client_order_id)

    def post_stop_loss_limit_non_retry(self, symbol, side, quantity, stop_price, limit_price=None, client_order_id=None):
        self.post_calls += 1
        if self.fail_post:
            raise TimeoutError("simulated timeout")
        oid = client_order_id or f"PROT-{len(self.orders)}"
        self.orders[oid] = {
            "orderId": f"EX-{oid}",
            "clientOrderId": oid,
            "status": "NEW",
            "symbol": symbol,
            "side": side,
            "executedQty": "0",
            "origQty": str(quantity),
            "price": str(limit_price or stop_price),
            "stopPrice": str(stop_price),
        }
        return self.orders[oid]

    def fetch_open_orders(self, symbol=None):
        return [o for o in self.orders.values() if o.get("status") in ("NEW", "OPEN", "PARTIALLY_FILLED")]

    def fetch_recent_trades(self, symbol, limit=50):
        return []

    def fetch_account_balances(self):
        return {"USDT": {"free": 1000.0, "locked": 0.0}}

    def cancel_order_non_retry(self, symbol, client_order_id=None, exchange_order_id=None):
        key = client_order_id or exchange_order_id
        if key in self.orders:
            self.orders[key]["status"] = "CANCELED"
        return {"status": "CANCELED"}


@pytest.fixture
def env(tmp_path):
    db_path = str(tmp_path / "t17.db")
    db = DatabaseManager(db_path)
    run_migrations(db)
    sm = StateManager(db)
    fx = FakeExchange()
    ppm = PositionProtectionManager(sm, fx)
    return db, sm, fx, ppm


def test_ensure_protection_active(env):
    db, sm, fx, ppm = env
    status = ppm.ensure_protection("BTCUSDT", "CID-1", 0.5, 90000.0)
    assert status == PROTECTION_ACTIVE
    assert fx.post_calls == 1
    assert "PROT-CID-1-BTCUSDT" in fx.orders


def test_pre_submit_lookup_avoids_duplicate_post(env):
    db, sm, fx, ppm = env
    cid = ppm.deterministic_protective_cid("CID-2", "BTCUSDT")
    fx.orders[cid] = {"orderId": "EX1", "clientOrderId": cid, "status": "NEW", "executedQty": "0"}
    status = ppm.ensure_protection("BTCUSDT", "CID-2", 0.5, 90000.0)
    assert status == PROTECTION_ACTIVE
    assert fx.post_calls == 0


def test_post_timeout_becomes_pending_no_retry(env):
    db, sm, fx, ppm = env
    fx.fail_post = True
    status = ppm.ensure_protection("BTCUSDT", "CID-3", 0.5, 90000.0)
    assert status == PROTECTION_PENDING
    assert fx.post_calls == 1


def test_reconcile_pending_to_active(env):
    db, sm, fx, ppm = env
    fx.fail_post = True
    ppm.ensure_protection("BTCUSDT", "CID-4", 0.5, 90000.0)
    fx.fail_post = False
    cid = ppm.deterministic_protective_cid("CID-4", "BTCUSDT")
    fx.orders[cid] = {"orderId": "EX4", "clientOrderId": cid, "status": "NEW", "executedQty": "0"}
    ppm.reconcile_pending_protections()
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT protection_status FROM position_protection WHERE symbol=?",
            ("BTCUSDT",),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == PROTECTION_ACTIVE


def test_protective_fill_reduces_position(env):
    db, sm, fx, ppm = env
    with get_db_transaction(db) as conn:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO positions (exchange_id, account_id, symbol, total_qty, locked_qty, avg_buy_price, updated_at) VALUES (?,?,?,?,0,?,?)",
            ("TOKOCRYPTO", "DEFAULT", "BTCUSDT", 1.0, 95000.0, now),
        )
    status = ppm.ensure_protection("BTCUSDT", "CID-5", 1.0, 90000.0)
    assert status == PROTECTION_ACTIVE
    cid = ppm.deterministic_protective_cid("CID-5", "BTCUSDT")
    fx.orders[cid] = {
        "orderId": "EX5",
        "clientOrderId": cid,
        "status": "FILLED",
        "executedQty": "1.0",
        "price": "90000",
    }
    ppm.reconcile_pending_protections()
    # force fill path via reconcile when status already ACTIVE by calling _apply
    ppm._apply_protective_fill("BTCUSDT", cid, fx.orders[cid])
    conn = db.get_connection()
    try:
        qty = float(conn.execute("SELECT total_qty FROM positions WHERE symbol=?", ("BTCUSDT",)).fetchone()[0])
        fills = conn.execute("SELECT COUNT(*) FROM fills WHERE client_order_id=?", (cid,)).fetchone()[0]
    finally:
        conn.close()
    assert qty == 0.0
    assert fills == 1


def test_max_algo_gate_blocks(env):
    db, sm, fx, ppm = env
    for i in range(5):
        with get_db_transaction(db) as conn:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO position_protection (exchange_id, account_id, symbol, protection_status, protected_qty, updated_at) VALUES (?,?,?,?,?,?)",
                ("TOKOCRYPTO", "DEFAULT", f"S{i}", PROTECTION_ACTIVE, 0.1, now),
            )
    assert ppm.max_algo_allows_new() is False
    status = ppm.ensure_protection("BTCUSDT", "CID-MAX", 0.5, 90000.0)
    assert status == PROTECTION_NONE
    assert fx.post_calls == 0


def test_idempotent_protective_fill(env):
    db, sm, fx, ppm = env
    with get_db_transaction(db) as conn:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO positions (exchange_id, account_id, symbol, total_qty, locked_qty, avg_buy_price, updated_at) VALUES (?,?,?,?,0,?,?)",
            ("TOKOCRYPTO", "DEFAULT", "ETHUSDT", 2.0, 3000.0, now),
        )
    ppm.ensure_protection("ETHUSDT", "CID-6", 2.0, 2800.0)
    cid = ppm.deterministic_protective_cid("CID-6", "ETHUSDT")
    od = {"orderId": "EX6", "clientOrderId": cid, "status": "FILLED", "executedQty": "2.0", "price": "2800"}
    ppm._apply_protective_fill("ETHUSDT", cid, od)
    ppm._apply_protective_fill("ETHUSDT", cid, od)
    conn = db.get_connection()
    try:
        qty = float(conn.execute("SELECT total_qty FROM positions WHERE symbol=?", ("ETHUSDT",)).fetchone()[0])
        fills = conn.execute("SELECT COUNT(*) FROM fills WHERE client_order_id=?", (cid,)).fetchone()[0]
    finally:
        conn.close()
    assert qty == 0.0
    assert fills == 1


def test_stop_price_persists_across_restart(env):
    db, sm, fx, ppm = env
    with get_db_transaction(db) as conn:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO orders (exchange_id, account_id, client_order_id, execution_id, signal_id, symbol, side, order_type,
               price, quantity, status, stop_price, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("TOKOCRYPTO", "DEFAULT", "ENTRY-1", "EX1", "SIG1", "BTCUSDT", "BUY", "LIMIT",
             95000.0, 0.5, "FILLED", 90000.0, now, now),
        )
        conn.execute(
            "INSERT INTO positions (exchange_id, account_id, symbol, total_qty, locked_qty, avg_buy_price, updated_at) VALUES (?,?,?,?,0,?,?)",
            ("TOKOCRYPTO", "DEFAULT", "BTCUSDT", 0.5, 95000.0, now),
        )
    ppm.ensure_protection("BTCUSDT", "ENTRY-1", 0.5, 90000.0)
    conn = db.get_connection()
    try:
        stop = conn.execute(
            "SELECT stop_price FROM position_protection WHERE symbol=?",
            ("BTCUSDT",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert float(stop) == 90000.0


def test_list_unprotected_positions(env):
    db, sm, fx, ppm = env
    with get_db_transaction(db) as conn:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO positions (exchange_id, account_id, symbol, total_qty, locked_qty, avg_buy_price, updated_at) VALUES (?,?,?,?,0,?,?)",
            ("TOKOCRYPTO", "DEFAULT", "SOLUSDT", 10.0, 100.0, now),
        )
    naked = ppm.list_unprotected_positions()
    assert any(p["symbol"] == "SOLUSDT" for p in naked)


def test_not_found_pending_no_blind_post(env):
    db, sm, fx, ppm = env
    fx.fail_post = True
    ppm.ensure_protection("BTCUSDT", "CID-7", 0.5, 90000.0)
    assert fx.post_calls == 1
    ppm.reconcile_pending_protections()
    assert fx.post_calls == 1  # no additional POST
