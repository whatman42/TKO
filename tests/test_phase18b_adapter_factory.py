"""Phase 1.8-B — ExchangeAdapter Protocol + Factory (no Binance trading)."""
import inspect
import os
import tempfile

import pytest

from tokocrypto_bot.exchange.adapter import (
    ExchangeAdapter,
    UnsupportedExchangeError,
    create_exchange_adapter,
)
from tokocrypto_bot.exchange.tokocrypto_client import TokocryptoDirectClient


def test_factory_tokocrypto_returns_direct_client():
    adapter = create_exchange_adapter("TOKOCRYPTO", api_key="k", api_secret="s")
    assert isinstance(adapter, TokocryptoDirectClient)
    assert getattr(adapter, "exchange_id") == "TOKOCRYPTO"
    assert getattr(adapter, "account_id") == "DEFAULT"


def test_factory_preserves_account_id():
    adapter = create_exchange_adapter(
        "tokocrypto", api_key="k", api_secret="s", account_id="ACC-1"
    )
    assert getattr(adapter, "exchange_id") == "TOKOCRYPTO"
    assert getattr(adapter, "account_id") == "ACC-1"


def test_factory_preserves_exchange_id_case_insensitive():
    adapter = create_exchange_adapter("ToKoCrYpTo", api_key="k", api_secret="s")
    assert getattr(adapter, "exchange_id") == "TOKOCRYPTO"


def test_factory_binance_fail_closed():
    with pytest.raises(UnsupportedExchangeError) as ei:
        create_exchange_adapter("BINANCE", api_key="k", api_secret="s")
    assert ei.value.exchange_id == "BINANCE"
    assert "NOT implemented" in str(ei.value)


def test_factory_unknown_exchange_fail_closed():
    with pytest.raises(UnsupportedExchangeError) as ei:
        create_exchange_adapter("KUCOIN", api_key="k", api_secret="s")
    assert ei.value.exchange_id == "KUCOIN"


def test_tokocrypto_adapter_satisfies_protocol():
    adapter = create_exchange_adapter("TOKOCRYPTO", "k", "s")
    assert isinstance(adapter, ExchangeAdapter)
    required = (
        "fetch_account_balances",
        "fetch_open_orders",
        "fetch_order_by_client_id",
        "fetch_recent_trades",
        "post_order_non_retry",
        "cancel_order_non_retry",
        "post_stop_loss_limit_non_retry",
    )
    for name in required:
        assert hasattr(adapter, name)
        assert callable(getattr(adapter, name))


def test_post_order_non_retry_is_single_shot_no_retry_loop():
    src = inspect.getsource(TokocryptoDirectClient.post_order_non_retry)
    assert src.count("self.session.post") == 1
    assert "while " not in src.lower()


def test_post_stop_loss_limit_non_retry_is_single_shot():
    src = inspect.getsource(TokocryptoDirectClient.post_stop_loss_limit_non_retry)
    assert src.count("self.session.post") == 1
    assert "while " not in src.lower()


def test_core_can_accept_injected_adapter_without_concrete_import():
    from tokocrypto_bot.persistence.database import DatabaseManager
    from tokocrypto_bot.persistence.migrations import run_migrations
    from tokocrypto_bot.persistence.state_manager import StateManager
    from tokocrypto_bot.execution.reconciliation import HardenedReconciliationEngine

    class MinimalAdapter:
        def fetch_account_balances(self):
            return {"USDT": {"free": 1.0, "locked": 0.0}}

        def fetch_open_orders(self, symbol=None):
            return []

        def fetch_order_by_client_id(self, symbol, client_order_id):
            return None

        def fetch_recent_trades(self, symbol, limit=50):
            return []

        def post_order_non_retry(self, **kwargs):
            raise RuntimeError("should not post in this test")

        def cancel_order_non_retry(self, **kwargs):
            return {}

        def post_stop_loss_limit_non_retry(self, **kwargs):
            raise RuntimeError("should not post in this test")

    with tempfile.TemporaryDirectory() as d:
        db = DatabaseManager(db_path=os.path.join(d, "a.db"))
        run_migrations(db)
        sm = StateManager(db)
        eng = HardenedReconciliationEngine(sm, MinimalAdapter())
        assert eng.exchange is not None


def test_adapter_identity_not_stripped():
    adapter = create_exchange_adapter(
        "TOKOCRYPTO", "k", "s", account_id="SUB-9"
    )
    assert getattr(adapter, "exchange_id", None) == "TOKOCRYPTO"
    assert getattr(adapter, "account_id", None) == "SUB-9"
