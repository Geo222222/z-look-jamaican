# Consequence-Proportional Evidence Policy

The governing standard is **minimum sufficient evidence for the consequence of the claim**. Before collecting evidence, state the downstream decision it can authorize. Stop when that decision is determined. This policy reduces cognition and elapsed-time cost; it does not weaken the Governor, economic qualification, preregistration integrity, or any frozen experiment.

## Evidence classes

### ENGINEERING_FACT

Provider field names, parser behavior, serialization, hashing, recovery, idempotency, and schema compatibility are engineering facts. Use authoritative documentation when applicable, preserved fixtures, deterministic unit/integration tests, and the smallest bounded direct observation needed. Do not create an economic experiment for an ordinary implementation fact.

### SYSTEM_QUALIFICATION

Replay, accounting, recovery, execution-contract, and market-data-integrity claims require deterministic tests, fault injection, fixtures, and reproducible audits. Preregister only when seeing the outcome before freezing a gate could materially bias promotion.

### MODEL_CALIBRATION

Spread, depth, slippage, latency, partial-fill, fee, rejection, and capacity assumptions require bounded or continuous empirical evidence. Preregister gates whenever the result can promote a capability. Observed venue facts, configured assumptions, and modeled results remain separate.

### ECONOMIC_CLAIM

Expectancy, profitability, and capital eligibility retain the strongest standard: preregistration, prospective evidence, realistic costs, falsification, deterministic promotion rules, and explicit reversal conditions. A reasoning model cannot authorize capital.

## Evidence budget

For each task record:

1. claim class;
2. downstream decision authorized;
3. cheapest decisive falsification;
4. sufficient evidence boundary;
5. stop condition.

Do not gather substantially stronger evidence than the current decision can use. Prefer cheap falsification before expensive confirmation.

## Machine labor and agent cognition

Codex designs, tests, diagnoses, interprets, and makes evidence-bound recommendations. Deterministic software downloads, polls, collects WebSocket data, hashes, timestamps, aggregates, replays, waits for time windows, and reports readiness.

Validated collectors must run as bounded durable background jobs. Jobs must have immutable commands, explicit time/message/byte bounds, durable journals, idempotent run IDs, restart behavior, health state, automatic stop conditions, and monitor visibility. They must never receive credentials, signer access, order authority, or capital merely because they are scheduled.

The Root Agent should inspect ready evidence and continue independent dependency-DAG work while clocks or collectors run. It must not spend an interactive session waiting for a forward window.

## Semantic checkpoints

Git checkpoints are required at:

1. a pre-observation freeze when qualifying evidence could bias a gate;
2. a completed qualification/result;
3. a material operational architecture release.

Coherent engineering between these boundaries should be implemented and tested as a batch. Existing valid evidence is never rewritten for checkpoint aesthetics.

## Predecessor accelerator

Before substantial market-data, execution, accounting, scanner, or exchange design, query the qualified EPI/Epinnox manifests and audits. Reuse verified algorithms, contracts, tests, failure cases, decompositions, and architectural lessons. Never inherit authority, credentials, MAINNET defaults, profitability, uncalibrated constants, or unsafe execution paths.

## Current stream retrospective

The preserved EXP-MICROSTREAM attempts remain historically immutable. Their interpretation is:

- channel alias and provider labeling: `ENGINEERING_FACT`;
- ingest complexity and connection-global sequence scope: `ENGINEERING_FACT` plus deterministic system tests;
- replay, restart, corruption, and idempotency: `SYSTEM_QUALIFICATION`;
- source-clock tolerance and time-separated spread/depth distributions: `MODEL_CALIBRATION`;
- strategy expectancy or capital eligibility: `ECONOMIC_CLAIM`, not established by these streams.

Future engineering corrections use preserved raw journals and deterministic tests. A new frozen observation is justified only when a calibration or economic gate actually needs new prospective evidence.
