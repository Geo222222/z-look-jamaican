# Root Agent State Machine

## Purpose

The Root Agent must know what mode it is in and what transitions are allowed. This prevents endless undifferentiated activity.

## Operating states

### BOOTSTRAP
Build the minimum durable operating kernel required to work autonomously.

Exit when the agent can persist state, create work, record evidence, run tests/experiments, and inspect its own current system state.

### DISCOVERY
Search broadly for candidate opportunity classes and important enabling capabilities.

Exit individual candidates into RESEARCH when they have a plausible mechanism and a falsifiable question.

### RESEARCH
Collect enough current evidence to define the mechanism, economics, dependencies, risks, and cheapest useful test.

Transition to EXPERIMENT, REJECTED, or PARKED.

### EXPERIMENT
Run bounded tests that answer a specific uncertainty.

Transition to BUILD, another EXPERIMENT, REJECTED, or PARKED.

### BUILD
Construct the minimum reliable system required for the validated next stage.

Transition to VALIDATE when implementation acceptance criteria pass.

### VALIDATE
Test correctness, economics, failure behavior, security, and operational readiness at the appropriate stage.

Transition to DEPLOY, BUILD, REJECTED, or SUSPENDED.

### DEPLOY
Release an authorized version into its allowed environment with rollback and observability.

Transition immediately to OBSERVE.

### OBSERVE
Collect real behavior, health, economic outcomes, errors, latency, resource use, and prediction error.

Transition to REFLECT on cadence or material event. Transition to INCIDENT immediately on dangerous anomalies.

### REFLECT
Compare expectation with evidence, update models and memory, and decide whether to KEEP, IMPROVE, SCALE, DEMOTE, SUSPEND, or REJECT.

Transition to the state implied by that decision.

### INCIDENT
Contain harm, preserve evidence, restore safe operation, diagnose root cause, validate remediation, and create postmortem.

Transition to BUILD, VALIDATE, OBSERVE, SUSPENDED, or owner escalation.

### SUSPENDED
The system or strategy is intentionally inactive while evidence, repair, authorization, or changed conditions are awaited.

### PARKED
Potentially useful work that is currently dominated by higher-value priorities.

### REJECTED
Current evidence says the opportunity/system should not consume more work under present conditions. Store reopening criteria.

## Economic strategy sub-states

Financial strategies use the additional progression defined in `docs/ZERO_TO_REVENUE.md`:

`DISCOVERY -> RESEARCH -> REPLAY/BACKTEST -> SIMULATION -> SHADOW -> MICRO -> LIMITED -> PRODUCTION -> SCALE`

The Root Agent must preserve both organizational state and strategy stage.

## Required transition evidence

Every non-trivial transition records:

- previous state;
- new state;
- trigger;
- evidence;
- decision ID;
- owner/agent responsible;
- rollback or demotion condition where relevant.

## Progress rule

Do not confuse state transitions with success. A rapid transition to REJECTED after a cheap, decisive falsification can be better than months spent in BUILD.