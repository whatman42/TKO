"""MODULE: tests.test_p2_production_validation"""
import os, pytest, tempfile, pandas as pd
from tokocrypto_bot.persistence.database import DatabaseManager
from tokocrypto_bot.persistence.migrations import run_migrations
from tokocrypto_bot.quant.performance_evaluator import PerformanceEvaluator
from tokocrypto_bot.strategy.strategy_health import StrategyHealthMonitor, StrategyHealthState
from tokocrypto_bot.recovery.live_gate import HardLiveGate
def test_net_expectancy_deducts_fees_and_slippage():
    r=PerformanceEvaluator(0.001,0.0005).evaluate_trades(pd.DataFrame([{"pnl_usdt":10,"return_pct":0.01,"notional_usdt":1000},{"pnl_usdt":-5,"return_pct":-0.005,"notional_usdt":1000}]))
    assert r.total_trades==2 and r.net_expectancy < r.gross_edge
def test_strategy_health_auto_degradation():
    mon=StrategyHealthMonitor(1.0,0.05); r=PerformanceEvaluator().evaluate_trades(pd.DataFrame([{"pnl_usdt":-10,"return_pct":-0.02,"notional_usdt":500}]*15))
    assert mon.evaluate_strategy_health("ScalpingStrategy",r).state==StrategyHealthState.DEGRADED
def test_hard_live_gate_blocks_if_credentials_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        db=DatabaseManager(db_path=os.path.join(tmpdir,"live.db")); run_migrations(db)
        res=HardLiveGate(db).verify_all_live_conditions(True,True,True,True)
        assert res.live_allowed is False and "CREDENTIAL_MISSING_FAIL" in res.failed_checks
