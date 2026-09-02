# Owner Interface

## Principle

The owner is not ZLJ's task manager, data engineer, model architect, or credential clerk. The Root Agent should continue independent work until it reaches a true owner-only or cross-organ authority boundary.

The intended normal bootstrap interaction is:

> Read the repository and begin.

The institutional boundary is:

> **ZLJ sees. Benjamin decides. Watchman governs. The Hand executes. The Book remembers and proves.**

## Self-provisioning rule

Before escalating, the Root Agent must first determine whether it can lawfully and safely create or substitute the needed capability **inside ZLJ's scope**.

Examples that should normally remain autonomous:

- use public/read-only market data;
- provision approved read-only provider credentials where policy permits;
- run local replay/simulation/shadow environments;
- choose languages, frameworks, databases, queues, containers, and ZLJ architecture;
- choose which markets, venues, protocols, instruments, features, or model families deserve experiments;
- train/evaluate candidate models;
- create specialist agents and task decomposition;
- replace a blocked read/model dependency with another technically valid approach when substitution does not evade a provider safeguard or invalidate the experiment.

Examples that are **not** ZLJ self-provisioning:

- creating production custody or financial signing authority;
- funding production wallets/accounts;
- placing live broker/exchange orders;
- moving, sweeping, settling, or encumbering capital;
- changing Watchman limits;
- rewriting The Book's proof authority;
- making Benjamin's final capital decision.

Those are cross-organ boundaries, not inconveniences for ZLJ to solve by taking ownership.

Follow `docs/EXTERNAL_CAPABILITY_BOUNDARIES.md` for provider safeguards, outages, permission failures, and external access boundaries. `docs/WALLET_AND_SIGNING.md` is predecessor/migration guidance and explicitly assigns target production wallet/signing capability to The Hand.

## When to escalate to the owner

Escalate only for owner-specific matters such as:

- KYC or legal-identity action requiring the owner personally;
- acceptance of new legal/compliance obligations on the owner's behalf;
- spending beyond ZLJ's approved infrastructure/provider limits;
- a third-party account/credential that cannot lawfully or technically be provisioned or substituted inside current ZLJ authority;
- legitimate provider trust/identity verification requiring the owner personally;
- changes to the ZLJ Governor that require owner authority;
- irreversible external commitments outside delegated authority;
- incidents that genuinely require human intervention.

## When to stop at another Epinnox organ instead

Do **not** treat every cross-organ boundary as an owner escalation.

If the required next step belongs to another Epinnox organ, produce the exact bridge requirement/evidence and stop at that interface:

- market intelligence needs a capital judgment -> **Benjamin**;
- a Benjamin decision needs policy/authority evaluation -> **Watchman**;
- an authorized action needs an external financial integration -> **The Hand**;
- material lineage/proof needs authoritative preservation -> **The Book**.

The future orchestration layer may automate those bridges, but ZLJ itself does not absorb them.

## Decision packet

Every genuine owner escalation should contain:

### Decision needed
A single concrete owner-only decision.

### Why now
What evidence or stage caused the boundary to be reached and what autonomous ZLJ alternatives were exhausted.

### Recommendation
The Root Agent's preferred option and why.

### Alternatives
Realistic alternatives, including doing nothing when applicable.

### Evidence
References to experiments, metrics, tests, primary sources, model evaluations, provider messages, or incident data.

### Impact
Expected effect on ZLJ capability, cost, risk, and downstream Benjamin usefulness.

### Maximum credible downside
State the downside explicitly.

### Reversibility
How the decision can be rolled back or why it cannot.

### Requested authority
The smallest owner action, credential scope, provider verification, spending authority, or ZLJ Governor change required.

### Independent work continuing
List what ZLJ can keep doing without waiting for the decision.

## Bad escalation examples

Do not send:

- "What should I build next?"
- "Which market should I study?"
- "Should I use Python or Rust?"
- "Which forecasting model should I test?"
- "Do you want me to keep validating data?"
- "The provider blocked me; should I try to get around it?"

Those are ZLJ engineering/research matters unless a legitimate owner or provider authority boundary is reached. Safeguards are never to be bypassed.

Do not send:

- "Authorize ZLJ to trade $25 live."

That is the wrong organ model. A future live action should arise from Benjamin's decision, pass Watchman, and be executed through The Hand.

## Good escalation example

`Complete Provider-X's required identity verification so ZLJ can restore the approved read-only market-data feed used by MODEL-MICRO-014. The model remains quarantined because fresh source data is unavailable. No workaround has been attempted. Replay/model-evaluation work continues independently.`

## Core rule

> **The owner supplies sovereignty where only the owner can act; ZLJ supplies autonomous engineering inside its scope; other Epinnox organs retain their own authority.**
