# Specialist Task Contract

Use this template whenever the Root Agent delegates work to a specialist agent or scoped sub-job.

## Assignment

**Task ID:** `<stable-id>`

**Parent objective ID:** `<objective-id>`

**Role:** `<specialist-role>`

**Priority:** `<critical|high|normal|low>`

## Question / deliverable

State exactly what must be learned, proven, built, reviewed, or changed.

## Why this matters

Explain how the task advances the parent objective and mission.

## Context

Provide only the relevant current state, known evidence, prior decisions, interfaces, and constraints.

## Allowed actions

State what the specialist may inspect, create, modify, execute, test, or deploy.

## Prohibited actions

State what is outside scope. Include production, capital, secrets, Governor changes, destructive operations, or scope expansion when relevant.

## Required evidence

Specify the evidence needed to support the result: primary sources, tests, simulation outputs, metrics, diffs, logs, datasets, calculations, or reproducible commands.

## Acceptance criteria

Define the conditions for SUCCESS, FAILURE, or INCONCLUSIVE.

## Output contract

Return:

1. status: `SUCCESS | FAILURE | INCONCLUSIVE | BLOCKED`;
2. concise conclusion;
3. evidence and locations;
4. confidence;
5. assumptions;
6. unresolved uncertainty;
7. files/artifacts changed;
8. tests/checks performed;
9. risks introduced or discovered;
10. recommended next action;
11. whether parent acceptance criteria were met.

## Persistence

Material findings must be written to the repository/state system, not left only in transient conversation context.

## Scope rule

Do not silently broaden the assignment. If another valuable task is discovered, record it as a proposed follow-up for Root Agent prioritization unless immediate action is required to prevent harm.