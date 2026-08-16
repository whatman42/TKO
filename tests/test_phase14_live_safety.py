"""PHASE 1.4 — LIVE safety, portfolio, stale data, credentials, circuit breaker."""
import os, time, pytest, tempfile
from datetime import datetime, timezone
from unittest.mock import patch
from tokocrypto_bot.persistence.database import DatabaseManager
from tokocrypto_bot.persistence.migrations import run_migrations
from tokocrypto_bot.persistence.lifecycle_state import ApplicationState
from tokocrypto_bot.application import AutonomousTradingWorker, ExecutionMode, LiveTradingBlockedError, PaperExchangeAdapter
from tokocrypto_bot.recovery.live_gate import HardLiveGate
from tokocrypto_bot.exchange.circuit_breaker import CircuitBreaker
from tokocrypto_bot.exchange.tokocrypto_client import RateLimitError
from tokocrypto_bot.strategy.market_data import MarketDataEngine, DataSource, OHLCVFrame
@pytest.fixture
def db_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        db=DatabaseManager(db_path=os.path.join(tmpdir,"p14.db")); run_migrations(db); yield db, os.path.join(tmpdir,"p14.db")
def test_live_missing_credential_blocked(db_env):
    _,db_path=db_env
    with patch.object(HardLiveGate,"verify_all_live_conditions",return_value=type("R",(),{"live_allowed":False,"failed_checks":["CREDENTIAL_MISSING_FAIL"],"passed_checks":[],"summary":"BLOCKED"})()):
        with pytest.raises(LiveTradingBlockedError):
            AutonomousTradingWorker(mode=ExecutionMode.LIVE, db_path=db_path, api_key="", api_secret="")
def test_live_hard_gate_failure_blocked(db_env):
    _,db_path=db_env
    with patch("tokocrypto_bot.application.SecureCredentialStore") as Store:
        Store.return_value.load_api_credentials.return_value=("k","s")
        with patch.object(HardLiveGate,"verify_all_live_conditions",return_value=type("R",(),{"live_allowed":False,"failed_checks":["P0_RELIABILITY_FAIL"],"passed_checks":[],"summary":"BLOCKED"})()):
            with pytest.raises(LiveTradingBlockedError):
                AutonomousTradingWorker(mode=ExecutionMode.LIVE, db_path=db_path, api_key="k", api_secret="s")
def test_live_all_gates_pass_enters_path(db_env):
    _,db_path=db_env
    with patch("tokocrypto_bot.application.SecureCredentialStore") as Store:
        Store.return_value.load_api_credentials.return_value=("k","s")
        with patch.object(HardLiveGate,"verify_all_live_conditions",return_value=type("R",(),{"live_allowed":True,"failed_checks":[],"passed_checks":["ALL"],"summary":"UNLOCKED"})()):
            w=AutonomousTradingWorker(mode=ExecutionMode.LIVE, db_path=db_path, api_key="k", api_secret="s")
            assert w._live_unlocked is True and w.mode==ExecutionMode.LIVE
def test_paper_mode_unaffected(db_env):
    _,db_path=db_env; w=AutonomousTradingWorker(mode=ExecutionMode.PAPER, db_path=db_path)
    assert isinstance(w.exchange, PaperExchangeAdapter) and w._live_unlocked is False
def test_portfolio_loads_positions_and_kill_switch(db_env):
    db,db_path=db_env; conn=db.get_connection()
    try:
        conn.execute("INSERT INTO positions(symbol,total_qty,avg_buy_price,updated_at) VALUES('BTCUSDT',0.5,60000.0,?)",(datetime.now(timezone.utc).isoformat(),))
        conn.execute("INSERT OR REPLACE INTO bot_state(key,value) VALUES('kill_switch','0')"); conn.commit()
    finally: conn.close()
    w=AutonomousTradingWorker(mode=ExecutionMode.PAPER, db_path=db_path)
    st=w._build_portfolio_state(ApplicationState.READY)
    assert st.active_positions.get("BTCUSDT")==30000.0 and st.is_kill_switch_active is False
def test_portfolio_balance_failure_marks_unclean(db_env):
    _,db_path=db_env; w=AutonomousTradingWorker(mode=ExecutionMode.PAPER, db_path=db_path)
    w.exchange.fetch_account_balances=lambda: (_ for _ in ()).throw(RuntimeError("down"))
    st=w._build_portfolio_state(ApplicationState.READY)
    assert st.is_reconciliation_clean is False
