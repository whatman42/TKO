"""MODULE: tokocrypto_bot.supervisor.crash_tracker"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from tokocrypto_bot.persistence.database import DatabaseManager, get_db_transaction
logger = logging.getLogger("NVRA.Supervisor.CrashTracker")
class PersistentCrashTracker:
    def __init__(self, db_mgr, window_minutes=5, max_crashes=3):
        self.db=db_mgr; self.window_minutes=window_minutes; self.max_crashes=max_crashes; self._init_table()
    def _init_table(self):
        with get_db_transaction(self.db) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS supervisor_incidents (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, pid INTEGER, exit_code INTEGER, reason TEXT NOT NULL, restart_number INTEGER NOT NULL);")
    def record_crash_event(self, pid, exit_code, reason):
        now_str = datetime.now(timezone.utc).isoformat()
        conn = self.db.get_connection()
        try:
            total = conn.execute("SELECT COUNT(*) FROM supervisor_incidents").fetchone()[0] + 1
        finally: conn.close()
        with get_db_transaction(self.db) as conn:
            conn.execute("INSERT INTO supervisor_incidents (timestamp, pid, exit_code, reason, restart_number) VALUES (?,?,?,?,?)", (now_str, pid, exit_code, reason, total))
        return self.get_recent_crash_count()
    def get_recent_crash_count(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=self.window_minutes)).isoformat()
        conn = self.db.get_connection()
        try: return conn.execute("SELECT COUNT(*) FROM supervisor_incidents WHERE timestamp >= ?", (cutoff,)).fetchone()[0]
        finally: conn.close()
    def is_crash_loop_triggered(self):
        return self.get_recent_crash_count() >= self.max_crashes
