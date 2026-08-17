"""Trade fill sync — myTrades as fill source of truth."""
from tokocrypto_bot.ml.trade_fill_sync import normalize_tokocrypto_trade, TradeFillSync
from tokocrypto_bot.persistence.database import DatabaseManager
from tokocrypto_bot.persistence.migrations import run_migrations
from tokocrypto_bot.persistence.state_manager import StateManager


def test_normalize_tokocrypto_trade_fields():
    raw = {
        "id": 12345,
        "orderId": 999,
        "price": "100.5",
        "qty": "0.01",
        "quoteQty": "1.005",
        "commission": "0.00001",
        "commissionAsset": "BTC",
        "isBuyer": True,
        "time": 1700000000000,
    }
    f = normalize_tokocrypto_trade(raw, "BTCUSDT")
    assert f["trade_id"] == "12345"
    assert f["fill_id"] == "TRADE-12345"
    assert f["side"] == "BUY"
    assert abs(f["price"] - 100.5) < 1e-9
    assert abs(f["quantity"] - 0.01) < 1e-12
    assert f["exchange_order_id"] == "999"


def test_normalize_seller():
    f = normalize_tokocrypto_trade(
        {"id": 1, "price": 10, "qty": 2, "isBuyer": False, "time": 1},
        "ETHUSDT",
    )
    assert f["side"] == "SELL"


class _FakeExchange:
    def __init__(self, trades):
        self.trades = trades
    def fetch_recent_trades(self, symbol, limit=50):
        return self.trades


def test_sync_idempotent_and_journal(tmp_path):
    db = DatabaseManager(db_path=str(tmp_path / "t.db"))
    run_migrations(db)
    sm = StateManager(db)
    from tokocrypto_bot.persistence.ml_journal import MLJournal
    from tokocrypto_bot.strategy.features import FEATURE_VERSION, EXPECTED_FEATURE_COLUMNS
    j = MLJournal(db)
    feats = {c: 0.0 for c in EXPECTED_FEATURE_COLUMNS}
    j.record_prediction(
        symbol="BTCUSDT", feature_timestamp=1699999999000,
        feature_version=FEATURE_VERSION, model_version="t",
        features=feats, probability_up=0.7, probability_down=0.3,
        confidence=0.4, prediction_valid=True, prediction_status="OK",
        decision_action="BUY",
    )
    trades = [
        {"id": 10, "orderId": 1, "price": "100", "qty": "1", "isBuyer": True,
         "commission": "0.1", "commissionAsset": "USDT", "time": 1700000000000},
        {"id": 11, "orderId": 2, "price": "110", "qty": "1", "isBuyer": False,
         "commission": "0.1", "commissionAsset": "USDT", "time": 1700000100000},
    ]
    sync = TradeFillSync(sm, _FakeExchange(trades))
    n1 = sync.sync_symbol("BTCUSDT")
    n2 = sync.sync_symbol("BTCUSDT")
    assert n1 == 2
    assert n2 == 0
    conn = db.get_connection()
    fills = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    closed = conn.execute(
        "SELECT realized_pnl_usdt FROM ml_trade_outcomes WHERE outcome_status='CLOSED'"
    ).fetchall()
    conn.close()
    assert fills == 2
    assert any(r[0] is not None for r in closed)
