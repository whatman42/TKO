# TKO Operations

## Modes
- **PAPER** (default): autonomous paper trading. Strategy fallback works without a trained ML model.
- **LIVE**: real capital. Requires Tokocrypto API credentials + a valid model artifact. HardLiveGate is mandatory.

## Model
Bundled installer: `models/install_champion.py` → writes `models/champion_model.pkl`
Version **DEV_NEUTRAL_0.5_2026.1.0**
- Pure-Python neutral model (P(UP)=P(DOWN)=0.5)
- Lets HardLiveGate pass structurally
- Does **not** generate ML BUY/SELL (thresholds require ≥0.65)
- Replace with your trained champion for real ML signals

### Install / override
```bash
python models/install_champion.py
# or
python scripts/build_neutral_champion.py
```
1. Env: `NVRA_MODEL_PATH=/path/to/champion_model.pkl`
2. Or: `%LOCALAPPDATA%\\NVRA\\Trading\\models\\champion_model.pkl` (Windows)
3. Or: `./models/champion_model.pkl` next to the app

Optional integrity: `NVRA_MODEL_SHA256=<hex>`

## LIVE checklist
1. PAPER runs clean for several cycles
2. Set API credentials (DPAPI store or env — never commit secrets)
3. Run `python models/install_champion.py` (or place trained model)
4. Start LIVE; confirm HardLiveGate PASS in logs
5. Neutral model → no ML trades until you replace the champion

## Safety
- No blind POST retry
- Kill switch / circuit breaker / SAFE_MODE remain active
- Credentials never embedded in the binary
