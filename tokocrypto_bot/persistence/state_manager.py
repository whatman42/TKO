"""
MODULE: tokocrypto_bot.persistence.state_manager
DESCRIPTION: Atomic Data Access Object (DAO) for orders, events, fills, and bot states.
Phase 1.8-A: exchange-scoped reads/writes (default exchange_id=TOKOCRYPTO).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from tokocrypto_bot.persistence.database import DatabaseManager, get_db_transaction
from tokocrypto_bot.persistence.exchange_ids import DEFAULT_EXCHANGE_ID, DEFAULT_ACCOUNT_ID

logger = logging.getLogger("NVRA.StateManager")


class StateManager:
    def __init__(self, db_manager: DatabaseManager, exchange_id: str = DEFAULT_EXCHANGE_ID, account_id: str = DEFAULT_ACCOUNT_ID):
        self.db = db_manager
        self.exchange_id = exchange_id or DEFAULT_EXCHANGE_ID
        self.account_id = account_id or DEFAULT_ACCOUNT_ID

    def create_order_intent(
        self,
        client_order_id: str,
        execution_id: str,
        signal_id: str,
        symbol: str,
        side: str,
        order_type: str,
        price: Optional[float],
        quantity: float,
        initial_status: str = "CREATED",
        exchange_id: Optional[str] = None,
        account_id: Optional[str] = None,
    ) -> bool:
        """Menyimpan intent order awal ke database secara atomic sebelum request dikirim ke jaringan."""
        eid = exchange_id or self.exchange_id
        aid = account_id or self.account_id
        now_str = datetime.now(timezone.utc).isoformat()
        with get_db_transaction(self.db) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM orders WHERE client_order_id = ? AND exchange_id = ?",
                (client_order_id, eid),
            )
            if cursor.fetchone() is not None:
                logger.warning(f"Order intent with client_order_id={client_order_id} exchange={eid} already exists.")
                return False

            conn.execute(
                """
                INSERT INTO orders (
                    client_order_id, execution_id, signal_id, symbol, side, order_type,
                    price, quantity, status, created_at, updated_at, exchange_id, account_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    client_order_id, execution_id, signal_id, symbol, side, order_type,
                    price, quantity, initial_status, now_str, now_str, eid, aid,
                ),
            )

            conn.execute(
                """
                INSERT INTO order_events (client_order_id, previous_status, new_status, event_trigger, details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (client_order_id, None, initial_status, "ORDER_INTENT_CREATED", json.dumps({"price": price, "qty": quantity, "exchange_id": eid}), now_str),
            )
        return True

    def transition_order_state(
        self,
        client_order_id: str,
        previous_status: str,
        new_status: str,
        trigger: str,
        details: Optional[Dict[str, Any]] = None,
        exchange_order_id: Optional[str] = None,
        exchange_id: Optional[str] = None,
    ) -> bool:
        """Mengubah state order dan mencatat event audit trail secara atomic."""
        eid = exchange_id or self.exchange_id
        now_str = datetime.now(timezone.utc).isoformat()
        details_str = json.dumps(details) if details else "{}"

        with get_db_transaction(self.db) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status, exchange_order_id FROM orders WHERE client_order_id = ? AND exchange_id = ?",
                (client_order_id, eid),
            )
            row = cursor.fetchone()
            if not row:
                cursor.execute("SELECT status, exchange_order_id, exchange_id FROM orders WHERE client_order_id = ?", (client_order_id,))
                row = cursor.fetchone()
                if not row:
                    raise ValueError(f"Order {client_order_id} not found in persistence layer.")
                eid = row["exchange_id"] if "exchange_id" in row.keys() else eid

            curr_status = row["status"]
            if curr_status != previous_status:
                logger.error(f"State mismatch for {client_order_id}: DB has {curr_status}, transition expected {previous_status}")
                return False

            if exchange_order_id:
                conn.execute(
                    "UPDATE orders SET status = ?, exchange_order_id = ?, updated_at = ? WHERE client_order_id = ? AND exchange_id = ?",
                    (new_status, exchange_order_id, now_str, client_order_id, eid),
                )
            else:
                conn.execute(
                    "UPDATE orders SET status = ?, updated_at = ? WHERE client_order_id = ? AND exchange_id = ?",
                    (new_status, now_str, client_order_id, eid),
                )

            conn.execute(
                """
                INSERT INTO order_events (client_order_id, previous_status, new_status, event_trigger, details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (client_order_id, previous_status, new_status, trigger, details_str, now_str),
            )
        return True

    def get_order(self, client_order_id: str, exchange_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Mengambil data order persisten berdasarkan client_order_id (+ exchange_id)."""
        eid = exchange_id or self.exchange_id
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM orders WHERE client_order_id = ? AND exchange_id = ?",
                (client_order_id, eid),
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            cursor.execute("SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_unresolved_orders(self, exchange_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Unresolved orders, scoped to exchange when provided (default: instance exchange_id)."""
        eid = exchange_id if exchange_id is not None else self.exchange_id
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM orders
                WHERE exchange_id = ?
                  AND status IN ('CREATED', 'SUBMITTING', 'UNKNOWN', 'RECONCILING', 'NEW', 'PARTIALLY_FILLED')
                """,
                (eid,),
            )
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
