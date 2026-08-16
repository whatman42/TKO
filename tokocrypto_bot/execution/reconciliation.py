"""MODULE: tokocrypto_bot.execution.reconciliation - Hardened Reconciliation Engine with Partial-Fill Precision"""
import json, logging
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Protocol
from tokocrypto_bot.execution.order_state_machine import OrderStateMachine, OrderStatus
from tokocrypto_bot.persistence.state_manager import StateManager
from tokocrypto_bot.persistence.database import get_db_transaction
from tokocrypto_bot.persistence.lifecycle_state import LifecycleManager, ApplicationState
logger = logging.getLogger("NVRA.ReconciliationEngine")
RECONCILE_API_MAX_RETRIES = 3
RECONCILE_API_BACKOFF_BASE_SEC = 0.5
UNKNOWN_MAX_AGE_DAYS = 7
class ReconciliationDecision(str, Enum):
    FOUND_NEW="FOUND_NEW"; FOUND_PARTIALLY_FILLED="FOUND_PARTIALLY_FILLED"; FOUND_FILLED="FOUND_FILLED"
    FOUND_CANCELED="FOUND_CANCELED"; FOUND_REJECTED="FOUND_REJECTED"; FOUND_EXPIRED="FOUND_EXPIRED"
    NOT_FOUND="NOT_FOUND"; API_ERROR="API_ERROR"
class SystemRecoveryStatus(str, Enum):
    RECOVERY_COMPLETE="RECOVERY_COMPLETE"; SAFE_MODE="SAFE_MODE"; IN_PROGRESS="IN_PROGRESS"
@dataclass(frozen=True)
class ReconciliationResult:
    client_order_id: str; decision: ReconciliationDecision; target_order_status: OrderStatus
    exchange_order_id: Optional[str]=None; executed_qty: float=0.0; remaining_qty: float=0.0
    avg_price: float=0.0; fee: float=0.0; fee_asset: Optional[str]=None; reason: str=""
    raw_response: Optional[Dict[str,Any]]=field(default=None, repr=False)
class ExchangeAdapterProtocol(Protocol):
    def fetch_open_orders(self, symbol: Optional[str]=None) -> List[Dict[str,Any]]: ...
    def fetch_order_by_client_id(self, symbol: str, client_order_id: str) -> Optional[Dict[str,Any]]: ...
    def fetch_recent_trades(self, symbol: str, limit: int=50) -> List[Dict[str,Any]]: ...
    def fetch_account_balances(self) -> Dict[str,Dict[str,float]]: ...
    def cancel_order_non_retry(self, symbol: str, client_order_id: Optional[str]=None, exchange_order_id: Optional[str]=None) -> Dict[str,Any]: ...
