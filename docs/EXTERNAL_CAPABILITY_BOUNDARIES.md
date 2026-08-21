# External Capability Boundaries and Safeguard Interruptions

## Purpose

The autonomous system depends on external platforms, APIs, model providers, code hosts, RPC providers, exchanges, cloud services, identity systems, and other third parties. Any of those systems may refuse, rate-limit, suspend, classify, gate, or otherwise interrupt work.

An external refusal is not automatically an agent failure, experiment failure, Governor violation, or product failure. It is a capability-boundary event that must be classified, preserved, and handled explicitly.

This policy exists to prevent three bad behaviors:

1. treating every external block as proof the underlying experiment failed;
2. wasting time repeatedly retrying a blocked path without new information;
3. attempting to evade or work around a legitimate platform safeguard.

## Core rule

**Never bypass, evade, weaken, or disguise work to defeat an external safety, trust, identity, legal, compliance, rate-limit, or access-control boundary.**

When an external system blocks or gates a task, preserve evidence, classify the boundary, stop the affected path, and continue independent work that remains permitted.

## Boundary classes

Classify every material external interruption as one of the following.

### PLATFORM_SAFEGUARD
The provider intentionally requires additional trust, identity, policy, or safety review before the requested capability can continue.

Examples:
- cybersecurity trust/identity verification;
- model-provider safety gating;
- code-host abuse protection;
- platform policy classification;
- human-verification requirements.

Required behavior:
- do not retry through wording changes intended to defeat the safeguard;
- do not route the same prohibited task through another account merely to evade the gate;
- preserve the exact user-visible/provider-visible error text where possible;
- record the interrupted task, provider, timestamp, scope, and impact;
- continue unrelated or read-only work if allowed;
- escalate only if the owner must personally complete a legitimate verification or authorization step.

### AUTHORITY_REQUIRED
The capability is technically available but requires an owner-only permission defined in `docs/GOVERNOR.md` or `docs/OWNER_INTERFACE.md`.

Examples:
- KYC/legal identity;
- non-zero production capital authorization;
- new paid account commitment beyond spend authority;
- privileged production credential unavailable to the agent.

Required behavior:
- create an owner decision packet;
- request the minimum authority necessary;
- continue all independent work while waiting.

### PROVIDER_OUTAGE_OR_DEGRADATION
The capability should be available but is temporarily unavailable or unreliable.

Examples:
- 5xx responses;
- RPC outage;
- rate-limit saturation;
- service degradation;
- transient network failure.

Required behavior:
- distinguish transient from structural failure;
- use bounded retries with backoff where appropriate;
- preserve error rates and timing;
- fail over only to an approved/substitutable provider when doing so preserves the experiment;
- do not silently change providers if that would alter the economic or technical hypothesis.

### PERMISSION_OR_CONFIGURATION_ERROR
The system expected access but local or account configuration is wrong.

Examples:
- expired token;
- missing scope;
- wrong environment variable;
- malformed webhook secret;
- account permission mismatch.

Required behavior:
- diagnose deterministically;
- repair only within existing authority;
- never expose secrets in logs or reports;
- escalate only when the missing permission is genuinely owner-only.

### LEGAL_OR_TERMS_BOUNDARY
Continuing requires accepting legal terms, licenses, KYC, compliance obligations, or jurisdiction-specific requirements not already authorized.

Required behavior:
- stop the affected external-write path;
- document the requirement and business impact;
- do not auto-accept terms on the owner’s behalf unless explicit authority already exists;
- escalate through the owner interface.

### TECHNICAL_INCOMPATIBILITY
The external dependency cannot satisfy the technical requirements of the experiment or system.

Required behavior:
- record why;
- evaluate a technically equivalent substitute;
- preserve comparability of evidence;
- reject the dependency rather than forcing integration.

## Cybersecurity-specific safeguard behavior

Security research is a legitimate part of autonomous economic engineering when it is lawful, authorized, and scoped. Examples include secure code review, invariant analysis, vulnerability validation, smart-contract review, dependency analysis, and defensive testing.

However, model or platform providers may apply additional safeguards to cybersecurity work.

If a provider interrupts security-related work and requests trusted/verified access:

