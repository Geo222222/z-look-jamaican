# Root Agent State Machine

## Purpose

The ZLJ Root Agent must know what mode it is in and what transitions are allowed. This prevents endless undifferentiated activity and prevents a ZLJ research/model stage from being mistaken for Epinnox capital authority.

> **ZLJ sees. Benjamin decides. Watchman governs. The Hand executes. The Book remembers and proves.**

## Operating states

### BOOTSTRAP
Build the minimum durable ZLJ operating kernel required to work autonomously.

Exit when the agent can persist state, create work, record evidence, run tests/experiments, inspect market/data/model state, and recover after interruption.

### DISCOVERY
Search for decision-relevant market questions, data gaps, model opportunities, and enabling perception capabilities.

Exit individual candidates into RESEARCH when they have a plausible mechanism and falsifiable question.

### RESEARCH
Collect enough current evidence to define the mechanism, horizon, data requirements, timing constraints, uncertainty, and cheapest useful test.

Transition to EXPERIMENT, REJECTED, or PARKED.

### EXPERIMENT
Run bounded tests that answer a specific market/data/model uncertainty.

Transition to BUILD, another EXPERIMENT, REJECTED, or PARKED.

### BUILD
Construct the minimum reliable ZLJ capability required for the validated next stage.

Transition to VALIDATE when implementation acceptance criteria pass.

### VALIDATE
Test correctness, provenance, timing integrity, predictive evidence, failure behavior, security, calibration, and operational readiness appropriate to the capability.

Transition to DEPLOY, BUILD, REJECTED, or SUSPENDED.

### DEPLOY
Release a qualified ZLJ data/model capability into its allowed environment with rollback/quarantine and observability.

Deployment means the capability may produce intelligence. It does not mean ZLJ may move capital.

Transition immediately to OBSERVE.

### OBSERVE
Collect real data/model behavior, health, errors, latency, prediction outcomes when labels become knowable, calibration, drift, and resource use.

Transition to REFLECT on cadence/material event. Transition to INCIDENT immediately on dangerous anomalies.

### REFLECT
Compare expectation with evidence, update ZLJ models/memory/competence, and decide whether to KEEP, IMPROVE, RECALIBRATE, DEMOTE, SUSPEND, QUARANTINE, REPLACE, or REJECT.

Transition to the state implied by that decision.

### INCIDENT
Contain harm, preserve evidence, restore safe ZLJ operation, diagnose root cause, validate remediation, and create a postmortem.

Transition to BUILD, VALIDATE, OBSERVE, SUSPENDED, or owner/cross-organ escalation.

### SUSPENDED
A ZLJ capability/model is intentionally inactive while evidence, repair, qualification, provider recovery, or changed conditions are awaited.

### PARKED
Potentially useful work currently dominated by higher-value ZLJ priorities.

### REJECTED
Current evidence says the hypothesis/model/capability should not consume more work under present conditions. Store reopening criteria.

## Intelligence qualification sub-states

Market hypotheses and models use the progression defined by `docs/ZERO_TO_REVENUE.md` (historical filename):

`DISCOVERY -> RESEARCH -> REPLAY/BACKTEST -> SHADOW -> ZLJ_READY -> BENJAMIN_SHADOW -> GOVERNED_EPINNOX_USE -> SCALE_INTELLIGENCE`

Interpret the later stages correctly:

- `ZLJ_READY` means a data/model capability is qualified to produce intelligence.
- `BENJAMIN_SHADOW` means Benjamin may consume it in shadow decisions.
- `GOVERNED_EPINNOX_USE` means any real capital use occurs only through Benjamin -> Watchman -> The Hand.
- No ZLJ state grants production wallet, signing, order, transfer, or settlement authority.

## Required transition evidence

Every non-trivial transition records:

- previous state;
- new state;
- trigger;
- evidence;
- decision/experiment/model ID;
- responsible ZLJ actor;
- instrument/horizon where relevant;
- rollback, demotion, or reopening condition.

## Progress rule

Do not confuse state transitions with success. A rapid transition to REJECTED after a cheap decisive falsification can be better than months spent in BUILD.

Likewise, promotion of a ZLJ model is evidence that the model earned a perception role—not evidence that Benjamin, Watchman, or The Hand may be bypassed.
