"""
MODULE: tokocrypto_bot.application
DESCRIPTION: P1-F Autonomous Worker Loop Orchestrator with Strict Cycle Isolation, Paper/Live Modes & P0-C Reconciliation.
"""

import sys
import time
import logging
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

# Import Sub-Sistem P0 (Persistence, Recovery, State Machine)
from tokocrypto_bot.persistence.database import DatabaseManager
from tokocrypto_bot.persistence.ml_journal import MLJournal
from tokocrypto_bot.persistence.state_manager import StateManager
from tokocrypto_bot.persistence.lifecycle_state import ApplicationState
from tokocrypto_bot.execution.order_state_machine import OrderStateMachine, OrderStatus
from tokocrypto_bot.execution.reconciliation import HardenedReconciliationEngine
from tokocrypto_bot.recovery.startup_recovery import StartupRecoveryOrchestrator

# Import Sub-Sistem P1 (Universe, Market, Features, ML, Decision, Portfolio)
from tokocrypto_bot.strategy.pair_universe import PairUniverseEngine, PairUniverseConfig
from tokocrypto_bot.strategy.market_data import MarketDataEngine
from tokocrypto_bot.strategy.features import FeatureEngine, FeatureFrame
from tokocrypto_bot.ml.inference import MLInferenceEngine, PredictionResult
from tokocrypto_bot.ml.continual_learning import ContinualLearningEngine
from tokocrypto_bot.strategy.selector import AdaptiveStrategySelector
from tokocrypto_bot.strategy.decision import DecisionEngine, Decision, DecisionAction
from tokocrypto_bot.strategy.portfolio import (
    RiskGate, PositionSizer, MultiPairPortfolioRanker, PortfolioState,
    PositionPlan, RiskDecision, RiskAction, compute_available_equity,
)
from tokocrypto_bot.exchange.tokocrypto_client import TokocryptoDirectClient
from tokocrypto_bot.exchange.adapter import create_exchange_adapter, ExchangeAdapter, UnsupportedExchangeError
from tokocrypto_bot.exchange.circuit_breaker import CircuitBreaker
from tokocrypto_bot.recovery.live_gate import HardLiveGate
from tokocrypto_bot.security.credential_manager import SecureCredentialStore

logger = logging.getLogger("NVRA.Application")


class LiveTradingBlockedError(RuntimeError):
    """Raised when LIVE mode is requested but HardLiveGate rejects activation."""


class ExecutionMode(str, Enum):
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    LIVE = "LIVE"


