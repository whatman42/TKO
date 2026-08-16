"""MODULE: tests.test_persistence_and_state_machine"""
import os, pytest, tempfile
from tokocrypto_bot.persistence.database import DatabaseManager
from tokocrypto_bot.persistence.migrations import run_migrations
from tokocrypto_bot.persistence.state_manager import StateManager
from tokocrypto_bot.execution.order_state_machine import OrderStateMachine, OrderStatus, InvalidStateTransitionException
@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db=DatabaseManager(db_path=os.path.join(tmpdir,"test.db")); run_migrations(db); yield db
def test_deterministic_client_order_id_length():
    c1=OrderStateMachine.generate_client_order_id("EXEC-001","SIG-999","BTCUSDT","BUY")
    c2=OrderStateMachine.generate_client_order_id("EXEC-001","SIG-999","BTCUSDT","BUY")
    assert c1==c2 and len(c1)<=36 and c1.startswith("QBOT-")
def test_illegal_state_transition():
    with pytest.raises(InvalidStateTransitionException):
        OrderStateMachine.validate_transition(OrderStatus.FILLED, OrderStatus.SUBMITTING)
def test_atomic_order_intent_creation_and_recovery(temp_db):
    sm=StateManager(temp_db); cid=OrderStateMachine.generate_client_order_id("EXEC-101","SIG-01","ETHUSDT","BUY")
    assert sm.create_order_intent(cid,"EXEC-101","SIG-01","ETHUSDT","BUY","LIMIT",3000.0,0.5) is True
    assert sm.create_order_intent(cid,"EXEC-101","SIG-01","ETHUSDT","BUY","LIMIT",3000.0,0.5) is False
    sm.transition_order_state(cid,"CREATED","SUBMITTING","HTTP_POST"); sm.transition_order_state(cid,"SUBMITTING","UNKNOWN","TIMEOUT")
    unresolved=sm.get_unresolved_orders(); assert len(unresolved)==1 and unresolved[0]["status"]=="UNKNOWN"
def test_wal_mode_and_backup(temp_db):
    conn=temp_db.get_connection(); mode=conn.execute("PRAGMA journal_mode;").fetchone()[0]; conn.close()
    assert mode.lower()=="wal"; assert temp_db.create_backup().exists()
