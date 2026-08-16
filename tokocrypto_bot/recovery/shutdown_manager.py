"""MODULE: tokocrypto_bot.recovery.shutdown_manager"""
import sys, signal, logging
from typing import Callable, List
from tokocrypto_bot.persistence.lifecycle_state import LifecycleManager, ApplicationState
from tokocrypto_bot.recovery.single_instance import SingleInstanceLock
logger = logging.getLogger("NVRA.ShutdownManager")
class ShutdownManager:
    def __init__(self, lifecycle_mgr, instance_lock):
        self.lifecycle_mgr=lifecycle_mgr; self.instance_lock=instance_lock
        self._cleanup_callbacks=[]; self._is_shutting_down=False; self._register_signals()
    def register_cleanup_callback(self, callback): self._cleanup_callbacks.append(callback)
    def _register_signals(self):
        signal.signal(signal.SIGINT, self._signal_handler); signal.signal(signal.SIGTERM, self._signal_handler)
    def _signal_handler(self, signum, frame):
        if self._is_shutting_down: sys.exit(1)
        self.execute_graceful_shutdown(f"Signal {signal.Signals(signum).name}")
    def execute_graceful_shutdown(self, reason="Clean Shutdown"):
        if self._is_shutting_down: return
        self._is_shutting_down=True
        self.lifecycle_mgr.set_state(ApplicationState.STOPPING, reason=reason)
        for cb in self._cleanup_callbacks:
            try: cb()
            except Exception as e: logger.error(f"cleanup error: {e}")
        self.lifecycle_mgr.set_state(ApplicationState.STOPPED, reason="Clean Shutdown Complete")
        self.instance_lock.release(); sys.exit(0)
