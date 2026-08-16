"""MODULE: tokocrypto_bot.recovery.single_instance"""
import os, sys, logging
logger = logging.getLogger("NVRA.SingleInstance")
class InstanceAlreadyRunningException(Exception): pass
class SingleInstanceLock:
    def __init__(self, lock_name="NVRA_TOKOCRYPTO_TRADING_INSTANCE"):
        self.lock_name=lock_name; self._mutex=None; self._file_handle=None; self.is_acquired=False
    def acquire(self):
        if self.is_acquired: return True
        if sys.platform=="win32":
            import ctypes; from ctypes import wintypes
            k=ctypes.windll.kernel32
            mutex=k.CreateMutexW(None, False, self.lock_name)
            if not mutex: raise RuntimeError("mutex create failed")
            if k.GetLastError()==183:
                k.CloseHandle(mutex); raise InstanceAlreadyRunningException(f"Mutex {self.lock_name} held")
            self._mutex=mutex; self.is_acquired=True; return True
        else:
            import fcntl
            lock_dir=os.path.expanduser("~/.nvra"); os.makedirs(lock_dir, exist_ok=True)
            lock_file=os.path.join(lock_dir, f"{self.lock_name}.lock")
            try:
                self._file_handle=open(lock_file,"w"); fcntl.flock(self._file_handle, fcntl.LOCK_EX|fcntl.LOCK_NB)
                self._file_handle.write(str(os.getpid())); self._file_handle.flush(); self.is_acquired=True; return True
            except IOError: raise InstanceAlreadyRunningException(f"Lock {lock_file} held")
    def release(self):
        if not self.is_acquired: return
        if sys.platform=="win32" and self._mutex:
            import ctypes; ctypes.windll.kernel32.CloseHandle(self._mutex); self._mutex=None
        elif self._file_handle:
            import fcntl; fcntl.flock(self._file_handle, fcntl.LOCK_UN); self._file_handle.close(); self._file_handle=None
        self.is_acquired=False
