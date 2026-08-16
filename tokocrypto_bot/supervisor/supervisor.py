"""MODULE: tokocrypto_bot.supervisor.supervisor"""
import sys, time, subprocess, logging
from enum import Enum
from datetime import datetime, timezone
from typing import Optional, List
from tokocrypto_bot.persistence.database import DatabaseManager
from tokocrypto_bot.supervisor.crash_tracker import PersistentCrashTracker
from tokocrypto_bot.supervisor.health_monitor import HealthMonitor, WorkerHealthStatus
from tokocrypto_bot.supervisor.restart_policy import RestartPolicy, WorkerExitCategory
logger = logging.getLogger("NVRA.Supervisor")
class SupervisorState(str, Enum):
    INITIALIZING="INITIALIZING"; STARTING_WORKER="STARTING_WORKER"; MONITORING="MONITORING"
    WORKER_UNHEALTHY="WORKER_UNHEALTHY"; RESTARTING="RESTARTING"; CRASH_LOOP="CRASH_LOOP"
    STOPPING="STOPPING"; STOPPED="STOPPED"
class NVRASupervisor:
    def __init__(self, db_mgr, worker_cmd, heartbeat_timeout_sec=30.0, startup_grace_sec=60.0, window_minutes=5, max_crashes=3):
        self.db_mgr=db_mgr; self.worker_cmd=worker_cmd
        self.crash_tracker=PersistentCrashTracker(db_mgr, window_minutes, max_crashes)
        self.health_monitor=HealthMonitor(db_mgr, heartbeat_timeout_sec, startup_grace_sec)
        self._state=SupervisorState.INITIALIZING; self._worker_process=None; self._worker_start_time=None; self._manual_stop_requested=False
    @property
    def state(self): return self._state
    def start_worker(self) -> bool:
        self._state=SupervisorState.STARTING_WORKER
        try:
            self._worker_process=subprocess.Popen(self.worker_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self._worker_start_time=datetime.now(timezone.utc)
            self._state=SupervisorState.MONITORING; return True
        except Exception as e:
            self.crash_tracker.record_crash_event(None, -1, f"Spawn failure: {e}")
            if self.crash_tracker.is_crash_loop_triggered(): self._state=SupervisorState.CRASH_LOOP
            return False
    def monitor_tick(self):
        if self._state in (SupervisorState.STOPPED, SupervisorState.CRASH_LOOP): return self._state
        is_alive = self._worker_process is not None and self._worker_process.poll() is None
        health = self.health_monitor.evaluate_worker_health(is_alive, self._worker_start_time)
        if health.status in (WorkerHealthStatus.HEALTHY_TRADING, WorkerHealthStatus.HEALTHY_SAFE_MODE, WorkerHealthStatus.STARTING_GRACE_PERIOD):
            self._state=SupervisorState.MONITORING; return self._state
        self._state=SupervisorState.WORKER_UNHEALTHY
        exit_code=None
        if is_alive and health.status==WorkerHealthStatus.UNHEALTHY_STALE_HEARTBEAT:
            self._worker_process.terminate()
            try: self._worker_process.wait(timeout=5.0)
            except subprocess.TimeoutExpired: self._worker_process.kill()
            exit_code=-9
        elif not is_alive:
            exit_code=self._worker_process.poll() if self._worker_process else -1
        exit_cat=RestartPolicy.classify_exit_code(exit_code)
        self.crash_tracker.record_crash_event(self._worker_process.pid if self._worker_process else None, exit_code, health.message)
        decision=RestartPolicy.evaluate_restart(exit_cat, self.crash_tracker.is_crash_loop_triggered(), self._manual_stop_requested)
        if decision.should_restart:
            self._state=SupervisorState.RESTARTING; time.sleep(2.0); self.start_worker()
        else:
            self._state=SupervisorState.CRASH_LOOP if self.crash_tracker.is_crash_loop_triggered() else SupervisorState.STOPPED
        return self._state
    def stop_supervisor(self):
        self._manual_stop_requested=True; self._state=SupervisorState.STOPPING
        if self._worker_process and self._worker_process.poll() is None:
            self._worker_process.terminate()
            try: self._worker_process.wait(timeout=10.0)
            except subprocess.TimeoutExpired: self._worker_process.kill()
        self._state=SupervisorState.STOPPED
