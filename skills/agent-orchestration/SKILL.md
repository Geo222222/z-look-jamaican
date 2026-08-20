# Agent Orchestration Skill

Use this skill when the root agent must decompose work, create specialist roles, coordinate parallel work, or decide whether an agent should continue, stop, or hand off.

## Root principle

Specialist agents exist to reduce uncertainty or complete scoped deliverables. They are not permanent bureaucracy.

## When to spawn a specialist

Create a specialist when one or more are true:
- the task requires domain expertise materially different from the current reasoning context;
- independent review improves safety or correctness;
- parallel work shortens a critical path without creating merge/conflict risk;
- the task needs a bounded execution environment or narrower permissions;
- adversarial review is valuable.

## Specialist task contract

Every delegated task must include:
- objective;
- relevant context/evidence;
- explicit scope;
- constraints and Governor boundaries;
- expected output artifact;
- acceptance criteria;
- stop conditions;
- required evidence.

Do not delegate vague instructions such as “make it better.”

## Useful roles

Roles may be created dynamically, including:
- opportunity researcher;
- protocol researcher;
- quantitative analyst;
- data engineer;
- software architect;
- implementation engineer;
- smart-contract reviewer;
- security adversary;
- test engineer;
- SRE/operator;
- economic auditor;
- incident investigator.

Roles are capabilities, not authority tiers. None may override the Governor.

## Review separation

For high-impact work, avoid having the same specialist be the sole author and sole approver. Use independent checks for:
- capital-bearing strategy promotion;
- signer/wallet changes;
- Governor enforcement code;
- production deployment permissions;
- security-sensitive infrastructure.

## Synthesis

The root agent must reconcile conflicting specialist outputs by:
1. comparing evidence quality;
2. identifying differing assumptions;
3. running a discriminating experiment where practical;
4. recording the final decision and uncertainty.

## Agent lifecycle

Specialists should terminate or become dormant when their scoped deliverable is complete. Persist their outputs and evidence, not unnecessary conversational state.

## Failure handling

If an agent stalls, loops, or produces unsupported conclusions:
- stop the task;
- preserve useful artifacts;
- identify missing context/tooling;
- re-scope or replace the specialist;
- do not repeatedly retry the same prompt without changing the failure condition.
