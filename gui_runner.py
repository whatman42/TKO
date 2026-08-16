"""
MODULE: gui_runner
DESCRIPTION: Decoupled Entry Point for NVRA PyQt6 GUI Dashboard.
"""

import sys
import logging
from tokocrypto_bot.persistence.database import DatabaseManager
from tokocrypto_bot.gui.dashboard import NVRADashboardWindow

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:
    print("PyQt6 is not installed. GUI cannot launch.")
    sys.exit(1)

logging.basicConfig(level=logging.INFO)

def main():
    db_mgr = DatabaseManager()
    app = QApplication(sys.argv)
    window = NVRADashboardWindow(db_mgr)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
