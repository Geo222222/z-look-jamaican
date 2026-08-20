# Observability and Reflection

The autonomous agent must be able to observe the systems it builds after deployment. Live reflection is a first-class architectural requirement, not an afterthought.

## Required telemetry domains

Where applicable, collect:
- service health and uptime;
- structured logs;
- traces and dependency latency;
- queue depth and job failures;
- infrastructure utilization and cost;
- external API/RPC failures;
- strategy decisions and rejected decisions;
- simulated and actual execution outcomes;
- transaction receipts/reverts;
- realized and unrealized P&L kept clearly separate;
- fees, gas, slippage, provider charges, and other costs;
- accounting reconciliation;
- prediction error;
- deployment/version metadata;
- security anomalies.

## Every deployed decision should be attributable

For important automated actions, preserve enough context to answer:

- which version made the decision;
- which strategy/rule produced it;
- what inputs were observed;
- what the system predicted;
- what action was taken or rejected;
- what actually happened afterward.

## Reflection record

A reflection should have a machine-readable form resembling:

```yaml
subject: strategy-or-service-id
period: ISO-8601-window
expected:
  metric: value
observed:
  metric: value
variance:
  metric: value
hypotheses:
  - explanation
confidence: 0.0-1.0
evidence:
  - immutable-reference
decision: keep|improve|reduce|suspend|rollback|quarantine|abandon
next_experiment: experiment-id
```

## Prediction-error discipline

The agent should prefer learning from error over merely reporting performance.

For each meaningful discrepancy classify likely causes:
- model error;
- stale data;
- data-quality failure;
- implementation defect;
- latency/execution mismatch;
- external market/environment change;
- hidden cost;
- adversarial/competitive behavior;
- infrastructure degradation.

Then design the smallest experiment that can discriminate among causes.

## Live monitoring relationship

The AI control plane should consume telemetry through read-oriented interfaces. Production execution services should not depend on an LLM response to complete deterministic, latency-sensitive operations.

The monitor may trigger safe actions such as:
- suspending a strategy through an approved control interface;
- opening an incident;
- starting a diagnostic experiment;
- launching a sandbox repair workflow;
- comparing a candidate build in canary/shadow mode.

It may not bypass the Governor to recover performance.

## Deployment reflection

After every material release, verify:
1. deployment completed;
2. health checks pass;
3. telemetry is arriving;
4. economics/behavior match expectations;
5. no new risk-control failures exist;
6. rollback remains available.

A release is incomplete until post-deployment observation occurs.
