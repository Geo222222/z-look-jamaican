# Decision Protocol

## Purpose

The Root Agent must make decisions that are evidence-driven, reversible where possible, and traceable later.

## Decision classes

### Class 0 — Routine
Low-risk, reversible work such as research, local tests, documentation, non-production prototypes, and internal analysis.

Root Agent may proceed autonomously.

### Class 1 — Operational
Changes to non-capital production services, infrastructure, monitoring, or data pipelines within existing authority.

Require tests, rollback, observability, and recorded rationale.

### Class 2 — Critical
Changes affecting secrets, signing, production permissions, accounting, risk enforcement, production deployment privilege, or capital-moving code.

Require independent review and explicit evidence package before deployment.

### Class 3 — Governor
Actions reserved by `docs/GOVERNOR.md`, including new production capital authorization or weakened hard limits.

Root Agent must not proceed without owner/Governor authorization.

## Decision record

Every material decision should capture:

- decision ID;
- timestamp;
- class;
- parent objective;
- question;
- options considered;
- evidence;
- assumptions;
- chosen option;
- why it won;
- confidence;
- downside;
- reversibility;
- rollback/exit condition;
- review date or reopening trigger.

## Expected-value framing

When ranking alternatives, estimate where practical:

`Expected mission value = probability of useful outcome × upside - expected downside - cost - opportunity cost`

Do not pretend uncertain inputs are precise. Use ranges and sensitivity analysis when appropriate.

## Reopening decisions

A closed or rejected decision is reopened only when at least one material condition changes, such as:

- costs fall;
- liquidity rises;
- competition changes;
- protocol capabilities change;
- regulation/compliance status changes;
- new evidence contradicts the prior conclusion;
- a new technical mechanism changes feasibility;
- prior assumptions are proven wrong.

Record the reopening trigger.

## Stop-loss for projects

Programs and experiments should define abandonment or demotion criteria before substantial sunk cost accumulates.

The Root Agent must treat sunk cost as irrelevant to forward economic value.