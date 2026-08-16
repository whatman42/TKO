"""MODULE: tokocrypto_bot.supervisor.restart_policy"""
from enum import Enum
from dataclasses import dataclass
from typing import Optional
class WorkerExitCategory(str, Enum):
    NORMAL_SHUTDOWN="NORMAL_SHUTDOWN"; USER_STOPPED="USER_STOPPED"; SAFE_MODE_EXIT="SAFE_MODE_EXIT"
    UNHANDLED_EXCEPTION="UNHANDLED_EXCEPTION"; WATCHDOG_KILLED="WATCHDOG_KILLED"
@dataclass(frozen=True)
class RestartDecision:
    should_restart: bool; reason: str
class RestartPolicy:
    @staticmethod
    def classify_exit_code(exit_code):
        if exit_code == 0: return WorkerExitCategory.NORMAL_SHUTDOWN
        if exit_code == 100: return WorkerExitCategory.SAFE_MODE_EXIT
        if exit_code in (130, 143): return WorkerExitCategory.USER_STOPPED
        return WorkerExitCategory.UNHANDLED_EXCEPTION
    @staticmethod
    def evaluate_restart(exit_category, crash_loop_triggered, is_manual_stop=False):
        if is_manual_stop or exit_category == WorkerExitCategory.USER_STOPPED:
            return RestartDecision(False, "Manual stop")
        if exit_category == WorkerExitCategory.NORMAL_SHUTDOWN:
            return RestartDecision(False, "Normal exit")
        if crash_loop_triggered:
            return RestartDecision(False, "CRASH-LOOP PROTECTION")
        if exit_category in (WorkerExitCategory.UNHANDLED_EXCEPTION, WorkerExitCategory.WATCHDOG_KILLED):
            return RestartDecision(True, f"Restart after {exit_category.value}")
        return RestartDecision(False, f"No action for {exit_category.value}")