class HardenedReconciliationEngine:
    def __init__(self, state_manager, lifecycle_manager_or_exchange=None, exchange_adapter=None):
        self.state_mgr = state_manager
        if exchange_adapter is None and lifecycle_manager_or_exchange is not None and not isinstance(lifecycle_manager_or_exchange, LifecycleManager):
            self.exchange = lifecycle_manager_or_exchange
            self.lifecycle_mgr = LifecycleManager(state_manager.db)
        else:
            self.lifecycle_mgr = lifecycle_manager_or_exchange or LifecycleManager(state_manager.db)
            self.exchange = exchange_adapter
        if self.exchange is None: raise TypeError("exchange_adapter is required")
    def execute_foundation_gate_reconciliation(self) -> bool:
        self.lifecycle_mgr.set_state(ApplicationState.RECONCILING, "Starting Foundation Gate Reconciliation")
        for order in self.state_mgr.get_unresolved_orders():
            res = self.reconcile_single_order(order)
            if not self._apply_reconciliation_result(order, res):
                self.lifecycle_mgr.set_state(ApplicationState.SAFE_MODE, "Unresolved Order")
                return False
        if not self._reconcile_account_and_positions():
            self.lifecycle_mgr.set_state(ApplicationState.SAFE_MODE, "Balance discrepancy")
            return False
        self.lifecycle_mgr.set_state(ApplicationState.READY, "Foundation Gate PASSED")
        return True
    def _order_age_days(self, order):
        raw = order.get("created_at") or order.get("updated_at")
        if not raw: return 0.0
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z","+00:00"))
            if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc)-dt).total_seconds()/86400.0)
        except Exception: return 0.0
    def _exchange_lookup_once(self, cid, order, symbol):
        open_orders = self.exchange.fetch_open_orders(symbol=symbol)
        match_open = next((o for o in open_orders if o.get("clientOrderId")==cid), None)
        if match_open: return self._parse_exchange_response(cid, order, match_open, "OPEN_ORDERS_API")
        ex_order = self.exchange.fetch_order_by_client_id(symbol=symbol, client_order_id=cid)
        if ex_order: return self._parse_exchange_response(cid, order, ex_order, "ORDER_DETAIL_API")
        trades = self.exchange.fetch_recent_trades(symbol=symbol, limit=50)
        matching = [t for t in trades if t.get("clientOrderId")==cid]
        if matching: return self._aggregate_trades(cid, order, matching)
        return ReconciliationResult(client_order_id=cid, decision=ReconciliationDecision.NOT_FOUND, target_order_status=OrderStatus.UNKNOWN, reason="NOT_FOUND preserve UNKNOWN")
    def reconcile_single_order(self, order):
        import time as _time
        cid, symbol, curr_status = order["client_order_id"], order["symbol"], order["status"]
        if curr_status == OrderStatus.SUBMITTING.value:
            self.state_mgr.transition_order_state(cid, OrderStatus.SUBMITTING.value, OrderStatus.UNKNOWN.value, "UPCAST_SUBMITTING_ON_RECOVERY")
            curr_status = OrderStatus.UNKNOWN.value
        if curr_status == OrderStatus.UNKNOWN.value:
            self.state_mgr.transition_order_state(cid, OrderStatus.UNKNOWN.value, OrderStatus.RECONCILING.value, "RECONCILING_START")
        last_err = None
        for attempt in range(1, RECONCILE_API_MAX_RETRIES+1):
            try:
                result = self._exchange_lookup_once(cid, order, symbol)
                if result.decision == ReconciliationDecision.NOT_FOUND and self._order_age_days(order) >= UNKNOWN_MAX_AGE_DAYS:
                    return ReconciliationResult(client_order_id=cid, decision=ReconciliationDecision.FOUND_EXPIRED, target_order_status=OrderStatus.EXPIRED, reason="Aged UNKNOWN expired")
                return result
            except Exception as e:
                last_err = e
                if attempt < RECONCILE_API_MAX_RETRIES:
                    _time.sleep(RECONCILE_API_BACKOFF_BASE_SEC * (2 ** (attempt-1)))
        return ReconciliationResult(client_order_id=cid, decision=ReconciliationDecision.API_ERROR, target_order_status=OrderStatus.UNKNOWN, reason=f"API Error: {last_err}")
    def _parse_exchange_response(self, cid, local_order, ex_order, source):
        ex_status = ex_order.get("status","").upper()
        ex_id = str(ex_order.get("orderId",""))
        orig_qty = float(ex_order.get("origQty", local_order["quantity"]))
        executed_qty = float(ex_order.get("executedQty", 0.0))
        remaining_qty = max(0.0, orig_qty - executed_qty)
        price = float(ex_order.get("price", 0.0))
        cum_quote = float(ex_order.get("cummulativeQuoteQty", 0.0))
        avg_price = cum_quote/executed_qty if executed_qty>0 else price
        if ex_status=="PARTIALLY_FILLED" or (0<executed_qty<orig_qty and ex_status not in ("CANCELED","EXPIRED")):
            decision, target = ReconciliationDecision.FOUND_PARTIALLY_FILLED, OrderStatus.PARTIALLY_FILLED
        elif ex_status=="FILLED" or (executed_qty>=orig_qty and orig_qty>0):
            decision, target = ReconciliationDecision.FOUND_FILLED, OrderStatus.FILLED
        elif ex_status=="CANCELED": decision, target = ReconciliationDecision.FOUND_CANCELED, OrderStatus.CANCELED
        elif ex_status=="REJECTED": decision, target = ReconciliationDecision.FOUND_REJECTED, OrderStatus.REJECTED
        elif ex_status=="EXPIRED": decision, target = ReconciliationDecision.FOUND_EXPIRED, OrderStatus.EXPIRED
        else: decision, target = ReconciliationDecision.FOUND_NEW, OrderStatus.NEW
        return ReconciliationResult(client_order_id=cid, decision=decision, target_order_status=target, exchange_order_id=ex_id, executed_qty=executed_qty, remaining_qty=remaining_qty, avg_price=avg_price, reason=f"Matched via {source}", raw_response=ex_order)
    def _sum_recorded_fill_qty(self, client_order_id):
        conn = self.state_mgr.db.get_connection()
        try:
            row = conn.execute("SELECT COALESCE(SUM(quantity),0) FROM fills WHERE client_order_id=?", (client_order_id,)).fetchone()
            return float(row[0] if row else 0.0)
        finally: conn.close()
    def _record_fill_idempotent(self, local_order, result):
        cid = result.client_order_id
        exchange_cum = float(result.executed_qty or 0.0)
        if exchange_cum <= 0: return 0.0
        recorded = self._sum_recorded_fill_qty(cid)
        delta = exchange_cum - recorded
        if delta <= 1e-12: return 0.0
        fill_id = f"FILL-{result.exchange_order_id or cid}-CUM-{exchange_cum:.10f}"
        now_str = datetime.now(timezone.utc).isoformat()
        px = float(result.avg_price or local_order.get("price") or 0.0)
        with get_db_transaction(self.state_mgr.db) as conn:
            conn.execute("INSERT INTO fills (fill_id, client_order_id, exchange_order_id, symbol, side, price, quantity, timestamp) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(fill_id) DO NOTHING;", (fill_id, cid, result.exchange_order_id, local_order["symbol"], local_order["side"], px, delta, now_str))
        actual_delta = max(0.0, self._sum_recorded_fill_qty(cid) - recorded)
        if actual_delta > 0:
            self._upsert_position_from_fill(local_order["symbol"], str(local_order["side"]).upper(), actual_delta, px)
        return actual_delta
    def _upsert_position_from_fill(self, symbol, side, delta_qty, price):
        if delta_qty <= 0: return
        now_str = datetime.now(timezone.utc).isoformat()
        signed = delta_qty if side=="BUY" else -delta_qty
        with get_db_transaction(self.state_mgr.db) as conn:
            row = conn.execute("SELECT total_qty, avg_buy_price FROM positions WHERE symbol=?", (symbol,)).fetchone()
            if row is None:
                if signed <= 0: return
                conn.execute("INSERT INTO positions (symbol, total_qty, locked_qty, avg_buy_price, updated_at) VALUES (?,?,0,?,?)", (symbol, signed, price, now_str))
                return
            prev_qty, prev_avg = float(row[0] or 0.0), float(row[1] or 0.0)
            new_qty = prev_qty + signed
            if new_qty <= 1e-12:
                conn.execute("UPDATE positions SET total_qty=0, locked_qty=0, avg_buy_price=0, updated_at=? WHERE symbol=?", (now_str, symbol)); return
            new_avg = ((prev_qty*prev_avg)+(signed*price))/new_qty if (signed>0 and prev_qty>0) else (price if signed>0 else prev_avg)
            conn.execute("UPDATE positions SET total_qty=?, avg_buy_price=?, updated_at=? WHERE symbol=?", (new_qty, new_avg, now_str, symbol))
    def _cancel_remainder_safe(self, local_order, result):
        if result.remaining_qty is None or result.remaining_qty <= 1e-12: return True
        cancel_fn = getattr(self.exchange, "cancel_order_non_retry", None)
        if cancel_fn is None: return False
        try:
            cancel_fn(symbol=local_order["symbol"], client_order_id=result.client_order_id, exchange_order_id=result.exchange_order_id)
            return True
        except Exception as e:
            logger.error(f"Remainder cancel failed: {e}"); return False
    def _apply_reconciliation_result(self, local_order, result):
        cid = result.client_order_id
        if result.decision in (ReconciliationDecision.NOT_FOUND, ReconciliationDecision.API_ERROR): return False
        fresh = self.state_mgr.get_order(cid)
        curr_status = (fresh or local_order)["status"]
        if curr_status == OrderStatus.UNKNOWN.value:
            self.state_mgr.transition_order_state(cid, OrderStatus.UNKNOWN.value, OrderStatus.RECONCILING.value, "RECONCILING_START")
            curr_status = OrderStatus.RECONCILING.value
        elif curr_status == OrderStatus.SUBMITTING.value:
            self.state_mgr.transition_order_state(cid, OrderStatus.SUBMITTING.value, OrderStatus.UNKNOWN.value, "UPCAST_SUBMITTING")
            self.state_mgr.transition_order_state(cid, OrderStatus.UNKNOWN.value, OrderStatus.RECONCILING.value, "RECONCILING_START")
            curr_status = OrderStatus.RECONCILING.value
        try:
            target = result.target_order_status
            if OrderStatus(curr_status)==target and target==OrderStatus.PARTIALLY_FILLED:
                if result.executed_qty>0: self._record_fill_idempotent(local_order, result)
                if result.remaining_qty and result.remaining_qty>1e-12 and self._cancel_remainder_safe(local_order, result):
                    return self.state_mgr.transition_order_state(cid, OrderStatus.PARTIALLY_FILLED.value, OrderStatus.CANCELED.value, "REMAINDER_CANCELED_AFTER_PARTIAL", details={"executed_qty":result.executed_qty}, exchange_order_id=result.exchange_order_id)
                return True
            OrderStateMachine.validate_transition(OrderStatus(curr_status), target)
            if result.executed_qty>0: self._record_fill_idempotent(local_order, result)
            final_status = target
            if target==OrderStatus.PARTIALLY_FILLED and result.remaining_qty and result.remaining_qty>1e-12:
                final_status = OrderStatus.CANCELED if self._cancel_remainder_safe(local_order, result) else OrderStatus.PARTIALLY_FILLED
            return self.state_mgr.transition_order_state(cid, curr_status, final_status.value, f"RECONCILED_{result.decision.value}", details={"executed_qty":result.executed_qty,"remaining_qty":result.remaining_qty}, exchange_order_id=result.exchange_order_id)
        except Exception as e:
            logger.error(f"Transition error: {e}"); return False
    def _reconcile_account_and_positions(self):
        try:
            balances = self.exchange.fetch_account_balances()
            now_str = datetime.now(timezone.utc).isoformat()
            with get_db_transaction(self.state_mgr.db) as conn:
                for asset, data in balances.items():
                    conn.execute("INSERT INTO balances (asset, free, locked, updated_at) VALUES (?,?,?,?) ON CONFLICT(asset) DO UPDATE SET free=excluded.free, locked=excluded.locked, updated_at=excluded.updated_at", (asset, data["free"], data["locked"], now_str))
            return True
        except Exception as e:
            logger.error(f"Balance recon failed: {e}"); return False
    def _aggregate_trades(self, cid, local_order, trades):
        executed_qty, quote_sum, exchange_order_id = 0.0, 0.0, None
        for t in trades:
            qty = float(t.get("qty") or t.get("quantity") or 0.0)
            price = float(t.get("price") or 0.0)
            executed_qty += qty; quote_sum += qty*price
            if exchange_order_id is None: exchange_order_id = str(t.get("orderId") or t.get("id") or "") or None
        orig_qty = float(local_order.get("quantity") or 0.0)
        avg_price = (quote_sum/executed_qty) if executed_qty>0 else 0.0
        remaining = max(0.0, orig_qty-executed_qty)
        if executed_qty>=orig_qty and orig_qty>0: decision, target = ReconciliationDecision.FOUND_FILLED, OrderStatus.FILLED
        elif executed_qty>0: decision, target = ReconciliationDecision.FOUND_PARTIALLY_FILLED, OrderStatus.PARTIALLY_FILLED
        else: decision, target = ReconciliationDecision.NOT_FOUND, OrderStatus.UNKNOWN
        return ReconciliationResult(client_order_id=cid, decision=decision, target_order_status=target, exchange_order_id=exchange_order_id, executed_qty=executed_qty, remaining_qty=remaining, avg_price=avg_price, reason="Aggregated trades")
    def reconcile_all_unresolved_orders(self, execution_id=None):
        unresolved = self.state_mgr.get_unresolved_orders()
        if execution_id: unresolved = [o for o in unresolved if o.get("execution_id")==execution_id]
        if not unresolved: return SystemRecoveryStatus.RECOVERY_COMPLETE
        any_safe = False
        for order in unresolved:
            res = self.reconcile_single_order(order)
            if res.decision in (ReconciliationDecision.NOT_FOUND, ReconciliationDecision.API_ERROR):
                any_safe = True
                current = self.state_mgr.get_order(order["client_order_id"])
                if current and current.get("status")==OrderStatus.RECONCILING.value:
                    self.state_mgr.transition_order_state(order["client_order_id"], OrderStatus.RECONCILING.value, OrderStatus.UNKNOWN.value, f"RECONCILE_{res.decision.value}_PRESERVE_UNKNOWN")
                continue
            if not self._apply_reconciliation_result(order, res): any_safe = True
        if any_safe:
            try: self.lifecycle_mgr.set_state(ApplicationState.SAFE_MODE, "Unresolved during reconcile_all")
            except Exception: pass
            return SystemRecoveryStatus.SAFE_MODE
        return SystemRecoveryStatus.RECOVERY_COMPLETE
ReconciliationEngine = HardenedReconciliationEngine
