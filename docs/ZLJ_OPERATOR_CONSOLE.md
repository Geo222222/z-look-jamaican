# ZLJ Operator Console

The ZLJ Operator Console is the authoritative human operating surface for Z Look Jamaican. It is intentionally more than a dashboard: it exposes system state, data quality, evidence lineage, certification truth, and governed operator requests without granting the browser authority owned by ZLJ domain services, Benjamin, Watchman, Hand, or Book.

## Institutional boundary

```text
browser
  ↓ request / inspect
ZLJ Operator API
  ↓ typed operator contract
autonomous_kernel.operator
  ↓ domain validation / service
Z1–Z9 durable state + journals
  ↓
ZLJ intelligence (Z10 later)
  ↓
Benjamin decides → Watchman governs → Hand executes → Book proves
```

The console has **no capital authority** and **no execution authority**. `LIVE_EXECUTION`, `CAPITAL_AUTHORIZATION`, and `ORDER_PLACEMENT` are constitutionally locked controls. Rendering a control never creates authority.

## Visual semantics

The interface is deliberately futuristic, but visual emphasis must encode real state rather than decoration:

- green = supported, valid, certified, or available;
- cyan/blue = constructed, observable, or read-only informational state;
- amber = blocked, gated, operator mutation mode, or evidence prerequisite;
- red = not earned, invalid, or constitutionally locked;
- muted = unavailable, no durable runtime records, or not yet constructed.

The console must never fabricate system-health percentages, model confidence, data coverage, or certification status. Unknown evidence is rendered as unknown; absent evidence is rendered unavailable or data-blocked.

## Information architecture

The navigation is organized by operator meaning, not historical build order.

```text
SYSTEM
└── Overview

PERCEPTION
├── Z1 Observations
├── Z2 Representations
└── Z9 Market Context

INTELLIGENCE
├── Z3 Predictions
├── Z4 Models
├── Z5 Lifecycle
├── Z8 Assembly
└── Z10 Intelligence       future

EVALUATION
├── Z6 Outcomes
└── Z7 Competence

GOVERNANCE
├── Certification
└── Evidence & Lineage

OPERATIONS
├── Control Center
└── Development

INSTITUTION
└── Benjamin Bridge
```

Every Z-stage page follows the same operator contract:

1. **Purpose** — why the stage exists.
2. **Live state** — durable runtime state actually present.
3. **Inputs** — authoritative evidence consumed.
4. **Outputs** — durable artifacts produced.
5. **Quality** — data and causal constraints that qualify the output.
6. **Controls** — only governed commands that actually exist.
7. **Evidence** — source paths, hashes, journals and receipts.
8. **Certification** — what has and has not been earned.
9. **Lineage** — where the stage sits in the causal chain.
10. **Benjamin benefit** — why the stage improves downstream decision intelligence.

## Operator command contract

The stable command catalog is owned by `autonomous_kernel.operator.contracts`, not JavaScript. A command has:

- command ID;
- domain;
- control class (`READ_ONLY`, `MUTATING`, `CONSTITUTIONALLY_LOCKED`);
- state (`AVAILABLE`, `UNAVAILABLE`, `LOCKED`);
- confirmation requirement;
- typed parameter names;
- system/Benjamin benefit.

The browser may execute only `/api/control/execute`; the FastAPI layer forwards the request to `python -m autonomous_kernel operator_command`. The web server never mutates a Z-stage state file directly.

### Mutation gate

Mutating commands require `ZLOOK_OPERATOR_MUTATIONS_ENABLED=true` to be set **outside the UI**. The current read-only monitor deployment remains mounted read-only and does not set that flag. A browser cannot enable operator mutation mode.

### Idempotency

Every mutating request carries a unique `request_id`. The append-only operator journal records it. A retry of the same request ID returns the original receipt and does not execute the operation twice.

### Receipts

Mutating command receipts record:

- request ID;
- command ID and control class;
- start/completion time;
- parameters;
- domain result;
- `capital_effect = NONE`;
- `execution_effect = NONE`.

Receipts are hash chained in `memory/operator_commands.jsonl`. The chain is part of normal kernel validation.

## Initial executable controls

The first console slice exposes only backend operations that already exist and are governed:

- `VALIDATE_KERNEL` — read-only full durable-state validation;
- `MATERIALIZE_CONTEXT` — Z9 materialization from durable Z2 history at cutoff T;
- `RECOVER_PENDING` — idempotent recovery of prepared kernel state.

Controls planned for later backend phases are visible as `UNAVAILABLE`, not simulated:

- freeze current state;
- launch a qualification campaign;
- activate a context profile from the console;
- Git-backed code-change proposal.

## Source-code control direction

The Development page is a future Git-backed engineering surface. The intended workflow is:

```text
view source
  ↓
propose patch
  ↓
review diff
  ↓
run tests
  ↓
run qualification
  ↓
commit / branch
  ↓
PR / promotion
```

The browser will not become an unversioned production Python editor.

## Data-flow story

The Overview page tells one coherent story:

```text
Z1 canonical facts
 ↓
Z2 point-in-time state
 ↓
Z3 falsifiable predictions
 ↓
Z4 model hypotheses
 ↓
Z5 lifecycle authority
 ↓
Z6 realized truth
 ↓
Z7 measured competence
 ↓
Z8 evidence-weighted belief
 ↓
Z9 market-wide context
 ↓
Z10 ZLJ.INTELLIGENCE       future
 ↓
Benjamin
```

The frozen Z8 `NOT_EARNED` historical result remains visible because the operator console is an evidence surface, not a success-only marketing surface.