1. classify the event as `PLATFORM_SAFEGUARD` unless evidence proves another class;
2. stop the affected security-review branch;
3. preserve completed partial work and evidence;
4. do not rephrase, fragment, obfuscate, or route the task in order to defeat the safeguard;
5. continue other lawful work that does not require the blocked capability;
6. record whether the experiment itself remains valid, invalid, or merely blocked;
7. if owner verification is legitimately required, create a concise decision packet rather than a vague request;
8. after access is restored, resume from durable state and re-run only the interrupted scope necessary for reproducible evidence.

A platform safeguard does **not** by itself prove that:
- the reviewed code is unsafe;
- the experiment failed;
- the agent violated policy;
- the opportunity should be rejected.

It proves only that the provider denied or gated that capability at that time.

## Required incident record

For any material external capability interruption, create or update an incident record with at least:

```yaml
incident_id: INCIDENT-...
timestamp_utc: ...
class: PLATFORM_SAFEGUARD | AUTHORITY_REQUIRED | PROVIDER_OUTAGE_OR_DEGRADATION | PERMISSION_OR_CONFIGURATION_ERROR | LEGAL_OR_TERMS_BOUNDARY | TECHNICAL_INCOMPATIBILITY
provider: ...
capability: ...
affected_task_ids: []
affected_experiment_ids: []
observed_message: ...
confirmed_cause: null
probable_cause: ...
agent_fault: CONFIRMED | NOT_CONFIRMED | NOT_APPLICABLE
experiment_status: ACTIVE | BLOCKED | INCONCLUSIVE | REJECTED | COMPLETED
state_corruption_detected: false
capital_or_secret_exposure_detected: false
workaround_attempted: false
independent_work_available: true
owner_action_required: false
recovery_condition: ...
evidence_refs: []
```

Do not claim a confirmed root cause when only a probable cause is known.

## Recovery decision tree

Use this order:

1. **Preserve** — save partial results, exact error evidence, task/experiment state, and relevant logs without secrets.
2. **Contain** — stop only the affected path; do not unnecessarily halt unrelated work.
3. **Classify** — determine the boundary class.
4. **Check authority** — determine whether the agent can lawfully repair/substitute within current Governor authority.
5. **Substitute if equivalent** — only when the substitute preserves the experiment and does not evade a safeguard.
6. **Escalate if owner-only** — use `docs/OWNER_INTERFACE.md`.
7. **Resume from durable state** — after the boundary is legitimately cleared.
8. **Reflect** — record whether the incident changes architecture, provider ranking, opportunity economics, or future experimental design.

## Anti-evasion invariant

The autonomous system must never treat safety or access controls as obstacles to be defeated.

Prohibited behaviors include:
- prompt obfuscation intended to bypass a provider safeguard;
- splitting one blocked prohibited task into smaller calls solely to evade detection;
- switching identities/accounts to bypass trust requirements;
- routing blocked work through another service when the purpose is to evade a policy decision rather than use a legitimately permitted equivalent capability;
- disabling local Governor/security controls to match a weaker external provider;
- hiding the true scope of a task from a provider.

Legitimate substitution is allowed when the alternative provider independently permits the work and using it does not violate law, contract, policy, or the Governor.

## Provider risk accounting

Repeated boundary events should affect provider/dependency scoring.

Track where relevant:
- availability;
- latency;
- rate limits;
- trust/access requirements;
- policy predictability;
- security-review suitability;
- API stability;
- reproducibility;
- cost;
- vendor lock-in;
- recovery time;
- owner-intervention frequency.

A provider that repeatedly blocks mission-critical lawful work may be an operationally poor dependency even when technically capable.

## Current known example

A Codex security-review workflow involving smart-contract review, invariant scanning, EIP-7702 code, and adjudicated security findings was interrupted by a product message stating that some cybersecurity requests require additional safeguards and requesting Trusted Access verification.

Until stronger evidence exists, classify that event as:

- class: `PLATFORM_SAFEGUARD`;
- confirmed cause: unknown;
- probable cause: cybersecurity-sensitive task classification;
- state corruption: not observed;
- capital movement: none observed;
- secret exposure: none observed;
- experiment conclusion: not determined by the safeguard itself;
- correct behavior: preserve evidence, stop the affected review branch, continue unrelated work, and use the legitimate trust-verification path if owner participation is required.

## Invariant

**External systems may constrain capability. The Root Agent must respond with evidence, containment, lawful substitution, or owner escalation—not evasion.**
