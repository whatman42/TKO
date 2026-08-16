"""
MODULE: tokocrypto_bot.persistence.lifecycle_state
DESCRIPTION: Application Lifecycle State & Heartbeat Manager for Supervisor/Watchdog contract.
"""

import os
import json
import logging
from enum import Enum
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from tokocrypto_bot.persistence.database import DatabaseManager, get_db_transaction

logger = logging.getLogger("NVRA.ApplicationLifecycle")


class ApplicationState(str, Enum):
    STARTING = "STARTING"
    RECOVERING = "RECOVERING"
    RECONCILING = "RECONCILING"
    VALIDATING = "VALIDATING"
    READY = "READY"
    TRADING = "TRADING"
    PAUSED = "PAUSED"
    SAFE_MODE = "SAFE_MODE"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


class LifecycleManager:
    def __init__(self, db_manager: DatabaseManager, engine_version: str = "2026.5.9"):
        self.db = db_manager
        self.version = engine_version
        self._current_state = ApplicationState.STARTING

    @property
    def current_state(self) -> ApplicationState:
        return self._current_state

    def set_state(self, new_state: ApplicationState, reason: str = "") -> None:
        logger.info(f"APPLICATION STATE TRANSITION: [{self._current_state.value}] -> [{new_state.value}] (Reason: {reason})")
        self._current_state = new_state
        now_str = datetime.now(timezone.utc).isoformat()
        
        with get_db_transaction(self.db) as conn:
            conn.execute(
                """
                INSERT INTO bot_state (key, value, updated_at) VALUES ('application_state', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (new_state.value, now_str)
            )
            conn.execute(
                """
                INSERT INTO system_events (level, component, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("INFO", "LIFECYCLE", f"State set to {new_state.value}", json.dumps({"reason": reason}), now_str)
            )

    def write_heartbeat(self, metrics: Optional[Dict[str, Any]] = None) -> None:
        """Menulis Heartbeat Contract secara berkala untuk dikonsumsi Watchdog/Supervisor P0-E."""
        now_str = datetime.now(timezone.utc).isoformat()
        payload = {
            "pid": os.getpid(),
            "version": self.version,
            "state": self._current_state.value,
            "last_heartbeat": now_str,
            "metrics": metrics or {}
        }

        with get_db_transaction(self.db) as conn:
            conn.execute(
                """
                INSERT INTO bot_state (key, value, updated_at) VALUES ('heartbeat', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (json.dumps(payload), now_str)
            )

    def verify_database_integrity(self) -> bool:
        """Menjalankan SQLite PRAGMA integrity_check saat startup."""
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check;")
            result = cursor.fetchone()[0]
            if result.lower() == "ok":
                logger.info("Database integrity check: PASSED (OK)")
                return True
            else:
                logger.critical(f"Database integrity check FAILED: {result}")
                return False
        except Exception as e:
            logger.critical(f"Database integrity check exception: {e}")
            return False
        finally:
            conn.close()