def test_stale_klines_discarded():
    engine=MarketDataEngine(max_staleness_seconds=60.0); now_ms=int(time.time()*1000)
    out=engine._parse_raw_klines("BTCUSDT",[[now_ms-120000,"1","1","1","1","1",now_ms-60000]], DataSource.TOKOCRYPTO)
    assert out.empty
def test_fresh_klines_kept():
    engine=MarketDataEngine(max_staleness_seconds=300.0); now_ms=int(time.time()*1000)
    out=engine._parse_raw_klines("BTCUSDT",[[now_ms-1000,"1","1","1","1","1",now_ms+59000]], DataSource.TOKOCRYPTO)
    assert len(out)==1
def test_live_empty_constructor_blocked_without_store(db_env):
    _,db_path=db_env
    with patch("tokocrypto_bot.application.SecureCredentialStore") as Store:
        Store.return_value.load_api_credentials.return_value=(None,None)
        with pytest.raises(LiveTradingBlockedError):
            AutonomousTradingWorker(mode=ExecutionMode.LIVE, db_path=db_path, api_key="", api_secret="")
def test_circuit_breaker_opens_and_blocks_cycle(db_env):
    _,db_path=db_env; w=AutonomousTradingWorker(mode=ExecutionMode.PAPER, db_path=db_path)
    for i in range(5): w.circuit_breaker.record_failure(f"err-{i}")
    assert w.circuit_breaker.is_open(); w._run_single_autonomous_cycle()
    assert w.orchestrator.lifecycle_mgr.current_state==ApplicationState.SAFE_MODE
def test_post_still_single_shot_on_rate_limit():
    from tokocrypto_bot.exchange.tokocrypto_client import TokocryptoDirectClient
    client=TokocryptoDirectClient("k","s")
    class FakeResp:
        status_code=429
        def raise_for_status(self): raise AssertionError("no")
    with patch.object(client.session,"post",return_value=FakeResp()):
        with pytest.raises(RateLimitError):
            client.post_order_non_retry(symbol="BTCUSDT",side="BUY",order_type="LIMIT",quantity=0.01,price=100.0,client_order_id="CID-1")


def test_live_always_invokes_hard_live_gate(db_env):
    """LIVE must call HardLiveGate; force_live_gate bypass removed from production API."""
    _, db_path = db_env
    with patch("tokocrypto_bot.application.SecureCredentialStore") as Store:
        Store.return_value.load_api_credentials.return_value = ("k", "s")
        with patch.object(HardLiveGate, "verify_all_live_conditions", return_value=type(
            "R", (), {"live_allowed": True, "failed_checks": [], "passed_checks": ["ALL"], "summary": "UNLOCKED"}
        )()) as gate_mock:
            with patch("tokocrypto_bot.application.create_exchange_adapter") as factory:
                factory.return_value = type("A", (), {
                    "fetch_account_balances": lambda self: {"USDT": {"free": 1.0, "locked": 0.0}},
                    "fetch_open_orders": lambda self, symbol=None: [],
                    "fetch_order_by_client_id": lambda self, symbol, client_order_id: None,
                    "fetch_recent_trades": lambda self, symbol, limit=50: [],
                })()
                w = AutonomousTradingWorker(mode=ExecutionMode.LIVE, db_path=db_path, api_key="k", api_secret="s")
                assert gate_mock.called
                assert factory.called
                assert w._live_unlocked is True


def test_force_live_gate_kwarg_rejected(db_env):
    """Passing force_live_gate must raise TypeError (parameter removed)."""
    _, db_path = db_env
    with pytest.raises(TypeError):
        AutonomousTradingWorker(
            mode=ExecutionMode.LIVE, db_path=db_path, api_key="k", api_secret="s", force_live_gate=False
        )


def test_live_gate_failure_blocks_before_factory(db_env):
    """HardLiveGate fail → LiveTradingBlockedError and factory never called."""
    _, db_path = db_env
    with patch("tokocrypto_bot.application.SecureCredentialStore") as Store:
        Store.return_value.load_api_credentials.return_value = ("k", "s")
        with patch.object(HardLiveGate, "verify_all_live_conditions", return_value=type(
            "R", (), {"live_allowed": False, "failed_checks": ["P0_RELIABILITY_FAIL"], "passed_checks": [], "summary": "BLOCKED"}
        )()):
            with patch("tokocrypto_bot.application.create_exchange_adapter") as factory:
                with pytest.raises(LiveTradingBlockedError):
                    AutonomousTradingWorker(mode=ExecutionMode.LIVE, db_path=db_path, api_key="k", api_secret="s")
                assert not factory.called
