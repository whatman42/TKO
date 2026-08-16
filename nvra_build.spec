# -*- mode: python ; coding: utf-8 -*-

import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# Hidden Imports Audit untuk Pustaka Kuantitatif & ML
hidden_imports = [
    'sqlite3',
    'polars',
    'numpy',
    'pandas',
    'scipy',
    'sklearn',
    'xgboost',
    'catboost',
    'win32crypt',
    'win32service',
    'win32serviceutil',
    'win32event',
    'tokocrypto_bot.persistence.database',
    'tokocrypto_bot.execution.order_state_machine',
    'tokocrypto_bot.execution.reconciliation',
    'tokocrypto_bot.recovery.startup_recovery',
    'tokocrypto_bot.supervisor.supervisor'
]

# Build 1: Trading Worker Executable
a_worker = Analysis(
    ['tokocrypto_bot/application.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    runtime_hooks=[],
    excludes=['PyQt6', 'tkinter'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)
pyz_worker = PYZ(a_worker.pure, a_worker.zipped_data, cipher=block_cipher)
exe_worker = EXE(
    pyz_worker,
    a_worker.scripts,
    exclude_binaries=True,
    name='NVRA-Worker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True
)

# Build 2: Supervisor Executable
a_sup = Analysis(
    ['service_runner.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    excludes=['PyQt6', 'tkinter'],
    cipher=block_cipher,
)
pyz_sup = PYZ(a_sup.pure, a_sup.zipped_data, cipher=block_cipher)
exe_sup = EXE(
    pyz_sup,
    a_sup.scripts,
    exclude_binaries=True,
    name='NVRA-Supervisor',
    debug=False,
    console=True
)

# Build 3: GUI Executable
a_gui = Analysis(
    ['gui_runner.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports + ['PyQt6'],
    cipher=block_cipher,
)
pyz_gui = PYZ(a_gui.pure, a_gui.zipped_data, cipher=block_cipher)
exe_gui = EXE(
    pyz_gui,
    a_gui.scripts,
    exclude_binaries=True,
    name='NVRA-GUI',
    debug=False,
    console=False  # No black CLI window for GUI
)

coll = COLLECT(
    exe_worker, a_worker.binaries, a_worker.zipfiles, a_worker.datas,
    exe_sup, a_sup.binaries, a_sup.zipfiles, a_sup.datas,
    exe_gui, a_gui.binaries, a_gui.zipfiles, a_gui.datas,
    strip=False,
    upx=True,
    name='NVRA-TradingEngine'
)
