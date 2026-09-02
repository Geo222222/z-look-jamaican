# Observability and Reflection

ZLJ must be able to observe the market-data and model systems it builds after deployment. Live reflection is a first-class architectural requirement, not an afterthought.

> **ZLJ sees. Benjamin decides. Watchman governs. The Hand executes. The Book remembers and proves.**

## Required ZLJ telemetry domains

Where applicable, collect:

- service health and uptime;
- structured logs;
- traces and dependency latency;
- queue depth and job failures;
- infrastructure utilization and cost;
- external API/RPC/data-provider failures;
- source freshness and sequence integrity;
- normalization/feature-pipeline failures;
- model identity/version/qualification;
- prediction latency;
- predictions recorded before outcomes are knowable;
- later observed outcomes/labels;
- calibration and competence by relevant instrument/horizon/regime;
- drift/out-of-distribution indicators;
- data/model deployment metadata;
- security anomalies.

ZLJ may also consume downstream execution/outcome evidence when it is necessary to evaluate whether a prediction corresponded to economically executable reality. That evidence remains owned by the producing organ.

## Every important intelligence object should be attributable

Preserve enough context to answer:

- which ZLJ version produced it;
- which data/model/feature version produced it;
- what sources and timestamps were available;
- what horizon/question was being predicted;
- what the system predicted;
- how confident/calibrated/qualified the producer was;
- what later outcome became knowable;
- what downstream Benjamin/Watchman/Hand references exist when relevant.

Do not merge those facts into one retrospective story.

## Reflection record

A reflection may have a machine-readable form resembling:

```yaml
subject: model-or-zlj-service-id
period: ISO-8601-window
instrument: optional
horizon: optional
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
  - immutable-or-governed-reference
decision: keep|improve|recalibrate|demote|suspend|rollback|quarantine|abandon
next_experiment: experiment-id
```

## Prediction-error discipline

ZLJ should prefer learning from error over merely reporting performance.

For each meaningful discrepancy classify likely causes such as:

- model error;
- miscalibration;
- stale data;
- data-quality or timing failure;
- implementation defect;
- latency mismatch;
- execution-feasibility assumption error;
- external market/regime change;
- hidden cost;
- provider/infrastructure degradation;
- out-of-distribution conditions.

Then design the smallest experiment that can discriminate among causes.

## Cross-organ attribution

Do not make ZLJ's telemetry imply that ZLJ owns the whole trading outcome.

Where a full Epinnox case exists, preserve enough references to distinguish:

- **ZLJ** — was the market observation/prediction useful and calibrated?
- **Benjamin** — was the decision rational and correctly sized from available evidence?
- **Watchman** — what was permitted/blocked and why?
- **The Hand** — was execution faithful and economical?
- **Outcome** — what actually happened afterward?

A losing trade is not automatically a bad ZLJ prediction. A good ZLJ prediction is not automatically a good Benjamin decision. A good Benjamin decision can still be blocked correctly. A good authorized decision can still suffer poor execution.

## Live monitoring relationship

ZLJ reasoning systems should consume telemetry through read-oriented interfaces. Canonical market-data and model-serving paths should not depend on an unconstrained LLM response for deterministic, latency-sensitive truth.

The ZLJ monitor may trigger local safe actions such as:

- quarantining/demoting a model;
- suspending a faulty data/model service;
- opening an incident;
- starting a diagnostic experiment;
- launching a sandbox repair workflow;
- comparing a candidate build in canary/shadow mode.

It may not:

- suspend/cancel a live financial position as if it were The Hand;
- change Benjamin's capital decision;
- weaken Watchman;
- route around another organ because a model is degraded.

If model degradation should affect trading, ZLJ reports degraded/invalid intelligence and the downstream organs act according to their own contracts.

## Deployment reflection

After every material ZLJ release, verify:

1. deployment completed;
2. health checks pass;
3. telemetry is arriving;
4. data/model behavior matches expectations;
5. qualification/calibration remains valid;
6. no new provenance/timing/security failures exist;
7. rollback/quarantine remains available.

A release is incomplete until post-deployment observation occurs.

## Core invariant

> **ZLJ observes itself so it can improve what it sees; observability does not expand what ZLJ is authorized to do.**
