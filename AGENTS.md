# NVRA Tokocrypto Trading Engine - Development Rules

## Core Principle

This is an autonomous trading system.
Financial safety takes priority over feature completeness.

Never introduce behavior that can submit an order when:
- risk gate is not satisfied
- reconciliation is unresolved
- ML inference is invalid when ML is required
- market data is stale
- execution state is ambiguous
- duplicate-order status cannot be resolved

When uncertain, FAIL CLOSED -> NO_TRADE.

## Architecture Authority

Execution Layer has authority over order lifecycle.

Risk Engine has authority to approve/reject trade risk.

Decision Engine generates candidate decisions only.

ML provides probability/prediction, not execution authority.

Strategies generate CandidateSignal only.

Gemini is a supervisory administrator.
Gemini does NOT execute orders directly.
Gemini does NOT bypass Risk Gate.
Gemini is invoked periodically for evaluation,
optimization, model review, and strategic recommendations.

## Autonomous Loop

Market Data
-> Features
-> ML Inference
-> Strategy Selection
-> Decision
-> Risk Gate
-> Position Sizing
-> Portfolio Ranking
-> Execution
-> Reconciliation
-> Persistence

Every stage must fail closed.

## Multi-Pair

Pair universe is dynamically discovered.
Do not hardcode a fixed trading pair list unless explicitly required
by configuration.

## Multi-Strategy

Strategies must be modular and independently testable.

A strategy produces CandidateSignal.
It must never directly submit an exchange order.

## ML

Model loading must:
- use pathlib
- support environment override
- validate model integrity
- validate feature version
- fail closed if unavailable

Never silently train, replace, or promote a production model.

## Self-Learning

Champion model must never be replaced automatically without:
- evaluation
- validation
- promotion gate
- rollback capability

## Changes

Before modifying production-critical code:
1. inspect existing implementation
2. preserve public interfaces where possible
3. add tests
4. run full pytest
5. report changed files
6. report failures
7. do not silently modify unrelated modules

## Live Trading

Never enable LIVE execution merely to make tests pass.

Tests must default to PAPER/SHADOW.

No API secret may be hardcoded or committed.
