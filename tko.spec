# -*- mode: python ; coding: utf-8 -*-
block_cipher = None
hidden = [
    'tokocrypto_bot.application','tokocrypto_bot.persistence.database','tokocrypto_bot.persistence.migrations',
    'tokocrypto_bot.persistence.state_manager','tokocrypto_bot.persistence.lifecycle_state',
    'tokocrypto_bot.recovery.live_gate','tokocrypto_bot.recovery.startup_recovery',
    'tokocrypto_bot.exchange.adapter','tokocrypto_bot.exchange.tokocrypto_client','tokocrypto_bot.exchange.circuit_breaker',
    'tokocrypto_bot.security.credential_manager','tokocrypto_bot.ml.inference','tokocrypto_bot.ml.model_loader',
    'tokocrypto_bot.strategy.market_data','tokocrypto_bot.strategy.features','tokocrypto_bot.strategy.decision',
    'tokocrypto_bot.strategy.portfolio','tokocrypto_bot.strategy.selector','tokocrypto_bot.strategy.pair_universe',
    'tokocrypto_bot.execution.reconciliation','tokocrypto_bot.execution.order_state_machine',
]
a = Analysis(['tko_worker_entry.py'], pathex=[], binaries=[], datas=[], hiddenimports=hidden,
    excludes=['PyQt6','matplotlib','scipy','sklearn','tensorflow','torch','onnxruntime','IPython','jedi','win32serviceutil'],
    cipher=block_cipher, noarchive=False)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='TKO', debug=False, strip=False, upx=True, console=True)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=True, name='TKO')
