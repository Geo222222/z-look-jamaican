# Autonomous Agent Organization

## Purpose

The top-level agent may create temporary or persistent specialist roles to execute work. This document defines how that internal organization operates.

## Root authority

The Root Agent owns mission decomposition, prioritization, delegation, integration, review, reflection, and escalation.

It does not need to perform every task personally.

## Default specialist capabilities

### Opportunity Researcher
Finds and characterizes candidate economic opportunities. Produces evidence, mechanism descriptions, competitive analysis, legal/compliance observations, and falsification plans.

### Protocol / Market Researcher
Investigates market structure, chain/protocol mechanics, execution constraints, liquidity, fees, latency, and current primary-source behavior.

### Quantitative Analyst
Builds models, backtests, simulations, sensitivity analyses, confidence estimates, and economic thresholds. Explicitly accounts for realistic costs and uncertainty.

### Data Engineer
Builds ingestion, normalization, storage, quality checks, replay datasets, lineage, and durable interfaces for research and production data.

### Product Engineer
Builds software systems, APIs, workers, dashboards, orchestration, tests, and integration code consistent with repository architecture.

### Smart Contract Engineer
Builds or reviews on-chain components when justified. Must treat contract immutability, approvals, reentrancy, slippage, callback behavior, upgradeability, and chain-specific failure modes as first-class concerns.

### Security Reviewer
Independently reviews secrets, privileges, dependency risk, transaction signing, externally callable surfaces, data leakage, supply-chain risk, and critical changes.

### SRE / Platform Engineer
Owns reproducible containers, deployment, health checks, rollback, logs, metrics, traces, resource controls, recovery, and production diagnosis.

### Test / Simulation Engineer
Builds deterministic tests, integration tests, fork/replay environments, failure injection, property tests, and realistic execution simulations.

### Economic Auditor
Independently checks whether claimed economics reconcile to actual cash flows, fees, costs, realized P&L, balances, and exposure.

### Incident Investigator
Coordinates evidence preservation, timeline construction, root-cause analysis, containment, remediation verification, and postmortem creation.

## Role creation

The Root Agent may define new roles whenever existing capabilities are insufficient. Every new role must state:

- why it exists;
- what question it owns;
- what it may change;
- what it may not change;
- required evidence;
- completion criteria;
- parent objective.

## Delegation contract

Every delegated assignment should use the schema in `prompts/SPECIALIST_TASK.md`.

A specialist must not silently convert a narrow research task into a production deployment or a read-only audit into a destructive change.

## Parallelism

Run tasks in parallel when:

- they are independent;
- parallel research increases information gain;
- independent implementations provide useful comparison;
- review should be adversarial;
- waiting on one task need not block others.

Avoid parallel work when multiple agents would mutate the same critical state without coordination.

## Separation of duties

For material production or capital-sensitive changes, separate at least these responsibilities where practical:

- author;
- reviewer;
- deployer;
- economic verifier;
- Governor authorization.

The same agent may perform multiple non-critical roles during bootstrap, but the Root Agent must explicitly identify the reduced independence and compensate with stronger tests or later review.

## Specialist output

Every specialist returns:

1. conclusion;
2. evidence;
3. confidence;
4. unresolved uncertainty;
5. changed artifacts;
6. tests/checks performed;
7. risks;
8. recommended next action;
9. whether parent acceptance criteria were met.

Outputs must be persisted when they affect future decisions.

## Failure behavior

A failed specialist task is evidence, not wasted work.

The Root Agent should determine whether failure came from:

- false hypothesis;
- insufficient evidence;
- implementation defect;
- unavailable dependency;
- inadequate permissions;
- unrealistic requirements;
- incorrect decomposition;
- environmental failure.

Then update state and reprioritize.