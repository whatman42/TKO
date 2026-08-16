# NVRA Tokocrypto Bot — Progress

**Repo:** https://github.com/whatman42/TKO  
**Mirror status:** **100% complete** (all production modules + full test suite)

## Phase completion (verified locally before sync)

| Phase | Scope | Tests | Local commit |
|-------|-------|-------|--------------|
| 1.2 | ML inference fail-closed | 51/51 | `37bdbed` |
| 1.3 | Execution safety | 10/10 | `0324ccd` |
| 1.4 | Live gate + stale + circuit | 11/11 | `c209859` |
| 1.5 | Adaptive equity sizing | 18/18 | `8e91f06` |
| 1.6 | Partial-fill lifecycle | 9/9 | `258671b` |

**Full suite (local):** 129 passed, 1 skipped (Windows DPAPI on non-Windows)

## Remote coverage

- Root: README, AGENTS, PROGRESS, gui_runner, service_runner, nvra_build.spec, .gitignore
- Package: application, exchange, execution, ml, persistence, quant, recovery, risk, security, strategy (+strategies), supervisor, gui
- Tests: Phase 1.2–1.6 + P0/P1/P2 foundation (18 test modules)

## Optional: byte-identical + full git history

API mirror may use compact formatting for large files. For exact local history:

```bash
cd Tokocrypto
git remote add tko https://github.com/whatman42/TKO.git
git push -u tko main --force
```

## Residual (non-blocking for Phase 1.7)

- Protective SL/TP order management after fill
- Amend remainder (currently cancel-only policy)
