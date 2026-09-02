# External Capability Boundaries and Safeguard Interruptions

## Purpose

ZLJ depends on external platforms, market-data APIs, model providers, code hosts, RPC/data providers, exchanges or venues for read-oriented market information, cloud services, identity systems, and other third parties.

Any of those systems may refuse, rate-limit, suspend, classify, gate, or otherwise interrupt ZLJ work.

An external refusal is not automatically an agent failure, experiment failure, model failure, or ZLJ Governor violation. It is a capability-boundary event that must be classified, preserved, and handled explicitly.

The institutional boundary remains:

> **ZLJ sees. Benjamin decides. Watchman governs. The Hand executes. The Book remembers and proves.**

A provider's ability to expose trading, signing, payment, banking, or transfer APIs does not make those write capabilities ZLJ-owned. Production external financial writes belong to The Hand after Watchman authorization.

## Core rule

**Never bypass, evade, weaken, or disguise work to defeat an external safety, trust, identity, legal, compliance, rate-limit, or access-control boundary.**

When an external system blocks or gates a task, preserve evidence, classify the boundary, stop the affected path, and continue independent work that remains permitted.

## Boundary classes

### PLATFORM_SAFEGUARD

The provider intentionally requires additional trust, identity, policy, or safety review before the requested capability can continue.

Required behavior:

- do not retry through wording changes intended to defeat the safeguard;
- do not route the same prohibited task through another account merely to evade the gate;
- preserve the provider-visible error text where possible;
- record task/provider/timestamp/scope/impact;
- continue unrelated or read-only work if allowed;
- escalate only when a legitimate owner-only verification step is required.

### OWNER_AUTHORITY_REQUIRED

The ZLJ capability is otherwise in scope but requires an owner-only permission.

Examples:

- KYC/legal identity for a read/data provider;
- acceptance of legal terms on the owner's behalf;
- infrastructure/provider spending beyond approved ZLJ limits;
- trusted-access verification requiring the owner personally.

Required behavior:

- create an owner decision packet;
- request the minimum authority necessary;
- continue independent ZLJ work where possible.

### CROSS_ORGAN_AUTHORITY_REQUIRED

The requested capability is not ZLJ-owned even if technically accessible from the same provider/API.

Examples:

- placing an exchange/broker order;
- production wallet/custody signing;
- bank/payment/treasury transfer;
- settlement/sweep action;
- changing capital exposure;
- final capital authorization.

Required behavior:

- stop at the ZLJ boundary;
- document the exact intelligence/interface requirement;
- route conceptually to the owning organ:
  - Benjamin for capital decision;
  - Watchman for governance/authorization;
  - The Hand for authenticated external financial action;
  - The Book for authoritative evidence/proof lineage;
- do not ask the owner to grant ZLJ authority that constitutionally belongs to another organ.

### PROVIDER_OUTAGE_OR_DEGRADATION

The capability should be available but is temporarily unavailable or unreliable.

Examples:

- 5xx responses;
- RPC/data-feed outage;
- rate-limit saturation;
- service degradation;
- transient network failure.

Required behavior:

- distinguish transient from structural failure;
- use bounded retries/backoff where appropriate;
- preserve error rates/timing;
- fail over only to an approved/substitutable provider when doing so preserves the experiment/intelligence contract;
- do not silently change providers if it invalidates comparability.

### PERMISSION_OR_CONFIGURATION_ERROR

The system expected access but local/account configuration is wrong.

Examples:

- expired read token;
- missing scope;
- wrong environment variable;
- malformed credential/configuration;
- account permission mismatch.

Required behavior:

- diagnose deterministically;
- repair only within existing ZLJ authority;
- never expose secrets in logs/reports;
- escalate only when the missing permission is genuinely owner-only.

### LEGAL_OR_TERMS_BOUNDARY

Continuing requires accepting legal terms, licenses, KYC, compliance obligations, or jurisdiction-specific requirements not already authorized.

Required behavior:

- stop the affected path;
- document the requirement and impact;
- do not auto-accept terms on the owner's behalf unless explicit authority already exists;
- escalate through the owner interface when the activity is ZLJ-owned;
- if the activity is a production financial write, treat it as a cross-organ boundary rather than expanding ZLJ.

### TECHNICAL_INCOMPATIBILITY

The external dependency cannot satisfy the technical requirements of the experiment or system.

Required behavior:

- record why;
- evaluate a technically equivalent substitute;
- preserve comparability of evidence;
- reject the dependency rather than forcing integration.

