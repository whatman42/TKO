"""MODULE: tokocrypto_bot.gui.dashboard — Decoupled observation plane"""
import json
from datetime import datetime
try:
    from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QGroupBox, QGridLayout, QMessageBox
    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QFont
except ImportError: pass
from tokocrypto_bot.persistence.database import DatabaseManager
class NVRADashboardWindow(QMainWindow):
    def __init__(self, db_mgr):
        super().__init__(); self.db=db_mgr
        self.setWindowTitle("NVRA Tokocrypto Quantitative Trading Engine v2026.5.9"); self.resize(1100,700)
        self._init_ui(); self._start_state_polling()
    def _init_ui(self):
        main=QWidget(); self.setCentralWidget(main); layout=QVBoxLayout(main)
        header=QGroupBox("System Health & Status"); hl=QGridLayout(header)
        self.lbl_app_state=QLabel("OFFLINE"); self.lbl_app_state.setFont(QFont("Arial",14,QFont.Weight.Bold))
        self.lbl_heartbeat=QLabel("Heartbeat: -"); self.lbl_unresolved=QLabel("Unresolved Orders: 0")
        hl.addWidget(QLabel("Application State:"),0,0); hl.addWidget(self.lbl_app_state,0,1)
        hl.addWidget(self.lbl_heartbeat,1,0); hl.addWidget(self.lbl_unresolved,1,1); layout.addWidget(header)
        controls=QGroupBox("Engine Controls"); cl=QHBoxLayout(controls)
        self.btn_pause=QPushButton("PAUSE TRADING"); self.btn_pause.clicked.connect(lambda:self._write_gui_command("PAUSE_TRADING"))
        self.btn_safemode=QPushButton("ENTER SAFE MODE"); self.btn_safemode.clicked.connect(self._on_safemode)
        self.btn_reconcile=QPushButton("TRIGGER RECONCILIATION"); self.btn_reconcile.clicked.connect(lambda:self._write_gui_command("TRIGGER_RECONCILIATION"))
        cl.addWidget(self.btn_pause); cl.addWidget(self.btn_safemode); cl.addWidget(self.btn_reconcile); layout.addWidget(controls)
        pos=QGroupBox("Active Positions"); pv=QVBoxLayout(pos); self.tbl_positions=QTableWidget(0,4); pv.addWidget(self.tbl_positions); layout.addWidget(pos)
    def _start_state_polling(self):
        self.timer=QTimer(self); self.timer.timeout.connect(self._refresh_dashboard); self.timer.start(1500)
    def _refresh_dashboard(self):
        conn=self.db.get_connection()
        try:
            row=conn.execute("SELECT value FROM bot_state WHERE key='application_state'").fetchone()
            state=row[0] if row else "OFFLINE"; self.lbl_app_state.setText(state)
            n=conn.execute("SELECT COUNT(*) FROM orders WHERE status IN ('CREATED','SUBMITTING','UNKNOWN','RECONCILING')").fetchone()[0]
            self.lbl_unresolved.setText(f"Unresolved Orders: {n}")
        except Exception: self.lbl_app_state.setText("DB ERROR")
        finally: conn.close()
    def _on_safemode(self):
        if QMessageBox.warning(self,"Confirm","Force SAFE_MODE?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes:
            self._write_gui_command("FORCE_SAFE_MODE")
    def _write_gui_command(self, cmd):
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO system_events (level,component,message,payload_json,created_at) VALUES (?,?,?,?,?)",
                ("WARNING","GUI_CONTROL",f"User: {cmd}",json.dumps({"cmd":cmd}),datetime.utcnow().isoformat()))
