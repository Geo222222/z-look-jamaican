# Canonical Pre-Live Operational Architecture

Z Look Jamaican has one intended path:

`market evidence -> hypothesis -> preregistered experiment -> qualification -> capability promotion -> typed decision -> risk authorization -> execution request -> adapter -> external truth -> reconciliation -> receipt -> monitor -> learning`

## Authorities

- Experiment lifecycle/index: `state/experiments.json`.
- Immutable hypotheses and parameters: each experiment's hash-pinned preregistration artifact.
- Active EXP-MKT-002 observations: `state/market_shadow.json`, unchanged by this architecture.
- Future experiment market-data index: `state/market_data.json`; immutable raw/normalized bundles live under `artifacts/market_data/observations/`.
- Capability lifecycle: `state/capabilities.json`.
- Typed execution/risk/accounting contracts: `autonomous_kernel/operations.py`.
- Immutable pre-live operation receipts: `receipts/execution/<request_id>.json`.
- Realized economic truth: `accounting/ledger.json`; shadow and simulation never enter it as realized outcomes.
- Observer contract: `python -m autonomous_kernel monitor_snapshot --json`.
- Evidence burden and machine-labor boundary: `docs/EVIDENCE_POLICY.md`.
- Bounded background-job registry: `state/background_jobs.json`; derived claims and receipts: `runtime/background_jobs/`.

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

## Market-data plane

Each immutable observation bundle contains separate `raw`, `normalized`, and `quality` sections. Raw data retains provider identity and the provider-shaped payload. Normalized data uses stable candle fields and points back to `raw_observation_id`. Quality uses the independently qualified source/receive/observe timestamp chain. A content hash covers all three sections.

The index is deterministically rebuildable from valid bundles. A crash after bundle persistence but before index replacement leaves a recoverable orphan, not silently authoritative partial state. ID conflicts and bundle tampering fail closed. The initial public Coinbase candle capture qualifies this storage path only; it does not calibrate execution realism or modify EXP-MKT-002.

Microstructure bundles use the same immutable store and raw/normalized/quality contract. They preserve public L2 book sequence and timestamp, product rules, ticker, recent trades, exact HTTP payload hashes, request/receive timing, derived spread, deterministic depth-walk VWAP, and bounded capacity. A REST snapshot reports sequence gaps as `NOT_APPLICABLE_SNAPSHOT_ONLY`; it must never imply contiguous update coverage. Public market-data HTTP duration is not order latency, and public depth is not actual fill truth.

Execution-realism evaluation keeps three classes separate: `OBSERVED` public venue facts, `CONFIGURED` experiment assumptions, and `MODELED` shadow results. A field can be matched, conservative for one snapshot, rejected by observed facts, or `UNAVAILABLE_FOR_QUALIFICATION`. Single-snapshot support cannot establish distributions, fee tier, rejection probability, partial-fill probability, capital eligibility, live readiness, or strategy edge.

## Recovery and observation

Repository validation verifies experiment preregistration hashes, experiment/capability relationships, live-disable invariants, Governor limits, evidence integrity, wallets, and accounting boundaries. Receipt persistence is atomic and idempotent. The monitor reads registries and receipts without invoking recovery, experiments, schedulers, signers, wallets, or network access.
