"""
MODULE: service_runner.py
DESCRIPTION: Native Windows Service Wrapper for NVRA Watchdog Supervisor.
"""

import sys
import os
import time
import logging

if sys.platform == "win32":
    import win32serviceutil
    import win32service
    import win32event
    import servicemanager

from tokocrypto_bot.persistence.database import DatabaseManager
from tokocrypto_bot.supervisor.supervisor import NVRASupervisor

class NVRAWindowsService(win32serviceutil.ServiceFramework):
    _svc_name_ = "NVRATradingSupervisor"
    _svc_display_name_ = "NVRA Tokocrypto Trading Supervisor Service"
    _svc_description_ = "Watchdog Supervisor and Fault-Tolerant Manager for NVRA Quantitative Trading Worker."

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.is_running = True

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        self.is_running = False

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.EVENTID_GENERIC_INFO,
            (f"Starting {self._svc_display_name_}...", "")
        )
        self.main()

    def main(self):
        db_mgr = DatabaseManager()
        base_dir = os.path.dirname(sys.executable)
        worker_exe = os.path.join(base_dir, "NVRA-Worker.exe")
        if not os.path.exists(worker_exe):
            worker_exe = sys.executable
        worker_cmd = [worker_exe]
        supervisor = NVRASupervisor(
            db_mgr=db_mgr,
            worker_cmd=worker_cmd,
            heartbeat_timeout_sec=30.0,
            startup_grace_sec=60.0
        )
        supervisor.start_worker()
        while self.is_running:
            state = supervisor.monitor_tick()
            rc = win32event.WaitForSingleObject(self.stop_event, 3000)
            if rc == win32event.WAIT_OBJECT_0:
                break
        supervisor.stop_supervisor()

if __name__ == '__main__':
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(NVRAWindowsService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(NVRAWindowsService)