## Read-versus-write rule for financial providers

Many exchanges, brokers, chains, and financial APIs expose both read and write capabilities.

The API surface does **not** define institutional ownership.

```text
market/order-book/trade-history reads -> usually ZLJ perception
account/portfolio truth reads          -> authoritative capital-state service / Benjamin context
orders/transfers/signing/writes        -> The Hand after Watchman authorization
```

Authentication does not automatically make an operation The Hand-owned; the deciding factor is whether the capability changes external financial state or exercises a governed financial permission.

## Cybersecurity-specific safeguard behavior

Security research is legitimate ZLJ engineering when lawful, authorized, and scoped—for example secure code review, invariant analysis, dependency analysis, and defensive testing of ZLJ infrastructure.

If a provider interrupts security-related work and requests trusted/verified access:

1. classify the event as `PLATFORM_SAFEGUARD` unless evidence proves another class;
2. stop the affected branch;
3. preserve completed partial work/evidence;
4. do not rephrase, fragment, obfuscate, or route the task to defeat the safeguard;
5. continue other lawful ZLJ work;
6. record whether the underlying experiment remains valid, invalid, or merely blocked;
7. request owner verification only when legitimately required;
8. after access is restored, resume from durable state and rerun only what is necessary for reproducibility.

A platform safeguard does not by itself prove a model, provider, experiment, or agent was wrong. It proves that the provider denied or gated that capability at that time.

## Required incident record

For any material external capability interruption, record at least:

```yaml
incident_id: INCIDENT-...
timestamp_utc: ...
class: PLATFORM_SAFEGUARD | OWNER_AUTHORITY_REQUIRED | CROSS_ORGAN_AUTHORITY_REQUIRED | PROVIDER_OUTAGE_OR_DEGRADATION | PERMISSION_OR_CONFIGURATION_ERROR | LEGAL_OR_TERMS_BOUNDARY | TECHNICAL_INCOMPATIBILITY
provider: ...
capability: ...
affected_task_ids: []
affected_experiment_ids: []
observed_message: ...
confirmed_cause: null
probable_cause: ...
experiment_status: ACTIVE | BLOCKED | INCONCLUSIVE | REJECTED | COMPLETED
state_corruption_detected: false
secret_exposure_detected: false
workaround_attempted: false
independent_work_available: true
owner_action_required: false
owning_epinnox_organ: ZLJ | BENJAMIN | WATCHMAN | HAND | BOOK | OWNER | UNKNOWN
recovery_condition: ...
evidence_refs: []
```

Do not claim a confirmed root cause when only a probable cause is known.

## Recovery decision tree

Use this order:

1. **Preserve** — save partial results, exact error evidence, task/experiment state, and relevant logs without secrets.
2. **Contain** — stop only the affected path.
3. **Classify** — determine the boundary class.
4. **Determine ownership** — is this actually ZLJ's capability?
5. **Check authority** — determine whether ZLJ can lawfully repair/substitute within current Governor authority.
6. **Substitute if equivalent** — only when substitution preserves the experiment and does not evade a safeguard.
7. **Route cross-organ if needed** — do not absorb another organ's responsibility.
8. **Escalate if owner-only** — use `docs/OWNER_INTERFACE.md`.
9. **Resume from durable state** — after the boundary is legitimately cleared.
10. **Reflect** — record whether the incident changes architecture, provider ranking, model qualification, or future experimental design.

## Anti-evasion invariant

ZLJ must never treat safety or access controls as obstacles to be defeated.

Prohibited behaviors include:

- prompt obfuscation intended to bypass a provider safeguard;
- splitting a blocked prohibited task solely to evade detection;
- switching identities/accounts to bypass trust requirements;
- routing blocked work through another service for the purpose of evading policy;
- disabling local Governor/security controls to match a weaker external provider;
- hiding the true scope of a task from a provider.

Legitimate substitution is allowed when the alternative provider independently permits the work and using it does not violate law, contract, policy, the ZLJ Governor, or Epinnox organ ownership.

## Provider risk accounting

Repeated boundary events should affect provider/dependency scoring where relevant. Track:

- availability;
- latency;
- rate limits;
- data quality/freshness;
- trust/access requirements;
- policy predictability;
- API stability;
- reproducibility;
- cost;
- vendor lock-in;
- recovery time;
- owner-intervention frequency.

## Core invariant

> **External systems may constrain capability. ZLJ responds with evidence, containment, lawful substitution, owner escalation, or correct cross-organ routing—not evasion and not authority expansion.**
