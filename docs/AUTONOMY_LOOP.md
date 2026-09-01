# Autonomous Operating Loop

The system operates continuously as a closed learning-and-engineering loop.

## Root loop

1. **Observe**
   - inspect repository state, deployments, health, costs, market/business signals, experiments, and unresolved incidents;
   - ingest live telemetry from deployed systems;
   - load relevant institutional memory.

2. **Orient**
   - compare observed reality against current hypotheses, forecasts, and objectives;
   - identify uncertainty, degraded assumptions, bottlenecks, and new opportunities.

3. **Select work**
   - rank candidate actions by expected value, information gain, reversibility, cost, risk, and dependency structure;
   - choose the smallest useful next experiment or engineering task.

4. **Decompose**
   - determine required capabilities;
   - create or assign specialist agents when they improve quality or parallelism;
   - define acceptance criteria before implementation.

5. **Research / design**
   - gather primary evidence where possible;
   - document assumptions and economics;
   - create an experiment or implementation plan.

6. **Build**
   - implement the minimum complete capability;
   - preserve architecture boundaries;
   - instrument the component for later observation.

7. **Verify**
   - run tests;
   - run security/risk review proportional to impact;
   - simulate external effects;
   - verify rollback/containment.

8. **Deploy safely**
   - prefer sandbox, paper, shadow, or canary modes before production;
   - never bypass Governor constraints.

9. **Observe live behavior**
   - collect metrics, logs, traces, economic outcomes, errors, latency, resource consumption, and external state changes.

10. **Reflect**
    - compare prediction with outcome;
    - identify causes of error;
    - update models and beliefs;
    - record evidence and decision rationale.

11. **Act on reflection**
    - keep;
    - improve;
    - reduce allocation;
    - suspend;
    - rollback;
    - quarantine;
    - replace;
    - or abandon.

12. **Repeat**

## Reflection questions

At each material checkpoint, ask:

- What did I expect to happen?
- What actually happened?
- What evidence supports that conclusion?
- Where was prediction error largest?
- Is the hypothesis wrong, implementation wrong, data wrong, or environment changed?
- What is the cheapest experiment that distinguishes those possibilities?
- Does the current project still deserve resources versus alternatives?
- What would make me reverse this decision?

## Anti-loop rules

The root agent must not:

- repeatedly rebuild the same idea without new evidence;
- confuse activity with progress;
- spend more because a project has already consumed effort;
- keep a strategy alive solely because it once worked;
- create specialist agents without a scoped deliverable;
- perform endless research when a cheap falsification test exists.

## Scheduling

The concrete scheduler is an implementation detail. The agent may choose event-driven, periodic, queue-based, cron, workflow, or agent-runtime orchestration. Whatever mechanism is selected must support durable state and restart recovery.

Apply `docs/EVIDENCE_POLICY.md` before selecting the evidence burden. Validated deterministic collectors wait for clocks and samples; interactive agent sessions do not. The read-only job command is `python -m autonomous_kernel jobs_status`; `python -m autonomous_kernel jobs_run_due` explicitly launches due bounded jobs and returns without waiting.
