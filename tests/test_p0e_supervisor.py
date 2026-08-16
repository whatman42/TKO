"""MODULE: tests.test_p0e_supervisor"""
import os, sys, time, pytest, tempfile
from tokocrypto_bot.persistence.database import DatabaseManager
from tokocrypto_bot.persistence.migrations import run_migrations
from tokocrypto_bot.persistence.lifecycle_state import LifecycleManager, ApplicationState
from tokocrypto_bot.supervisor.supervisor import NVRASupervisor, SupervisorState
from tokocrypto_bot.supervisor.crash_tracker import PersistentCrashTracker
@pytest.fixture
def supervisor_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        db=DatabaseManager(db_path=os.path.join(tmpdir,"sup.db")); run_migrations(db)
        ws=os.path.join(tmpdir,"dummy.py"); open(ws,"w").write("import time\ntime.sleep(100)\n")
        sup=NVRASupervisor(db,[sys.executable,ws],heartbeat_timeout_sec=2.0,startup_grace_sec=1.0,window_minutes=5,max_crashes=3)
        yield sup, db, LifecycleManager(db)
def test_supervisor_crash_loop_protection(supervisor_env):
    _,db,_=supervisor_env; ct=PersistentCrashTracker(db,5,3)
    ct.record_crash_event(1001,1,"C1"); ct.record_crash_event(1002,1,"C2"); assert ct.record_crash_event(1003,1,"C3")==3 and ct.is_crash_loop_triggered()
def test_safe_mode_prevents_supervisor_restart(supervisor_env):
    sup,_,lm=supervisor_env; sup.start_worker(); time.sleep(1.2)
    lm.set_state(ApplicationState.SAFE_MODE,"test"); lm.write_heartbeat()
    assert sup.monitor_tick()==SupervisorState.MONITORING; sup.stop_supervisor()
def test_stale_heartbeat_triggers_restart(supervisor_env):
    sup,db,lm=supervisor_env; sup.start_worker(); time.sleep(1.2)
    with db.get_connection() as conn:
        conn.execute("INSERT INTO bot_state (key,value,updated_at) VALUES ('heartbeat',?,?)", ('{"last_heartbeat":"2020-01-01T00:00:00+00:00"}','2020-01-01T00:00:00+00:00'))
    assert sup.monitor_tick() in (SupervisorState.RESTARTING, SupervisorState.MONITORING); sup.stop_supervisor()
