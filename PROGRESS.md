# NVRA Tokocrypto — Progress (Phase 1.2–1.6)

Repository: https://github.com/whatman42/TKO

## Commits (local source of truth)

| Phase | Commit | Summary |
|-------|--------|---------|
| 1.2 | `37bdbed` | ML fail-closed inference + model loader |
| 1.3 | `0324ccd` | Execution safety + reconciliation |
| 1.4 | `c209859` | Live gate, portfolio state, stale data, circuit breaker |
| 1.5 | `8e91f06` | Adaptive capital & position sizing |
| 1.6 | `258671b` | Partial-fill accounting & recovery |

## Test baseline

```
python3 -m pytest -q
→ 129 passed, 1 skipped
```

| Suite | Result |
|-------|--------|
| Phase 1.2 | 51 PASS |
| Phase 1.3 | 10 PASS |
| Phase 1.4 | 11 PASS |
| Phase 1.5 | 18 PASS |
| Phase 1.6 | 9 PASS |

## Hard rules enforced

- FAIL CLOSED on invalid ML / risk / reconciliation / stale data
- POST single-shot (no blind retry)
- LIVE blocked without HardLiveGate + credentials
- Partial fill uses executed qty; remainder cancel single-shot
- Adaptive equity (no fixed capital tiers)

## Residual P1

- Protective SL/TP orders on actual fill qty
- Dedicated SELL progressive partial tests

## Next

Phase 1.7 — pre-flight audit before implementation.
