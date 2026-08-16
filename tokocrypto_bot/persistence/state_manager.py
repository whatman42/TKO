"""MODULE: tokocrypto_bot.persistence.state_manager"""
import json, logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from tokocrypto_bot.persistence.database import DatabaseManager, get_db_transaction
logger = logging.getLogger("NVRA.StateManager")
class StateManager:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    def create_order_intent(self, client_order_id: str, execution_id: str, signal_id: str, symbol: str, side: str, order_type: str, price: Optional[float], quantity: float, initial_status: str = "CREATED") -> bool:
        now_str = datetime.now(timezone.utc).isoformat()
        with get_db_transaction(self.db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM orders WHERE client_order_id = ?", (client_order_id,))
            if cursor.fetchone() is not None:
                logger.warning(f"Order intent {client_order_id} already exists.")
                return False
            conn.execute("INSERT INTO orders (client_order_id, execution_id, signal_id, symbol, side, order_type, price, quantity, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (client_order_id, execution_id, signal_id, symbol, side, order_type, price, quantity, initial_status, now_str, now_str))
            conn.execute("INSERT INTO order_events (client_order_id, previous_status, new_status, event_trigger, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?)", (client_order_id, None, initial_status, "ORDER_INTENT_CREATED", json.dumps({"price": price, "qty": quantity}), now_str))
        return True
    def transition_order_state(self, client_order_id: str, previous_status: str, new_status: str, trigger: str, details: Optional[Dict[str, Any]] = None, exchange_order_id: Optional[str] = None) -> bool:
        now_str = datetime.now(timezone.utc).isoformat()
        details_str = json.dumps(details) if details else "{}"
        with get_db_transaction(self.db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, exchange_order_id FROM orders WHERE client_order_id = ?", (client_order_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Order {client_order_id} not found")
            if row["status"] != previous_status:
                logger.error(f"State mismatch for {client_order_id}: DB has {row['status']}, expected {previous_status}")
                return False
            if exchange_order_id:
                conn.execute("UPDATE orders SET status = ?, exchange_order_id = ?, updated_at = ? WHERE client_order_id = ?", (new_status, exchange_order_id, now_str, client_order_id))
            else:
                conn.execute("UPDATE orders SET status = ?, updated_at = ? WHERE client_order_id = ?", (new_status, now_str, client_order_id))
            conn.execute("INSERT INTO order_events (client_order_id, previous_status, new_status, event_trigger, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?)", (client_order_id, previous_status, new_status, trigger, details_str, now_str))
        return True
    def get_order(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    def get_unresolved_orders(self) -> List[Dict[str, Any]]:
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM orders WHERE status IN ('CREATED', 'SUBMITTING', 'UNKNOWN', 'RECONCILING', 'NEW', 'PARTIALLY_FILLED')")
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()
