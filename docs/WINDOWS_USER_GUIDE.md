# TKO — Panduan Pengguna Windows (Tokocrypto saja)

## Ringkas
Tidak perlu Python/pip. Jalankan `TKO.exe`. Default **PAPER** (aman). LIVE dilindungi HardLiveGate.

## Instalasi portable
Salin seluruh folder `TKO/` (`TKO.exe` + `_internal/`) ke lokasi writable (contoh `C:\\TKO\\`).

## Menjalankan
```text
TKO.exe
TKO.exe --mode PAPER
TKO.exe --mode LIVE
```

## Kredensial (bukan di EXE)
1. Windows DPAPI: `%LOCALAPPDATA%\\NVRA\\Trading\\credentials\\` (`t_key.dat`, `t_secret.dat`)
2. Env fallback: `TOKOCRYPTO_API_KEY`, `TOKOCRYPTO_API_SECRET`

## HardLiveGate
Wajib untuk setiap LIVE unlock. Bypass production tidak ada.

## Kill switch
`bot_state.kill_switch` = `1`/`true`/`on`/`active`

## Path
- `data\\tko.db` — SQLite
- `logs\\tko_worker.log` — log

## Backup / update
Stop → backup `data\\` → ganti folder app → uji PAPER dulu.

## Tidak didukung
Binance trading · LIVE tanpa HardLiveGate · key di dalam EXE
