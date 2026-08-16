"""Phase 1.7 — protective exit + reconciliation integration (TKO-native)."""
import os, tempfile, pytest
from datetime import datetime, timezone
from tokocrypto_bot.persistence.database import DatabaseManager, get_db_transaction
from tokocrypto_bot.persistence.migrations import run_migrations
from tokocrypto_bot.persistence.state_manager import StateManager
from tokocrypto_bot.execution.position_protection import (
    PositionProtectionManager, PROTECTION_ACTIVE, PROTECTION_PENDING, MAX_ALGO_ORDERS
)
from tokocrypto_bot.execution.reconciliation import HardenedReconciliationEngine, ReconciliationResult, ReconciliationDecision
from tokocrypto_bot.execution.order_state_machine import OrderStatus


class MockEx:
    def __init__(self):
        self.orders = {}
        self.posts = 0
        self.balances = {"USDT": {"free": 10000.0, "locked": 0.0}}
    def fetch_order_by_client_id(self, symbol, client_order_id):
        return self.orders.get(client_order_id)
    def post_stop_loss_limit_non_retry(self, **kwargs):
        self.posts += 1
        cid = kwargs["client_order_id"]
        od = {"orderId": f"SL{self.posts}", "clientOrderId": cid, "symbol": kwargs["symbol"],
              "status": "NEW", "executedQty": "0", "origQty": str(kwargs["quantity"])}
        self.orders[cid] = od
        return od
    def fetch_account_balances(self):
        return self.balances
    def cancel_order_non_retry(self, **kwargs):
        return {"status": "CANCELED"}


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as d:
        db = DatabaseManager(db_path=os.path.join(d, "p17.db"))
        run_migrations(db)
        sm = StateManager(db)
        ex = MockEx()
        ppm = PositionProtectionManager(sm, ex)
        recon = HardenedReconciliationEngine(sm, ex)
        yield ppm, sm, ex, db, recon


def test_ensure_protection_active(env):
    ppm, sm, ex, db, recon = env
    assert ppm.ensure_protection("BTCUSDT", "PARENT1", 0.5, 58000.0) == PROTECTION_ACTIVE
    assert ex.posts == 1


def test_no_blind_retry_on_pending_not_found(env):
    ppm, sm, ex, db, recon = env
    ppm.ensure_protection("BTCUSDT", "PARENT2", 0.5, 58000.0)
    prot_cid = ppm.deterministic_protective_cid("PARENT2", "BTCUSDT")
    ppm._upsert_row("BTCUSDT", "PARENT2", prot_cid, 0.5, 58000.0, PROTECTION_PENDING)
    ex.orders.clear()
    before = ex.posts
    ppm.reconcile_pending_protections()
    assert ex.posts == before


def test_max_algo_fail_closed(env):
    ppm, sm, ex, db, recon = env
    for i in range(MAX_ALGO_ORDERS):
        ppm.ensure_protection(f"SYM{i}", f"P{i}", 0.1, 100.0)
    assert ppm.ensure_protection("EXTRA", "PX", 0.1, 100.0) == "NONE"


def test_naked_position_listed(env):
    ppm, sm, ex, db, recon = env
    now = datetime.now(timezone.utc).isoformat()
    with get_db_transaction(db) as conn:
        conn.execute(
            "INSERT INTO positions (exchange_id, account_id, symbol, total_qty, locked_qty, avg_buy_price, updated_at) VALUES (?,?,?,?,0,?,?)",
            ("TOKOCRYPTO", "DEFAULT", "BTCUSDT", 1.0, 50000.0, now),
        )
    assert any(n["symbol"] == "BTCUSDT" for n in ppm.list_unprotected_positions())


def test_deterministic_cid(env):
    ppm, sm, ex, db, recon = env
    assert ppm.deterministic_protective_cid("P", "BTCUSDT") == ppm.deterministic_protective_cid("P", "BTCUSDT")


def test_buy_fill_attaches_protection_via_reconciliation(env):
    ppm, sm, ex, db, recon = env
    assert sm.create_order_intent("BUY1", "E1", "S1", "BTCUSDT", "BUY", "LIMIT", 60000.0, 1.0)
    with get_db_transaction(db) as conn:
        conn.execute("UPDATE orders SET stop_price=? WHERE client_order_id=? AND exchange_id=?", (58000.0, "BUY1", "TOKOCRYPTO"))
    order = sm.get_order("BUY1")
    res = ReconciliationResult(
        client_order_id="BUY1", decision=ReconciliationDecision.FOUND_FILLED,
        target_order_status=OrderStatus.FILLED, exchange_order_id="99",
        executed_qty=1.0, remaining_qty=0.0, avg_price=60000.0, reason="filled",
    )
    delta = recon._record_fill_idempotent(order, res)
    assert delta > 0
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT protection_status, protected_qty, stop_price FROM position_protection WHERE exchange_id=? AND symbol=?",
            ("TOKOCRYPTO", "BTCUSDT"),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == PROTECTION_ACTIVE
    assert abs(float(row[1]) - 1.0) < 1e-9
    assert ex.posts >= 1


