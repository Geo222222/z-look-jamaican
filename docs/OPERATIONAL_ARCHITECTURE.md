# Canonical Pre-Live Operational Architecture

Z Look Jamaican has one intended path:

`market evidence -> hypothesis -> preregistered experiment -> qualification -> capability promotion -> typed decision -> risk authorization -> execution request -> adapter -> external truth -> reconciliation -> receipt -> monitor -> learning`

## Authorities

- Experiment lifecycle/index: `state/experiments.json`.
- Immutable hypotheses and parameters: each experiment's hash-pinned preregistration artifact.
- Active EXP-MKT-002 observations: `state/market_shadow.json`, unchanged by this architecture.
- Capability lifecycle: `state/capabilities.json`.
- Typed execution/risk/accounting contracts: `autonomous_kernel/operations.py`.
- Immutable pre-live operation receipts: `receipts/execution/<request_id>.json`.
- Realized economic truth: `accounting/ledger.json`; shadow and simulation never enter it as realized outcomes.
- Observer contract: `python -m autonomous_kernel monitor_snapshot --json`.

No parallel execution or accounting path is authoritative.

## Capability promotion

Economic mechanisms advance one state at a time:

`DISCOVERED -> HYPOTHESIS -> PREREGISTERED -> BACKTEST_SUPPORTED -> PROSPECTIVE_SUPPORTED -> REPLAY_QUALIFIED -> SHADOW_QUALIFIED -> EXECUTION_PLANE_QUALIFIED -> CAPITAL_ELIGIBLE -> LIVE`

Every promotion requires evidence. The deterministic registry validator rejects unknown states and any `live_enabled` value other than false under the current Governor. The promotion function rejects skipped states. Model recommendations have no direct authorization effect.

## Execution and risk boundary

`ExecutionRequest` uses stable IDs, an idempotency key, capability and decision lineage, market-observation lineage, explicit mode, string decimals, instrument, side, order type, and capital-effect declaration. SHADOW and SIMULATION requests must declare `capital_effect=NONE`.

The deterministic authorization checks capability maturity and the current zero-exposure Governor snapshot. LIVE is denied unconditionally. An authorized pre-live receipt still records no venue order, no fill, no external truth, no realized P&L, and no capital movement.

One immutable receipt contains the request, risk authorization, execution result, accounting result, and hashes. Retrying the same request is idempotent. Reusing an ID with different request content fails closed. Corrupt existing receipts fail closed. An interrupted temporary file cannot become an authoritative receipt.

## Truth and reconciliation

- Request: intent.
- Authorization: bounded permission for one request and mode.
- Result: execution-plane observation; pre-live results are not venue truth.
- Fill: venue execution truth when a qualified adapter eventually supplies it.
- Venue/account balance: external truth.
- Accounting ledger: reconciled realized economic truth.

The current execution plane deliberately stops before a venue adapter. Venue-specific precision, minimum notional, fee schedules, latency, partial fills, rejection behavior, capacity, and balance reconciliation must be independently calibrated before adapter qualification.

## Recovery and observation

Repository validation verifies experiment preregistration hashes, experiment/capability relationships, live-disable invariants, Governor limits, evidence integrity, wallets, and accounting boundaries. Receipt persistence is atomic and idempotent. The monitor reads registries and receipts without invoking recovery, experiments, schedulers, signers, wallets, or network access.
