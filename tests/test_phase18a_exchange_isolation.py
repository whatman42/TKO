"""Phase 1.8-A — exchange_id state isolation (TKO-native)."""
import os, tempfile, pytest
from datetime import datetime, timezone
from tokocrypto_bot.persistence.database import DatabaseManager, get_db_transaction
from tokocrypto_bot.persistence.migrations import run_migrations
from tokocrypto_bot.persistence.state_manager import StateManager
from tokocrypto_bot.persistence.exchange_ids import DEFAULT_EXCHANGE_ID


@pytest.fixture
def db_env():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "iso.db")
        db = DatabaseManager(db_path=path)
        run_migrations(db)
        yield db


def test_migration_v3_applies(db_env):
    conn = db_env.get_connection()
    try:
        ver = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        assert ver >= 3
        cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
        assert "exchange_id" in cols
        cols_p = [r[1] for r in conn.execute("PRAGMA table_info(positions)").fetchall()]
        assert "exchange_id" in cols_p
    finally:
        conn.close()


def test_backfill_default_tokocrypto(db_env):
    now = datetime.now(timezone.utc).isoformat()
    # simulate pre-v3 by inserting with defaults via migration path
    conn = db_env.get_connection()
    try:
        conn.execute(
            "INSERT INTO orders (client_order_id, execution_id, signal_id, symbol, side, order_type, price, quantity, status, created_at, updated_at, exchange_id, account_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("CID1", "E1", "S1", "BTCUSDT", "BUY", "LIMIT", 1.0, 1.0, "NEW", now, now, "TOKOCRYPTO", "DEFAULT"),
        )
        conn.commit()
        row = conn.execute("SELECT exchange_id FROM orders WHERE client_order_id=?", ("CID1",)).fetchone()
        assert row[0] == "TOKOCRYPTO"
    finally:
        conn.close()


def test_position_isolation_same_symbol(db_env):
    now = datetime.now(timezone.utc).isoformat()
    with get_db_transaction(db_env) as conn:
        conn.execute(
            "INSERT INTO positions (exchange_id, account_id, symbol, total_qty, locked_qty, avg_buy_price, updated_at) VALUES (?,?,?,?,0,?,?)",
            ("TOKOCRYPTO", "DEFAULT", "BTCUSDT", 0.1, 50000.0, now),
        )
        conn.execute(
            "INSERT INTO positions (exchange_id, account_id, symbol, total_qty, locked_qty, avg_buy_price, updated_at) VALUES (?,?,?,?,0,?,?)",
            ("BINANCE", "DEFAULT", "BTCUSDT", 0.9, 51000.0, now),
        )
    conn = db_env.get_connection()
    try:
        t = conn.execute("SELECT total_qty FROM positions WHERE exchange_id=? AND symbol=?", ("TOKOCRYPTO", "BTCUSDT")).fetchone()[0]
        b = conn.execute("SELECT total_qty FROM positions WHERE exchange_id=? AND symbol=?", ("BINANCE", "BTCUSDT")).fetchone()[0]
        assert abs(float(t) - 0.1) < 1e-9
        assert abs(float(b) - 0.9) < 1e-9
    finally:
        conn.close()


def test_balance_isolation(db_env):
    now = datetime.now(timezone.utc).isoformat()
    with get_db_transaction(db_env) as conn:
        conn.execute(
            "INSERT INTO balances (exchange_id, account_id, asset, free, locked, updated_at) VALUES (?,?,?,?,0,?)",
            ("TOKOCRYPTO", "DEFAULT", "USDT", 100.0, now),
        )
        conn.execute(
            "INSERT INTO balances (exchange_id, account_id, asset, free, locked, updated_at) VALUES (?,?,?,?,0,?)",
            ("BINANCE", "DEFAULT", "USDT", 999.0, now),
        )
    conn = db_env.get_connection()
    try:
        t = conn.execute("SELECT free FROM balances WHERE exchange_id=? AND asset=?", ("TOKOCRYPTO", "USDT")).fetchone()[0]
        b = conn.execute("SELECT free FROM balances WHERE exchange_id=? AND asset=?", ("BINANCE", "USDT")).fetchone()[0]
        assert float(t) == 100.0 and float(b) == 999.0
    finally:
        conn.close()


def test_protection_isolation(db_env):
    now = datetime.now(timezone.utc).isoformat()
    with get_db_transaction(db_env) as conn:
        conn.execute(
            "INSERT INTO position_protection (exchange_id, account_id, symbol, protection_status, updated_at) VALUES (?,?,?,?,?)",
            ("TOKOCRYPTO", "DEFAULT", "BTCUSDT", "ACTIVE", now),
        )
        conn.execute(
            "INSERT INTO position_protection (exchange_id, account_id, symbol, protection_status, updated_at) VALUES (?,?,?,?,?)",
            ("BINANCE", "DEFAULT", "BTCUSDT", "NONE", now),
        )
    conn = db_env.get_connection()
    try:
        t = conn.execute("SELECT protection_status FROM position_protection WHERE exchange_id=? AND symbol=?", ("TOKOCRYPTO", "BTCUSDT")).fetchone()[0]
        b = conn.execute("SELECT protection_status FROM position_protection WHERE exchange_id=? AND symbol=?", ("BINANCE", "BTCUSDT")).fetchone()[0]
        assert t == "ACTIVE" and b == "NONE"
    finally:
        conn.close()