def test_partial_fill_protection_qty_follows_executed(env):
    ppm, sm, ex, db, recon = env
    assert sm.create_order_intent("BUY2", "E1", "S1", "BTCUSDT", "BUY", "LIMIT", 60000.0, 1.0)
    with get_db_transaction(db) as conn:
        conn.execute("UPDATE orders SET stop_price=? WHERE client_order_id=? AND exchange_id=?", (58000.0, "BUY2", "TOKOCRYPTO"))
    order = sm.get_order("BUY2")
    res = ReconciliationResult(
        client_order_id="BUY2", decision=ReconciliationDecision.FOUND_PARTIALLY_FILLED,
        target_order_status=OrderStatus.PARTIALLY_FILLED, exchange_order_id="X1",
        executed_qty=0.4, remaining_qty=0.6, avg_price=60000.0, reason="partial",
    )
    recon._record_fill_idempotent(order, res)
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT protected_qty FROM position_protection WHERE exchange_id=? AND symbol=?",
            ("TOKOCRYPTO", "BTCUSDT"),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and abs(float(row[0]) - 0.4) < 1e-9


def test_missing_stop_price_fail_closed_no_attach(env):
    ppm, sm, ex, db, recon = env
    assert sm.create_order_intent("BUY3", "E1", "S1", "ETHUSDT", "BUY", "LIMIT", 3000.0, 1.0)
    order = sm.get_order("BUY3")
    res = ReconciliationResult(
        client_order_id="BUY3", decision=ReconciliationDecision.FOUND_FILLED,
        target_order_status=OrderStatus.FILLED, exchange_order_id="X2",
        executed_qty=1.0, remaining_qty=0.0, avg_price=3000.0, reason="filled",
    )
    before = ex.posts
    recon._record_fill_idempotent(order, res)
    assert ex.posts == before
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM position_protection WHERE exchange_id=? AND symbol=?",
            ("TOKOCRYPTO", "ETHUSDT"),
        ).fetchone()
    finally:
        conn.close()
    assert row is None


def test_same_qty_replay_no_duplicate_fill(env):
    ppm, sm, ex, db, recon = env
    assert sm.create_order_intent("BUY4", "E1", "S1", "BTCUSDT", "BUY", "LIMIT", 60000.0, 0.5)
    with get_db_transaction(db) as conn:
        conn.execute("UPDATE orders SET stop_price=? WHERE client_order_id=? AND exchange_id=?", (58000.0, "BUY4", "TOKOCRYPTO"))
    order = sm.get_order("BUY4")
    res = ReconciliationResult(
        client_order_id="BUY4", decision=ReconciliationDecision.FOUND_FILLED,
        target_order_status=OrderStatus.FILLED, exchange_order_id="X4",
        executed_qty=0.5, remaining_qty=0.0, avg_price=60000.0, reason="f",
    )
    assert recon._record_fill_idempotent(order, res) > 0
    assert recon._record_fill_idempotent(order, res) == 0


def test_protective_filled_reduces_position(env):
    ppm, sm, ex, db, recon = env
    now = datetime.now(timezone.utc).isoformat()
    with get_db_transaction(db) as conn:
        conn.execute(
            "INSERT INTO positions (exchange_id, account_id, symbol, total_qty, locked_qty, avg_buy_price, updated_at) VALUES (?,?,?,?,0,?,?)",
            ("TOKOCRYPTO", "DEFAULT", "BTCUSDT", 1.0, 50000.0, now),
        )
        # parent order for FK on fills
        conn.execute(
            """INSERT INTO orders (exchange_id, account_id, client_order_id, execution_id, signal_id, symbol, side, order_type,
               price, quantity, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("TOKOCRYPTO", "DEFAULT", "PROT-P-BTCUSDT", "E", "S", "BTCUSDT", "SELL", "STOP_LOSS_LIMIT",
             48000.0, 1.0, "NEW", now, now),
        )
    prot_cid = "PROT-P-BTCUSDT"
    ppm._upsert_row("BTCUSDT", "P", prot_cid, 1.0, 48000.0, PROTECTION_ACTIVE)
    od = {"orderId": "9", "clientOrderId": prot_cid, "status": "FILLED", "executedQty": "1.0", "price": "48000"}
    ppm._apply_protective_fill("BTCUSDT", prot_cid, od)
    ppm._apply_protective_fill("BTCUSDT", prot_cid, od)
    conn = db.get_connection()
    try:
        qty = float(conn.execute("SELECT total_qty FROM positions WHERE exchange_id=? AND symbol=?", ("TOKOCRYPTO", "BTCUSDT")).fetchone()[0])
        fills = conn.execute("SELECT COUNT(*) FROM fills WHERE fill_id LIKE ?", (f"PFILL-{prot_cid}%",)).fetchone()[0]
    finally:
        conn.close()
    assert qty == 0.0
    assert fills == 1
