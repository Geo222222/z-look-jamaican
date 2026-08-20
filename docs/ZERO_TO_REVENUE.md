# Zero-to-Revenue Graduation Model

The system starts with no assumed edge and no production capital. Opportunities must earn resources through evidence.

## Stage 0 — Opportunity discovery

Goal: create a ranked opportunity map.

Required outputs:
- opportunity thesis;
- addressable mechanism for earning USD-denominated value;
- dependency map;
- legal/compliance considerations;
- capital requirement;
- automation potential;
- likely competition;
- measurable falsification test.

No live financial exposure.

## Stage 1 — Research prototype

Goal: prove the opportunity can be observed and modeled.

Required outputs:
- working data ingestion or business validation mechanism;
- normalized data;
- unit-economics model;
- explicit cost model;
- known assumptions;
- first falsification results.

## Stage 2 — Backtest / deterministic simulation

Goal: evaluate the idea under historical or replayed conditions where appropriate.

For market strategies include fees, gas, price impact, latency assumptions, failure/revert probability, MEV/ordering effects where relevant, and data-timing limitations.

For product businesses include acquisition assumptions, hosting/provider costs, billing costs, support burden, churn/retention assumptions, abuse risk, and realistic demand evidence.

## Stage 3 — Shadow / paper operation

Goal: run against live external conditions without risking production capital.

Record what the system **would** have done before outcomes are known. Retrospective cherry-picking is invalid.

Measure:
- predicted vs observed outcome;
- execution feasibility;
- false positives;
- latency;
- error rates;
- infrastructure cost;
- stability over time.

## Stage 4 — Production-readiness review

Required:
- sufficient live shadow evidence for the opportunity's cadence;
- deterministic risk checks;
- accounting reconciliation;
- observability and alerting;
- incident/rollback path;
- security review;
- Governor authorization for any needed capital or write permissions.

The root agent may recommend graduation but cannot manufacture Governor authorization.

## Stage 5 — Micro-production

Use the smallest practical real exposure allowed by the Governor.

Primary objective: validate that simulated economics survive real execution.

Do not optimize profit yet. Optimize information gained per unit of risk.

## Stage 6 — Limited production

Increase exposure only after measured real outcomes remain consistent with the thesis and operational controls prove reliable.

## Stage 7 — Scale

Scale only where marginal economics remain positive. Re-evaluate capacity, slippage, competition, customer saturation, provider concentration, and operational risk.

## Automatic demotion

Any production system may be moved backward to limited, micro, shadow, research, quarantine, or rejected state when:
- realized edge materially degrades;
- prediction error exceeds tolerance;
- risk controls fail;
- accounting diverges;
- external protocols/providers change;
- security assumptions change;
- superior opportunity cost becomes clear.

## Rejected opportunity record

Every rejection should record:
- why it failed;
- evidence supporting rejection;
- conditions under which it may be reconsidered.

Example:

```yaml
status: rejected
reason: executable edge disappears after realistic priority fees
reconsider_if:
  - median execution cost decreases materially
  - liquidity increases materially
  - a new execution mechanism changes ordering economics
```
