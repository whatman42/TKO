"""PHASE 1.5 — Adaptive capital & position sizing edge cases."""
import os, tempfile, pytest
from tokocrypto_bot.persistence.lifecycle_state import ApplicationState
from tokocrypto_bot.strategy.portfolio import PortfolioState, PortfolioRiskConfig, RiskGate, RiskAction, compute_available_equity, compute_executable_notional
from tokocrypto_bot.strategy.decision import Decision, DecisionAction
from tokocrypto_bot.strategy.features import FeatureFrame, FEATURE_VERSION
from tokocrypto_bot.application import AutonomousTradingWorker, ExecutionMode, LiveTradingBlockedError
def _pstate(available, total=None, clean=True, kill=False, exposure=0.0, positions=None, lifecycle=ApplicationState.READY):
    total = total if total is not None else available
    return PortfolioState(total, available, exposure, 0.0, max(total,available), 0.0, 0.0, 0, positions or {}, lifecycle, clean, kill)
def _ff(symbol="BTCUSDT"):
    return FeatureFrame(1, symbol,FEATURE_VERSION,{"ATR":100.0,"RSI14":50.0,"ema_ratio":1.0,"MACD_HIST":0.0},True)
def _dec(symbol="BTCUSDT", action=DecisionAction.BUY):
    return Decision(symbol,1,action,0.8,0.6,0.05,49000.0,52000.0,["TEST"],"t","t")
def _gate(**kw): return RiskGate(PortfolioRiskConfig(**kw) if kw else PortfolioRiskConfig())
def test_equity_47000_min_46000_eligible():
    rd=_gate(min_notional_usdt=46000.0).evaluate_trade_risk(_dec(),_ff(),_pstate(47000.0),50000.0,min_notional=46000.0)
    assert rd.action in (RiskAction.ALLOW,RiskAction.REDUCE) and rd.approved_notional>=46000.0-1e-6
def test_equity_47000_min_47000_full_equity_edge():
    rd=_gate(min_notional_usdt=47000.0,taker_fee_pct=0.0,slippage_pct=0.0).evaluate_trade_risk(_dec(),_ff(),_pstate(47000.0),50000.0,min_notional=47000.0)
    assert rd.action in (RiskAction.ALLOW,RiskAction.REDUCE) and abs(rd.approved_notional-47000.0)<1.0
def test_equity_47000_min_48000_no_trade():
    rd=_gate(min_notional_usdt=48000.0).evaluate_trade_risk(_dec(),_ff(),_pstate(47000.0),50000.0,min_notional=48000.0)
    assert rd.action==RiskAction.REJECT and "EXECUTABLE_BELOW_EXCHANGE_MINIMUM" in rd.reason_codes
def test_fee_makes_47000_infeasible():
    rd=_gate(min_notional_usdt=47000.0,taker_fee_pct=0.001,slippage_pct=0.0005).evaluate_trade_risk(_dec(),_ff(),_pstate(47000.0),50000.0,min_notional=47000.0)
    assert rd.action==RiskAction.REJECT
def test_slippage_buffer_infeasible():
    rd=_gate(min_notional_usdt=46000.0,taker_fee_pct=0.0,slippage_pct=0.05).evaluate_trade_risk(_dec(),_ff(),_pstate(47000.0),50000.0,min_notional=46000.0)
    assert rd.action==RiskAction.REJECT
def test_precision_invalid_no_trade():
    rd=_gate(min_notional_usdt=10.0).evaluate_trade_risk(_dec(),_ff(),_pstate(1000.0),50000.0,min_notional=10.0,step_size=10.0,min_qty=10.0)
    assert rd.action==RiskAction.REJECT
def test_large_equity_not_all_in():
    rd=_gate(min_notional_usdt=10.0,base_risk_per_trade_pct=0.01).evaluate_trade_risk(_dec(),_ff(),_pstate(10_000_000.0),50000.0,min_notional=10.0)
    assert rd.action in (RiskAction.ALLOW,RiskAction.REDUCE) and rd.approved_notional < 10_000_000.0*0.2
def test_equity_change_deposit_withdrawal():
    gate=_gate(min_notional_usdt=10.0,base_risk_per_trade_pct=0.01)
    a1=gate.evaluate_trade_risk(_dec(),_ff(),_pstate(100_000.0),50000.0,min_notional=10.0).approved_notional
    a2=gate.evaluate_trade_risk(_dec(),_ff(),_pstate(47_000.0),50000.0,min_notional=10.0).approved_notional
    a3=gate.evaluate_trade_risk(_dec(),_ff(),_pstate(500_000.0),50000.0,min_notional=10.0).approved_notional
    assert a3>a2 and compute_available_equity(_pstate(47_000.0))==47_000.0
def test_unverified_equity_no_trade():
    rd=_gate().evaluate_trade_risk(_dec(),_ff(),_pstate(1000.0,clean=False),50000.0)
    assert rd.action==RiskAction.REJECT
def test_riskgate_kill_switch_no_trade():
    rd=_gate().evaluate_trade_risk(_dec(),_ff(),_pstate(1000.0,kill=True),50000.0)
    assert rd.action==RiskAction.REJECT and "CRITICAL_KILL_SWITCH_ACTIVE" in rd.reason_codes
def test_boundary_46001_46000():
    rd=_gate(taker_fee_pct=0.0,slippage_pct=0.0).evaluate_trade_risk(_dec(),_ff(),_pstate(46001.0),50000.0,min_notional=46000.0)
    assert rd.action in (RiskAction.ALLOW,RiskAction.REDUCE)
def test_boundary_46000_46000():
    rd=_gate(taker_fee_pct=0.0,slippage_pct=0.0).evaluate_trade_risk(_dec(),_ff(),_pstate(46000.0),50000.0,min_notional=46000.0)
    assert rd.action in (RiskAction.ALLOW,RiskAction.REDUCE)
def test_boundary_45999_46000_no_trade():
    rd=_gate(taker_fee_pct=0.0,slippage_pct=0.0).evaluate_trade_risk(_dec(),_ff(),_pstate(45999.0),50000.0,min_notional=46000.0)
    assert rd.action==RiskAction.REJECT
def test_executable_formula():
    assert abs(compute_executable_notional(47000.0,0.001,0.0005)-47000.0/1.0015)<1e-6
def test_circuit_breaker_cycle_no_trade():
    with tempfile.TemporaryDirectory() as tmp:
        w=AutonomousTradingWorker(mode=ExecutionMode.PAPER,db_path=os.path.join(tmp,"t.db"))
        for _ in range(5): w.circuit_breaker.record_failure("429")
        w._run_single_autonomous_cycle(); assert w.orchestrator.lifecycle_mgr.current_state==ApplicationState.SAFE_MODE
def test_stale_data_no_frames():
    from tokocrypto_bot.strategy.market_data import MarketDataEngine, DataSource
    import time; engine=MarketDataEngine(max_staleness_seconds=60.0); now=int(time.time()*1000)
    assert engine._parse_raw_klines("BTCUSDT",[[now-120000,"1","1","1","1","1",now-60000]],DataSource.TOKOCRYPTO)==[]
def test_live_gate_fail_still_blocks():
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(LiveTradingBlockedError):
            AutonomousTradingWorker(mode=ExecutionMode.LIVE,db_path=os.path.join(tmp,"t.db"),api_key="",api_secret="")
def test_partial_fill_state_updates_equity():
    s1,s2=_pstate(100_000.0),_pstate(90_000.0,exposure=10_000.0,positions={"BTCUSDT":10_000.0})
    assert compute_available_equity(s1)==100_000.0 and compute_available_equity(s2)==90_000.0
