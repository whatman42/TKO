"""MODULE: tokocrypto_bot.gui.wizard — Credential setup wizard"""
from typing import Optional
try:
    from PyQt6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QLineEdit, QCheckBox, QPushButton, QMessageBox, QGroupBox
except ImportError: pass
from tokocrypto_bot.security.credential_manager import SecureCredentialStore
from tokocrypto_bot.exchange.tokocrypto_client import TokocryptoDirectClient
class NVRASetupWizardDialog(QDialog):
    def __init__(self, cred_store=None):
        super().__init__(); self.cred_store=cred_store or SecureCredentialStore()
        self.setWindowTitle("NVRA Setup Wizard"); self.resize(500,450); self._init_ui()
    def _init_ui(self):
        layout=QVBoxLayout(self)
        ex=QGroupBox("Tokocrypto Credentials"); form=QFormLayout(ex)
        self.txt_t_key=QLineEdit(); self.txt_t_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_t_secret=QLineEdit(); self.txt_t_secret.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("API Key:",self.txt_t_key); form.addRow("API Secret:",self.txt_t_secret); layout.addWidget(ex)
        ai=QGroupBox("Gemini AI (Optional)"); aiform=QFormLayout(ai)
        self.chk_gemini=QCheckBox("Enable Gemini"); self.chk_gemini.setChecked(True)
        self.txt_g_key=QLineEdit(); self.txt_g_key.setEchoMode(QLineEdit.EchoMode.Password)
        aiform.addRow(self.chk_gemini); aiform.addRow("Gemini Key:",self.txt_g_key); layout.addWidget(ai)
        self.btn_validate=QPushButton("Test Connections"); self.btn_validate.clicked.connect(self._on_validate)
        self.btn_save=QPushButton("Save Encrypted & Continue"); self.btn_save.clicked.connect(self._on_save)
        layout.addWidget(self.btn_validate); layout.addWidget(self.btn_save)
    def _on_validate(self):
        key,secret=self.txt_t_key.text().strip(),self.txt_t_secret.text().strip()
        if not key or not secret: QMessageBox.warning(self,"Error","Key & Secret required"); return
        try:
            bal=TokocryptoDirectClient(key,secret).fetch_account_balances()
            QMessageBox.information(self,"OK",f"Connected. USDT: ${bal.get('USDT',{}).get('free',0):.2f}")
        except Exception as e: QMessageBox.critical(self,"Failed",str(e))
    def _on_save(self):
        t_key,t_secret=self.txt_t_key.text().strip(),self.txt_t_secret.text().strip()
        g_key=self.txt_g_key.text().strip() if self.chk_gemini.isChecked() else None
        if not t_key or not t_secret: QMessageBox.warning(self,"Error","Required"); return
        if self.cred_store.save_credentials(t_key,t_secret,g_key):
            QMessageBox.information(self,"Saved","Credentials encrypted"); self.accept()
        else: QMessageBox.critical(self,"Error","Encrypt failed")
