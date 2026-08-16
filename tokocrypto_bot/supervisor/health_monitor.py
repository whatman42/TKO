"""MODULE: tokocrypto_bot.supervisor.health_monitor"""
import json, logging
from enum import Enum
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from tokocrypto_bot.persistence.database import DatabaseManager
logger = logging.getLogger("NVRA.Supervisor.HealthMonitor")
class WorkerHealthStatus(str, Enum):
    HEALTHY_TRADING="HEALTHY_TRADING"; HEALTHY_SAFE_MODE="HEALTHY_SAFE_MODE"; STARTING_GRACE_PERIOD="STARTING_GRACE_PERIOD"
    UNHEALTHY_PROCESS_DEAD="UNHEALTHY_PROCESS_DEAD"; UNHEALTHY_STALE_HEARTBEAT="UNHEALTHY_STALE_HEARTBEAT"; UNHEALTHY_INVALID_STATE="UNHEALTHY_INVALID_STATE"
@dataclass(frozen=True)
class HealthEvaluationResult:
    status: WorkerHealthStatus; is_alive: bool; app_state: str; heartbeat_age_seconds: float; message: str
class HealthMonitor:
    def __init__(self, db_mgr, heartbeat_timeout_sec=30.0, startup_grace_sec=60.0):
        self.db=db_mgr; self.heartbeat_timeout=heartbeat_timeout_sec; self.startup_grace=startup_grace_sec
    def evaluate_worker_health(self, worker_process_alive, worker_start_time=None):
        now = datetime.now(timezone.utc)
        if not worker_process_alive:
            return HealthEvaluationResult(WorkerHealthStatus.UNHEALTHY_PROCESS_DEAD, False, "UNKNOWN", -1.0, "Process dead")
        if worker_start_time and (now - worker_start_time).total_seconds() < self.startup_grace:
            return HealthEvaluationResult(WorkerHealthStatus.STARTING_GRACE_PERIOD, True, "STARTING", (now-worker_start_time).total_seconds(), "Grace period")
        conn = self.db.get_connection()
        try:
            hb_row = conn.execute("SELECT value, updated_at FROM bot_state WHERE key='heartbeat'").fetchone()
            app_row = conn.execute("SELECT value FROM bot_state WHERE key='application_state'").fetchone()
            app_state = app_row[0] if app_row else "UNKNOWN"
            if not hb_row:
                return HealthEvaluationResult(WorkerHealthStatus.UNHEALTHY_STALE_HEARTBEAT, True, app_state, 9999.0, "No heartbeat")
            hb_data = json.loads(hb_row["value"] if isinstance(hb_row, dict) or hasattr(hb_row, 'keys') else hb_row[0])
            last_hb_str = hb_data.get("last_heartbeat", hb_row["updated_at"] if hasattr(hb_row,'keys') else hb_row[1])
            last_hb_dt = datetime.fromisoformat(last_hb_str)
            if last_hb_dt.tzinfo is None: last_hb_dt = last_hb_dt.replace(tzinfo=timezone.utc)
            age = (now - last_hb_dt).total_seconds()
            if age > self.heartbeat_timeout:
                return HealthEvaluationResult(WorkerHealthStatus.UNHEALTHY_STALE_HEARTBEAT, True, app_state, age, f"Stale {age:.1f}s")
            if app_state == "SAFE_MODE":
                return HealthEvaluationResult(WorkerHealthStatus.HEALTHY_SAFE_MODE, True, app_state, age, "SAFE_MODE healthy")
            return HealthEvaluationResult(WorkerHealthStatus.HEALTHY_TRADING, True, app_state, age, f"Healthy [{app_state}]")
        except Exception as e:
            return HealthEvaluationResult(WorkerHealthStatus.UNHEALTHY_INVALID_STATE, True, "ERROR", -1.0, str(e))
        finally: conn.close()