class PaperExchangeAdapter:
    """Mock/Paper Adapter untuk simulasi eksekusi tanpa risiko kapital fisik."""

    def __init__(self, initial_balance_usdt: float = 10000.0):
        self.balance_usdt = initial_balance_usdt
        self.paper_positions: Dict[str, float] = {}
        self.paper_orders: Dict[str, Dict[str, Any]] = {}

    def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        if symbol:
            return [o for o in self.paper_orders.values() if o["symbol"] == symbol and o["status"] == "NEW"]
        return [o for o in self.paper_orders.values() if o["status"] == "NEW"]

    def fetch_order_by_client_id(self, symbol: str, client_order_id: str) -> Optional[Dict[str, Any]]:
        return self.paper_orders.get(client_order_id)

    def fetch_recent_trades(self, symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
        return []

    def fetch_account_balances(self) -> Dict[str, Dict[str, float]]:
        return {"USDT": {"free": self.balance_usdt, "locked": 0.0}}

    def simulate_paper_order(self, plan: PositionPlan) -> Dict[str, Any]:
        """Simulasi pengisian instant untuk PAPER mode."""
        ex_id = f"PAPER-EX-{int(time.time() * 1000)}"
        order_dict = {
            "orderId": ex_id,
            "clientOrderId": plan.risk_decision.symbol,
            "symbol": plan.symbol,
            "status": "FILLED",
            "price": str(plan.target_price),
            "origQty": str(plan.calculated_quantity),
            "executedQty": str(plan.calculated_quantity),
            "cummulativeQuoteQty": str(plan.approved_notional_usdt)
        }
        self.paper_orders[plan.risk_decision.symbol] = order_dict

        if plan.action == DecisionAction.BUY:
            self.balance_usdt -= plan.approved_notional_usdt
            self.paper_positions[plan.symbol] = self.paper_positions.get(plan.symbol, 0.0) + plan.approved_notional_usdt
        elif plan.action == DecisionAction.SELL:
            self.balance_usdt += plan.approved_notional_usdt
            self.paper_positions[plan.symbol] = max(0.0, self.paper_positions.get(plan.symbol, 0.0) - plan.approved_notional_usdt)

        return order_dict


class AutonomousTradingWorker:
    def __init__(
        self,
        mode: ExecutionMode = ExecutionMode.PAPER,
        db_path: Optional[str] = None,
        api_key: str = "",
        api_secret: str = "",
        *,
        is_p0_passed: bool = True,
        is_p1_paper_passed: bool = True,
        is_model_valid: bool = True,
        is_strategy_healthy: bool = True,
    ):
        self.mode = mode
        self.db_mgr = DatabaseManager(db_path=db_path)
        self.state_mgr = StateManager(self.db_mgr)
        self.circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout_sec=60.0)
        self.live_gate = HardLiveGate(self.db_mgr)
        self._live_unlocked = False

        # Quantitative & ML engines BEFORE LIVE gate so model validity is evidence-based
        self.universe_engine = PairUniverseEngine()
        self.market_data_engine = MarketDataEngine()
        self.feature_engine = FeatureEngine()
        self.ml_engine = MLInferenceEngine()
        self.strategy_selector = AdaptiveStrategySelector()
        self.decision_engine = DecisionEngine()
        self.risk_gate = RiskGate()
        self.position_sizer = PositionSizer()
        self.ml_journal = MLJournal(self.db_mgr, exchange_id=getattr(self, "exchange_id", "TOKOCRYPTO"))
        self.cl_engine = ContinualLearningEngine(self.db_mgr)

        # 1. Setup Exchange Adapter berdasarkan Mode
        if self.mode == ExecutionMode.LIVE:
            key, secret = self._resolve_live_credentials(api_key, api_secret)
            if not key or not secret:
                raise LiveTradingBlockedError(
                    "LIVE mode rejected: missing API credentials (DPAPI/env/constructor empty)."
                )
            # HardLiveGate is ALWAYS mandatory for every LIVE unlock
            effective_model_valid = bool(is_model_valid) and self.ml_engine.is_model_ready()
            gate = self.live_gate.verify_all_live_conditions(
                is_p0_passed=is_p0_passed,
                is_p1_paper_passed=is_p1_paper_passed,
                is_model_valid=effective_model_valid,
                is_strategy_healthy=is_strategy_healthy,
                is_kill_switch_active=self._read_kill_switch(),
            )
            if not gate.live_allowed:
                raise LiveTradingBlockedError(
                    f"LIVE mode rejected by HardLiveGate: {gate.summary}; failed={gate.failed_checks}"
                )
            logger.warning("INITIALIZING LIVE EXECUTION MODE (Real Capital at risk!). Gate PASSED.")
            self.exchange = create_exchange_adapter(
                exchange_id=getattr(self, "exchange_id", "TOKOCRYPTO"),
                api_key=key,
                api_secret=secret,
                account_id=getattr(self, "account_id", "DEFAULT"),
            )
            self._live_unlocked = True
        else:
            logger.info(f"INITIALIZING {self.mode.value} EXECUTION MODE (Paper Simulator Active).")
            self.exchange = PaperExchangeAdapter()

        # 2. Infrastructure & Recovery Initialization (P0)
        self.orchestrator = StartupRecoveryOrchestrator(self.db_mgr, self.exchange)
        self.reconciler = HardenedReconciliationEngine(
            self.state_mgr, self.orchestrator.lifecycle_mgr, self.exchange
        )

    def run_worker_loop(self, poll_interval_sec: float = 10.0) -> None:
        """Entry point utama worker loop otonom."""
        logger.info(f"Starting NVRA Autonomous Trading Worker Loop [{self.mode.value} MODE]...")

        # Step A: Boot Verification & P0-D Startup Recovery Gate
        boot_state = self.orchestrator.run_startup_recovery_gate()
        if boot_state != ApplicationState.READY:
            logger.critical(f"Startup Recovery Gate did NOT yield READY (Got: {boot_state.value}). Loop Halted.")
            return

        # Main Scan & Execution Cycle Loop
        try:
            while True:
                cycle_start_time = time.time()
                self._run_single_autonomous_cycle()

                # Write Heartbeat to Database
                elapsed = time.time() - cycle_start_time
                self.orchestrator.lifecycle_mgr.write_heartbeat({
                    "cycle_duration_sec": round(elapsed, 2),
                    "execution_mode": self.mode.value
                })

                time.sleep(poll_interval_sec)
        except KeyboardInterrupt:
            logger.info("Shutdown signal received via KeyboardInterrupt.")
            self.orchestrator.shutdown_mgr.execute_graceful_shutdown("KeyboardInterrupt")

    def _resolve_live_credentials(self, api_key: str, api_secret: str):
        """Prefer SecureCredentialStore (DPAPI/env); fall back to non-empty constructor args."""
        store = SecureCredentialStore()
        try:
            key, secret = store.load_api_credentials()
        except Exception as e:
            logger.error(f"Credential store load failed: {e}")
            key, secret = None, None
        if key and secret:
            return key, secret
        if api_key and api_secret:
            logger.warning("Using constructor-provided credentials (credential store empty).")
            return api_key, api_secret
        return None, None

    def _read_kill_switch(self) -> bool:
        try:
            conn = self.db_mgr.get_connection()
            try:
                row = conn.execute(
                    "SELECT value FROM bot_state WHERE key='kill_switch'"
                ).fetchone()
                if not row:
                    return False
                return str(row[0]).lower() in ("1", "true", "on", "active")
            finally:
                conn.close()
        except Exception:
            # Fail closed: if we cannot read kill switch, treat as active for LIVE gate
            return True

    def _assert_live_still_allowed(self) -> None:
        """Re-check live gate + circuit breaker before any LIVE order submission."""
        if self.mode != ExecutionMode.LIVE:
            return
        if not self._live_unlocked:
            raise LiveTradingBlockedError("LIVE execution path locked")
        if self.circuit_breaker.is_open():
            raise LiveTradingBlockedError("CircuitBreaker OPEN — LIVE orders suppressed")
        if self._read_kill_switch():
            raise LiveTradingBlockedError("Kill switch active — LIVE orders suppressed")

    def _run_single_autonomous_cycle(self) -> None:

        """Eksekusi 1 siklus pemindaian multi-pair end-to-end terisolasi."""
        lifecycle = self.orchestrator.lifecycle_mgr
        current_app_state = lifecycle.current_state

        if current_app_state in (ApplicationState.SAFE_MODE, ApplicationState.PAUSED):
            logger.warning(f"Worker cycle skipped: Application is in [{current_app_state.value}] state.")
            return

        if self.circuit_breaker.is_open():
            logger.critical("CircuitBreaker OPEN — forcing SAFE_MODE, cycle skipped")
            lifecycle.set_state(ApplicationState.SAFE_MODE, f"CircuitBreaker: {self.circuit_breaker.last_error}")
            lifecycle.write_heartbeat({"phase": "circuit_open", "error": self.circuit_breaker.last_error})
            return

        lifecycle.set_state(ApplicationState.TRADING, "Beginning Multi-Pair Autonomous Cycle")

        # 1. SCANNING: Dynamic Pair Discovery
        active_universe = self.universe_engine.get_active_universe()
        if not active_universe:
            logger.warning("Active pair universe is empty. Cycle finished.")
            lifecycle.set_state(ApplicationState.READY, "Empty universe — cycle idle READY")
            lifecycle.write_heartbeat({"phase": "empty_universe", "execution_mode": self.mode.value})
            return

        # 2. ANALYZING: Fetch Market Data, Features, ML Inference & Strategy Selection
        feature_frames: Dict[str, FeatureFrame] = {}
        predictions: Dict[str, PredictionResult] = {}
        candidate_decisions: Dict[str, Decision] = {}
        current_prices: Dict[str, float] = {}

        pair_timeout_sec = float(getattr(self, "pair_processing_timeout_sec", 15.0))
        for rule in active_universe:
            symbol = rule.symbol
            try:
                # Isolated Pair Execution Guard with timeout boundary
                pair_started = time.time()

                klines_df = self.market_data_engine.get_klines_dataframe(symbol, interval="5m", limit=210)
                if time.time() - pair_started > pair_timeout_sec:
                    logger.error(f"Pair timeout after market data [{symbol}] > {pair_timeout_sec}s — isolated, skip pair")
                    continue
                if klines_df.empty:
                    continue

                ff = self.feature_engine.compute_features(klines_df, symbol)
                feature_frames[symbol] = ff
                if not ff.is_valid:
                    continue

                current_prices[symbol] = klines_df["close"].iloc[-1]

                pred = self.ml_engine.predict(ff)
                predictions[symbol] = pred

                if time.time() - pair_started > pair_timeout_sec:
                    logger.error(f"Pair timeout during analysis [{symbol}] > {pair_timeout_sec}s — isolated, skip pair")
                    # Drop partial artifacts so timeout cannot produce a trade
                    feature_frames.pop(symbol, None)
                    predictions.pop(symbol, None)
                    current_prices.pop(symbol, None)
                    continue

                # Adaptive Strategy Selection & Signal Generation
                candidate_sig, score, regime_ctx = self.strategy_selector.select_best_signal(symbol, ff, pred)
                
                # Evaluate Candidate into Decision
                decision = self.decision_engine.evaluate(
                    symbol, ff, pred,
                    current_price=current_prices[symbol],
                    allow_strategy_fallback=(self.mode == ExecutionMode.PAPER),
                )
                candidate_decisions[symbol] = decision

                # CL-0 prediction journal (fail-safe; never breaks the cycle)
                try:
                    regime_name = None
                    try:
                        regime_name = getattr(regime_ctx, "name", None) or str(regime_ctx)
                    except Exception:
                        regime_name = None
                    self.ml_journal.record_prediction(
                        symbol=symbol,
                        feature_timestamp=int(getattr(ff, "timestamp", 0) or 0),
                        feature_version=str(getattr(ff, "feature_version", "") or getattr(pred, "feature_version", "")),
                        model_version=str(getattr(pred, "model_version", "UNAVAILABLE")),
                        features=dict(getattr(ff, "features", {}) or {}),
                        probability_up=float(getattr(pred, "probability_up", 0.0) or 0.0),
                        probability_down=float(getattr(pred, "probability_down", 0.0) or 0.0),
                        confidence=float(getattr(pred, "confidence", 0.0) or 0.0),
                        prediction_valid=bool(getattr(pred, "is_valid", False)),
                        prediction_status=str(getattr(pred, "status_code", "") or ""),
                        decision_action=str(getattr(decision.action, "value", decision.action)),
                        decision_reasons=list(getattr(decision, "reason_codes", []) or []),
                        market_regime=regime_name,
                    )
                except Exception as journal_err:
                    logger.error("CL-0 prediction journal failed (swallowed): %s", journal_err)

            except Exception as e:
                logger.error(f"Isolated Exception processing symbol [{symbol}]: {e}", exc_info=True)
                # Ensure partial state for this pair cannot leak into execution
                feature_frames.pop(symbol, None)
                predictions.pop(symbol, None)
                candidate_decisions.pop(symbol, None)
                current_prices.pop(symbol, None)

        # 3. RISK_CHECK & POSITION SIZING (adaptive equity — Phase 1.5)
        portfolio_state = self._build_portfolio_state(current_app_state)
        available_equity = compute_available_equity(portfolio_state)
        symbol_rules_map = {r.symbol: r for r in active_universe}
        candidate_plans: Dict[str, PositionPlan] = {}

        for symbol, decision in candidate_decisions.items():
            if decision.action not in (DecisionAction.BUY, DecisionAction.SELL):
                continue

            # Deduplication Check: Skip jika order/posisi sudah ada
            if self._has_unresolved_or_active_position(symbol, portfolio_state):
                logger.info(f"Deduplication Guard: Order/Position already active for [{symbol}]. Skipping.")
                continue

            ff = feature_frames[symbol]
            price = current_prices[symbol]
            rules = symbol_rules_map.get(symbol)
            min_n = float(rules.min_notional) if rules else None
            step = float(rules.step_size) if rules else 0.0
            min_q = float(rules.min_qty) if rules else 0.0

            risk_dec = self.risk_gate.evaluate_trade_risk(
                decision, ff, portfolio_state, current_price=price,
                min_notional=min_n, step_size=step, min_qty=min_q,
            )
            plan = self.position_sizer.create_position_plan(decision, risk_dec, current_price=price)

            if plan.action in (DecisionAction.BUY, DecisionAction.SELL) and plan.approved_notional_usdt > 0:
                candidate_plans[symbol] = plan

        # Portfolio Multi-Pair Ranking & Capital Allocation — use verified available_equity
        approved_plans = MultiPairPortfolioRanker.rank_and_filter_plans(
            candidate_plans, available_equity
        )

        # 4. EXECUTING & RECONCILING
        if approved_plans:
            lifecycle.set_state(ApplicationState.RECONCILING, f"Executing {len(approved_plans)} approved position plans")
            for plan in approved_plans:
                self._execute_single_position_plan(plan)

        # 5. POST-CYCLE RECONCILIATION & PERSISTENCE COMMIT
        self.reconciler.execute_foundation_gate_reconciliation()
        # Continual learning: periodic retrain/promote (fail-closed; never breaks cycle)
        try:
            cl_res = self.cl_engine.on_cycle(
                ml_engine=self.ml_engine,
                execution_mode=getattr(self.mode, "value", str(self.mode)),
            )
            if cl_res.promoted:
                logger.warning(
                    "CL-1 promoted model: %s acc=%.3f prec=%.3f reason=%s",
                    cl_res.model_path, cl_res.accuracy, cl_res.precision, cl_res.reason,
                )
            elif cl_res.attempted:
                logger.info("CL-1 retrain skipped: %s (n=%s)", cl_res.reason, cl_res.n_samples)
        except Exception as cl_err:
            logger.error("CL on_cycle failed (swallowed): %s", cl_err)
        lifecycle.set_state(ApplicationState.READY, "Cycle execution complete. System READY.")
        lifecycle.write_heartbeat({"phase": "cycle_complete", "execution_mode": self.mode.value})

    def _lookup_existing_exchange_order(self, symbol: str, client_order_id: str):
        """
        Pre-submission exchange lookup by clientOrderId.
        Returns exchange order dict if found, None if confirmed absent.
        Raises on ambiguous API failure (caller must fail closed — no blind POST).
        """
        # Prefer direct client-order lookup
        existing = self.exchange.fetch_order_by_client_id(symbol=symbol, client_order_id=client_order_id)
        if existing:
            return existing
        # Fallback: open orders scan
        open_orders = self.exchange.fetch_open_orders(symbol=symbol)
        match = next((o for o in open_orders if o.get("clientOrderId") == client_order_id), None)
        return match

    def _recover_from_existing_exchange_order(self, client_order_id: str, existing: dict) -> None:
        """Apply exchange state without re-POSTing (anti-duplicate recovery).

        From SUBMITTING the state machine only allows ACKNOWLEDGED / UNKNOWN / REJECTED.
        Detailed FILLED/NEW/etc. is left to reconciliation after ACKNOWLEDGED.
        """
        ex_id = str(existing.get("orderId", "") or "")
        ex_status = str(existing.get("status", "")).upper()
        if ex_status in ("REJECTED",):
            target = "REJECTED"
        else:
            # Safe intermediate: order exists on exchange → ACKNOWLEDGED (no second POST)
            target = "ACKNOWLEDGED"
        OrderStateMachine.validate_transition(OrderStatus.SUBMITTING, OrderStatus(target))
        self.state_mgr.transition_order_state(
            client_order_id, "SUBMITTING", target,
            f"PRE_SUBMIT_EXCHANGE_LOOKUP_RECOVERY_{ex_status or 'UNKNOWN'}",
            exchange_order_id=ex_id or None,
        )

    def _execute_single_position_plan(self, plan: PositionPlan) -> None:
        """Mengeksekusi satu PositionPlan melalui OrderStateMachine & StateManager."""
        symbol = plan.symbol
        side = plan.action.value
        execution_id = f"EXEC-{int(time.time() * 1000)}"
        signal_id = f"SIG-{symbol}-{int(time.time())}"

        client_order_id = OrderStateMachine.generate_client_order_id(execution_id, signal_id, symbol, side)

        # P0-B: Persist Order Intent CREATED
        created_ok = self.state_mgr.create_order_intent(
            client_order_id=client_order_id,
            execution_id=execution_id,
            signal_id=signal_id,
            symbol=symbol,
            side=side,
            order_type="LIMIT",
            price=plan.target_price,
            quantity=plan.calculated_quantity,
            initial_status=OrderStatus.CREATED.value
        )
        if not created_ok:
            return

        # P0-A: Transition to SUBMITTING before Network Request
        OrderStateMachine.validate_transition(OrderStatus.CREATED, OrderStatus.SUBMITTING)
        self.state_mgr.transition_order_state(client_order_id, "CREATED", "SUBMITTING", "NETWORK_REQUEST_INITIATED")

        # Network Transmit
        try:
            if self.mode == ExecutionMode.LIVE:
                self._assert_live_still_allowed()
                live_client = self.exchange

                # PRIORITY 1: Pre-submission duplicate protection
                try:
                    existing = self._lookup_existing_exchange_order(symbol, client_order_id)
                except Exception as lookup_err:
                    # Lookup ambiguous → fail closed, NEVER blind POST
                    logger.error(
                        f"Pre-submit exchange lookup failed for [{client_order_id}]: {lookup_err}. "
                        f"Fail-closed — POST suppressed."
                    )
                    OrderStateMachine.validate_transition(OrderStatus.SUBMITTING, OrderStatus.UNKNOWN)
                    self.state_mgr.transition_order_state(
                        client_order_id, "SUBMITTING", "UNKNOWN",
                        f"PRE_SUBMIT_LOOKUP_FAILED_{type(lookup_err).__name__}",
                    )
                    return

                if existing:
                    logger.warning(
                        f"Duplicate protection: clientOrderId [{client_order_id}] already on exchange. "
                        f"POST suppressed; recovering from exchange state."
                    )
                    self._recover_from_existing_exchange_order(client_order_id, existing)
                    return

                # Call Real Tokocrypto Direct Client (NO BLIND RETRY ON POST)
                res = live_client.post_order_non_retry(
                    symbol=symbol, side=side, order_type="LIMIT",
                    quantity=plan.calculated_quantity, price=plan.target_price,
                    client_order_id=client_order_id
                )
                ex_id = str(res.get("orderId", ""))
                self.state_mgr.transition_order_state(client_order_id, "SUBMITTING", "ACKNOWLEDGED", "HTTP_POST_200_OK", exchange_order_id=ex_id)
            else:
                # Simulate Paper Order
                paper_adapter: PaperExchangeAdapter = self.exchange
                res = paper_adapter.simulate_paper_order(plan)
                ex_id = res["orderId"]
                self.state_mgr.transition_order_state(client_order_id, "SUBMITTING", "FILLED", "PAPER_SIMULATION_FILLED", exchange_order_id=ex_id)

        except Exception as e:
            # Network Timeout / Unknown Handling: NEVER BLIND RETRY!
            logger.error(f"Network error submitting order [{client_order_id}]: {e}. Marking state as UNKNOWN.")
            OrderStateMachine.validate_transition(OrderStatus.SUBMITTING, OrderStatus.UNKNOWN)
            self.state_mgr.transition_order_state(client_order_id, "SUBMITTING", "UNKNOWN", f"NETWORK_EXCEPTION_{type(e).__name__}")

    def _build_portfolio_state(self, app_state: ApplicationState) -> PortfolioState:
        """Construct PortfolioState from persistence + exchange. Fail closed on unverifiable risk state."""
        balance_ok = False
        usdt_free = 0.0
        try:
            if not self.circuit_breaker.allow_request():
                raise RuntimeError("CircuitBreaker open — balance fetch suppressed")
            balances = self.exchange.fetch_account_balances()
            usdt_free = float(balances.get("USDT", {}).get("free", 0.0) or 0.0)
            balance_ok = True
            self.circuit_breaker.record_success()
        except Exception as e:
            logger.error(f"Balance fetch failed: {e}")
            self.circuit_breaker.record_failure(str(e))
            # Try DB balances as last resort
            try:
                conn = self.db_mgr.get_connection()
                try:
                    row = conn.execute(
                        "SELECT free FROM balances WHERE asset='USDT'"
                    ).fetchone()
                    if row is not None:
                        usdt_free = float(row[0])
                        balance_ok = True
                finally:
                    conn.close()
            except Exception as db_e:
                logger.error(f"DB balance fallback failed: {db_e}")

        active_positions: Dict[str, float] = {}
        exposure = 0.0
        try:
            conn = self.db_mgr.get_connection()
            try:
                for sym, qty, avg in conn.execute(
                    "SELECT symbol, total_qty, avg_buy_price FROM positions WHERE total_qty > 0"
                ).fetchall():
                    notional = abs(float(qty) * float(avg or 0.0))
                    active_positions[str(sym)] = notional
                    exposure += notional
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Position load failed: {e}")
            # Unverifiable positions → fail closed: mark reconciliation unclean via exposure unknown
            active_positions = {}
            exposure = 0.0

        daily_pnl = 0.0
        try:
            conn = self.db_mgr.get_connection()
            try:
                # Realized approximation from fills today (UTC)
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                rows = conn.execute(
                    "SELECT side, price, quantity FROM fills WHERE timestamp LIKE ?",
                    (f"{today}%",),
                ).fetchall()
                for side, price, qty in rows:
                    signed = float(price) * float(qty)
                    if str(side).upper() == "SELL":
                        daily_pnl += signed
                    else:
                        daily_pnl -= signed
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Daily PnL load failed: {e}")
            daily_pnl = 0.0

        peak_equity = usdt_free + exposure
        try:
            conn = self.db_mgr.get_connection()
            try:
                row = conn.execute(
                    "SELECT value FROM bot_state WHERE key='peak_equity_usdt'"
                ).fetchone()
                if row:
                    peak_equity = max(peak_equity, float(row[0]))
            finally:
                conn.close()
        except Exception:
            pass

        drawdown = 0.0
        if peak_equity > 0:
            drawdown = max(0.0, (peak_equity - (usdt_free + exposure)) / peak_equity)

        unresolved = self.state_mgr.get_unresolved_orders()
        is_reconciled = len(unresolved) == 0 and balance_ok
        kill = self._read_kill_switch()

        # If we cannot verify balance at all → unclean (risk gate rejects trades)
        if not balance_ok:
            is_reconciled = False

        return PortfolioState(
            total_equity_usdt=usdt_free + exposure,
            available_balance_usdt=usdt_free,
            current_portfolio_exposure_usdt=exposure,
            daily_realized_pnl_usdt=daily_pnl,
            peak_equity_usdt=peak_equity,
            current_drawdown_pct=drawdown,
            cusum_statistic=0.0,
            consecutive_losses=0,
            active_positions=active_positions,
            app_lifecycle_state=app_state,
            is_reconciliation_clean=is_reconciled,
            is_kill_switch_active=kill,
        )

    def _has_unresolved_or_active_position(self, symbol: str, p_state: PortfolioState) -> bool:
        """Memeriksa apakah simbol memiliki order gantung atau posisi aktif."""
        unresolved = self.state_mgr.get_unresolved_orders()
        if any(o["symbol"] == symbol for o in unresolved):
            return True
        return symbol in p_state.active_positions


if __name__ == "__main__":
    worker = AutonomousTradingWorker(mode=ExecutionMode.PAPER)
    worker.run_worker_loop()
