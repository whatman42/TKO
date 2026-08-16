"""
MODULE: tokocrypto_bot.execution.position_protection
DESCRIPTION: Phase 1.7 protective exit lifecycle (TKO-native reconstruction).
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tokocrypto_bot.persistence.database import get_db_transaction
from tokocrypto_bot.persistence.exchange_ids import DEFAULT_EXCHANGE_ID

logger = logging.getLogger("NVRA.PositionProtection")

MAX_ALGO_ORDERS = 5
PROTECTION_NONE = "NONE"
PROTECTION_SUBMITTING = "SUBMITTING"
PROTECTION_ACTIVE = "ACTIVE"
PROTECTION_PENDING = "PENDING"
PROTECTION_FILLED = "FILLED"
PROTECTION_CANCELED = "CANCELED"


class PositionProtectionManager:
    def __init__(self, state_mgr, exchange, exchange_id: str = DEFAULT_EXCHANGE_ID):
        self.state_mgr = state_mgr
        self.exchange = exchange
        self.exchange_id = exchange_id or DEFAULT_EXCHANGE_ID

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def deterministic_protective_cid(self, parent_cid: str, symbol: str) -> str:
        return f"PROT-{parent_cid}-{symbol}"[:64]

    def count_active_protective(self) -> Optional[int]:
        try:
            conn = self.state_mgr.db.get_connection()
            try:
                row = conn.execute(
                    """
                    SELECT COUNT(*) FROM position_protection
                    WHERE exchange_id=? AND protection_status IN (?,?,?)
                    """,
                    (self.exchange_id, PROTECTION_ACTIVE, PROTECTION_PENDING, PROTECTION_SUBMITTING),
                ).fetchone()
                return int(row[0] or 0)
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"max-algo count failed: {e}")
            return None

    def max_algo_allows_new(self) -> bool:
        n = self.count_active_protective()
        if n is None:
            return False  # fail closed
        return n < MAX_ALGO_ORDERS

    def list_unprotected_positions(self) -> List[Dict[str, Any]]:
        conn = self.state_mgr.db.get_connection()
        try:
            pos = conn.execute(
                "SELECT symbol, total_qty FROM positions WHERE exchange_id=? AND total_qty > 1e-12",
                (self.exchange_id,),
            ).fetchall()
            naked = []
            for r in pos:
                symbol, qty = r[0], float(r[1] or 0)
                prot = conn.execute(
                    "SELECT protection_status, protected_qty FROM position_protection WHERE exchange_id=? AND symbol=?",
                    (self.exchange_id, symbol),
                ).fetchone()
                if prot is None or prot[0] in (PROTECTION_NONE, PROTECTION_CANCELED, None):
                    naked.append({"symbol": symbol, "total_qty": qty})
                elif prot[0] in (PROTECTION_PENDING, PROTECTION_SUBMITTING, PROTECTION_ACTIVE):
                    continue
            return naked
        finally:
            conn.close()

    def ensure_protection(
        self,
        symbol: str,
        parent_cid: str,
        protected_qty: float,
        stop_price: float,
        side: str = "SELL",
    ) -> str:
        if protected_qty <= 0 or stop_price is None or float(stop_price) <= 0:
            logger.critical(f"ensure_protection fail-closed [{symbol}]: invalid qty/stop")
            return PROTECTION_NONE
        if not self.max_algo_allows_new():
            logger.critical(f"max-algo gate blocked protection for {symbol}")
            return PROTECTION_NONE

        prot_cid = self.deterministic_protective_cid(parent_cid, symbol)
        # pre-submit lookup
        lookup = getattr(self.exchange, "fetch_order_by_client_id", None)
        if lookup:
            try:
                existing = lookup(symbol, prot_cid)
                if existing:
                    status = str(existing.get("status", "")).upper()
                    mapped = PROTECTION_ACTIVE if status in ("NEW", "OPEN", "PARTIALLY_FILLED") else (
                        PROTECTION_FILLED if status == "FILLED" else PROTECTION_PENDING
                    )
                    self._upsert_row(symbol, parent_cid, prot_cid, protected_qty, stop_price, mapped)
                    return mapped
            except Exception as e:
                logger.warning(f"pre-submit lookup failed: {e}")

        self._upsert_row(symbol, parent_cid, prot_cid, protected_qty, stop_price, PROTECTION_SUBMITTING)
        post = getattr(self.exchange, "post_stop_loss_limit_non_retry", None)
        if post is None:
            logger.error("exchange lacks post_stop_loss_limit_non_retry")
            self._upsert_row(symbol, parent_cid, prot_cid, protected_qty, stop_price, PROTECTION_PENDING)
            return PROTECTION_PENDING
        try:
            post(
                symbol=symbol,
                side=side,
                quantity=protected_qty,
                stop_price=float(stop_price),
                limit_price=float(stop_price),
                client_order_id=prot_cid,
            )
            self._upsert_row(symbol, parent_cid, prot_cid, protected_qty, stop_price, PROTECTION_ACTIVE)
            return PROTECTION_ACTIVE
        except Exception as e:
            logger.error(f"protective POST failed/timeout: {e} — PENDING, no blind retry")
            self._upsert_row(symbol, parent_cid, prot_cid, protected_qty, stop_price, PROTECTION_PENDING)
            return PROTECTION_PENDING

    def _upsert_row(self, symbol, parent_cid, prot_cid, qty, stop, status):
        now = self._now()
        with get_db_transaction(self.state_mgr.db) as conn:
            conn.execute(
                """
                INSERT INTO position_protection (
                    exchange_id, account_id, symbol, parent_entry_client_order_id, protective_client_order_id,
                    protected_qty, stop_price, protection_status, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(exchange_id, symbol) DO UPDATE SET
                    parent_entry_client_order_id=excluded.parent_entry_client_order_id,
                    protective_client_order_id=excluded.protective_client_order_id,
                    protected_qty=excluded.protected_qty,
                    stop_price=excluded.stop_price,
                    protection_status=excluded.protection_status,
                    updated_at=excluded.updated_at
                """,
                (self.exchange_id, "DEFAULT", symbol, parent_cid, prot_cid, qty, stop, status, now),
            )

    def reconcile_pending_protections(self) -> None:
        conn = self.state_mgr.db.get_connection()
        try:
            rows = conn.execute(
                """
                SELECT symbol, parent_entry_client_order_id, protective_client_order_id, protected_qty, stop_price, protection_status
                FROM position_protection
                WHERE exchange_id=? AND protection_status IN (?,?)
                """,
                (self.exchange_id, PROTECTION_PENDING, PROTECTION_SUBMITTING),
            ).fetchall()
        finally:
            conn.close()
        lookup = getattr(self.exchange, "fetch_order_by_client_id", None)
        for r in rows:
            symbol, parent, prot_cid, qty, stop, st = r[0], r[1], r[2], float(r[3] or 0), r[4], r[5]
            if not lookup or not prot_cid:
                continue
            try:
                od = lookup(symbol, prot_cid)
            except Exception as e:
                logger.warning(f"pending protection lookup error: {e}")
                continue
            if not od:
                # NOT_FOUND — do NOT blind POST
                logger.warning(f"protection {prot_cid} NOT_FOUND — remain PENDING, no blind retry")
                continue
            status = str(od.get("status", "")).upper()
            if status == "FILLED":
                self._apply_protective_fill(symbol, prot_cid, od)
            elif status in ("NEW", "OPEN", "PARTIALLY_FILLED"):
                self._upsert_row(symbol, parent, prot_cid, qty, stop, PROTECTION_ACTIVE)
            elif status in ("CANCELED", "EXPIRED", "REJECTED"):
                self._upsert_row(symbol, parent, prot_cid, qty, stop, PROTECTION_CANCELED)

    def _apply_protective_fill(self, symbol: str, prot_cid: str, od: dict) -> None:
        executed = float(od.get("executedQty") or od.get("executed_qty") or 0)
        if executed <= 0:
            return
        price = float(od.get("price") or od.get("avgPrice") or 0)
        fill_id = f"PFILL-{prot_cid}-CUM-{executed:.10f}"
        now = self._now()
        with get_db_transaction(self.state_mgr.db) as conn:
            conn.execute(
                """
                INSERT INTO fills (fill_id, client_order_id, exchange_order_id, symbol, side, price, quantity, timestamp, exchange_id)
                VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(fill_id) DO NOTHING
                """,
                (fill_id, prot_cid, str(od.get("orderId") or ""), symbol, "SELL", price, executed, now, self.exchange_id),
            )
            row = conn.execute(
                "SELECT total_qty, avg_buy_price FROM positions WHERE exchange_id=? AND symbol=?",
                (self.exchange_id, symbol),
            ).fetchone()
            if row:
                prev = float(row[0] or 0)
                new_qty = max(0.0, prev - executed)
                conn.execute(
                    "UPDATE positions SET total_qty=?, updated_at=? WHERE exchange_id=? AND symbol=?",
                    (new_qty, now, self.exchange_id, symbol),
                )
            conn.execute(
                "UPDATE position_protection SET protection_status=?, updated_at=? WHERE exchange_id=? AND symbol=?",
                (PROTECTION_FILLED, now, self.exchange_id, symbol),
            )