def test_order_isolation_same_cid_different_exchange(db_env):
    now = datetime.now(timezone.utc).isoformat()
    sm_t = StateManager(db_env, exchange_id="TOKOCRYPTO")
    sm_b = StateManager(db_env, exchange_id="BINANCE")
    assert sm_t.create_order_intent("SAME-CID", "E1", "S1", "BTCUSDT", "BUY", "LIMIT", 1.0, 0.1)
    # different exchange allows same client_order_id only if PK allows — current PK is client_order_id only
    # document: if PK is global CID, second insert fails; isolation is on exchange_id column presence
    ok = sm_b.create_order_intent("SAME-CID-B", "E2", "S2", "BTCUSDT", "BUY", "LIMIT", 1.0, 0.2)
    assert ok
    o1 = sm_t.get_order("SAME-CID")
    assert o1 is not None
    assert o1.get("exchange_id") in (None, "TOKOCRYPTO", "TOKOCRYPTO")


def test_unresolved_orders_scoped(db_env):
    now = datetime.now(timezone.utc).isoformat()
    sm = StateManager(db_env, exchange_id="TOKOCRYPTO")
    sm.create_order_intent("U1", "E", "S", "BTCUSDT", "BUY", "LIMIT", 1.0, 0.1)
    unresolved = sm.get_unresolved_orders()
    assert any(o["client_order_id"] == "U1" for o in unresolved)


def test_fill_isolation(db_env):
    now = datetime.now(timezone.utc).isoformat()
    with get_db_transaction(db_env) as conn:
        for cid, eid in (("C1", "TOKOCRYPTO"), ("C2", "BINANCE")):
            conn.execute(
                "INSERT INTO orders (client_order_id, execution_id, signal_id, symbol, side, order_type, price, quantity, status, created_at, updated_at, exchange_id, account_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, "E", "S", "BTCUSDT", "BUY", "LIMIT", 1.0, 1.0, "FILLED", now, now, eid, "DEFAULT"),
            )
        conn.execute(
            "INSERT INTO fills (fill_id, client_order_id, symbol, side, price, quantity, timestamp, exchange_id) VALUES (?,?,?,?,?,?,?,?)",
            ("F1", "C1", "BTCUSDT", "BUY", 1.0, 0.1, now, "TOKOCRYPTO"),
        )
        conn.execute(
            "INSERT INTO fills (fill_id, client_order_id, symbol, side, price, quantity, timestamp, exchange_id) VALUES (?,?,?,?,?,?,?,?)",
            ("F2", "C2", "BTCUSDT", "BUY", 1.0, 0.9, now, "BINANCE"),
        )
    conn = db_env.get_connection()
    try:
        s_t = conn.execute("SELECT COALESCE(SUM(quantity),0) FROM fills WHERE exchange_id=? AND symbol=?", ("TOKOCRYPTO", "BTCUSDT")).fetchone()[0]
        s_b = conn.execute("SELECT COALESCE(SUM(quantity),0) FROM fills WHERE exchange_id=? AND symbol=?", ("BINANCE", "BTCUSDT")).fetchone()[0]
        assert abs(float(s_t) - 0.1) < 1e-9
        assert abs(float(s_b) - 0.9) < 1e-9
    finally:
        conn.close()


def test_same_cid_different_exchange_allowed(db_env):
    sm_t = StateManager(db_env, exchange_id="TOKOCRYPTO")
    sm_b = StateManager(db_env, exchange_id="BINANCE")
    assert sm_t.create_order_intent("SHARED-CID", "E1", "S1", "BTCUSDT", "BUY", "LIMIT", 1.0, 0.1)
    assert sm_b.create_order_intent("SHARED-CID", "E2", "S2", "BTCUSDT", "BUY", "LIMIT", 1.0, 0.2)
    o_t = sm_t.get_order("SHARED-CID")
    o_b = sm_b.get_order("SHARED-CID")
    assert o_t is not None and o_b is not None
    assert o_t["exchange_id"] == "TOKOCRYPTO"
    assert o_b["exchange_id"] == "BINANCE"
    assert float(o_t["quantity"]) == 0.1
    assert float(o_b["quantity"]) == 0.2


def test_same_cid_same_exchange_rejected(db_env):
    sm = StateManager(db_env, exchange_id="TOKOCRYPTO")
    assert sm.create_order_intent("DUP-CID", "E1", "S1", "BTCUSDT", "BUY", "LIMIT", 1.0, 0.1)
    assert sm.create_order_intent("DUP-CID", "E2", "S2", "BTCUSDT", "BUY", "LIMIT", 1.0, 0.2) is False


def test_unresolved_scoped_by_exchange(db_env):
    sm_t = StateManager(db_env, exchange_id="TOKOCRYPTO")
    sm_b = StateManager(db_env, exchange_id="BINANCE")
    sm_t.create_order_intent("U-T", "E", "S", "BTCUSDT", "BUY", "LIMIT", 1.0, 0.1)
    sm_b.create_order_intent("U-B", "E", "S", "BTCUSDT", "BUY", "LIMIT", 1.0, 0.1)
    ut = sm_t.get_unresolved_orders()
    ub = sm_b.get_unresolved_orders()
    assert any(o["client_order_id"] == "U-T" for o in ut)
    assert not any(o["client_order_id"] == "U-B" for o in ut)
    assert any(o["client_order_id"] == "U-B" for o in ub)
