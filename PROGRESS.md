# NVRA Tokocrypto Bot — Progress

**Repo:** https://github.com/whatman42/TKO  
**Status:** Production modules + full test suite synced (API mirror)

## Phase completion (local verified)

| Phase | Scope | Tests | Commit (local) |
|-------|-------|-------|----------------|
| 1.2 | ML inference fail-closed | 51/51 | `37bdbed` |
| 1.3 | Execution safety | 10/10 | `0324ccd` |
| 1.4 | Live gate + stale + circuit | 11/11 | `c209859` |
| 1.5 | Adaptive equity sizing | 18/18 | `8e91f06` |
| 1.6 | Partial-fill lifecycle | 9/9 | `258671b` |

**Full suite (local):** 129 passed, 1 skipped (Windows DPAPI)

## Synced to GitHub

- All production packages: application, exchange, execution, ml, persistence, quant, recovery, risk, security, strategy, supervisor, gui
- Full test suite: Phase 1.2–1.6 + P0/P1/P2 foundation tests
- Entry points: gui_runner, service_runner, nvra_build.spec

## Note on byte-identical history

API push uses compact formatting for some large files. For exact local git history + full formatting:

```bash
cd Tokocrypto
git remote add tko https://github.com/whatman42/TKO.git
git push -u tko main --force
```

## Residual (non-blocking)

- Protective SL/TP order management after fill
- Amend remainder (currently cancel-only)
- `.pyk` artifact not synced (non-source)
