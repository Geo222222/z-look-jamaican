# Product Engineering Skill

Use this skill for architecture, implementation, testing, refactoring, and production-readiness work.

## Default posture

- Treat every component as part of a real autonomous production system.
- Read existing code and architecture before changing it.
- Preserve unrelated work.
- Prefer narrow, reviewable changes with explicit acceptance criteria.
- Build the smallest complete capability that answers the current uncertainty.
- Instrument what you build so the root agent can observe it later.

## Architecture principles

Maintain clear boundaries between:
- AI control plane;
- deterministic execution plane;
- Governor enforcement;
- data/telemetry plane;
- persistence/memory.

Do not put LLM calls on latency-critical deterministic execution paths unless the task truly requires probabilistic reasoning and failure is contained.

## Containerization

Prefer containerized services for reproducibility and isolation. During bootstrap, Docker Compose is an acceptable default when it keeps the system simple. Do not introduce Kubernetes or distributed infrastructure before justified by operational need.

Separate privileges:
- builder/test agents may modify source and run sandboxes;
- production executors should run immutable/versioned artifacts;
- production signing/financial credentials must not be exposed to general-purpose coding agents;
- observability readers should prefer read-only access.

## Implementation requirements

For material components include:
- typed/configurable interfaces where practical;
- tests;
- structured errors;
- health/readiness checks for services;
- structured logging;
- metrics for important operations;
- deterministic configuration validation;
- documented environment variables without committed secrets;
- rollback/compatibility considerations.

## Change lifecycle

`understand → define acceptance criteria → implement → test → security/risk check → build immutable artifact → deploy safely → observe → reflect`

Code existing in the repository is not proof of completion.

## Dependencies

Prefer well-maintained libraries and official protocol/provider SDKs when they reduce risk. Pin or constrain dependencies appropriately. Record material external dependencies and upgrade assumptions.

## Testing ladder

Use the lowest-cost meaningful layers first:
- unit;
- contract/interface;
- integration;
- deterministic simulation;
- historical replay/fork testing when appropriate;
- shadow/canary;
- production observation.

## Output

Engineering work must leave behind:
- implementation;
- tests;
- relevant docs;
- operational instrumentation;
- evidence of verification;
- clear remaining limitations.
