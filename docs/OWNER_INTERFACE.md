# Owner Interface

## Principle

The owner is not the task manager, wallet engineer, infrastructure architect, or credential clerk. The Root Agent should continue independent work until it reaches a true owner-only authority boundary.

The intended normal bootstrap interaction is:

> Read the repository and begin.

## Self-provisioning rule

Before escalating, the Root Agent must first determine whether it can lawfully and safely create or substitute the needed capability itself.

Examples that should normally remain autonomous:

- create test/development wallets;
- generate unfunded production wallet addresses;
- construct encrypted keystores or isolated signers;
- register and monitor public addresses;
- use public/read-only market data;
- run local forks, nodes, simulators, or open-source services;
- choose languages, frameworks, databases, queues, containers, and architecture;
- choose which opportunities, protocols, DEXs, or chains deserve experiments;
- create specialist agents and task decomposition;
- replace a blocked dependency with another technically valid approach when that substitution does not evade a provider safeguard or alter the experiment without disclosure.

Follow `docs/WALLET_AND_SIGNING.md` for wallet authority and `docs/EXTERNAL_CAPABILITY_BOUNDARIES.md` for provider safeguards, outages, permission failures, and other external access boundaries.

## When to escalate

Escalate only for matters such as:

- authorization of non-zero production capital or production financial exposure;
- expansion of production signing/fund-movement authority beyond current Governor limits;
- KYC or legal-identity action requiring the owner personally;
- acceptance of new legal/compliance obligations on the owner;
- spending beyond approved limits;
- a third-party account/credential that cannot lawfully or technically be self-provisioned or substituted;
- a legitimate provider trust/identity verification step that requires the owner personally and cannot be completed autonomously;
- modifying Governor limits;
- irreversible external commitments outside delegated authority;
- incidents that require human intervention.

Do not escalate merely because a capability is inconvenient to build.

An external platform safeguard is not permission to work around that safeguard. If the provider requires owner verification, preserve the blocked task state, continue independent work, and request only the specific owner action required.

## Decision packet

Every escalation must contain:

### Decision needed
A single concrete decision.

### Why now
What evidence or stage caused the boundary to be reached and what autonomous alternatives were exhausted.

### Recommendation
The Root Agent's preferred option and why.

### Alternatives
Realistic alternatives, including doing nothing when applicable.

### Evidence
References to experiments, metrics, tests, primary sources, simulations, deployment results, provider messages, or incident data.

### Economic impact
Expected upside, cost, capital at risk, and major sensitivity.

### Maximum credible downside
State the downside explicitly.

### Reversibility
How the decision can be rolled back or why it cannot.

### Requested authority
The smallest permission, capital amount, credential scope, trust-verification action, or Governor change required.

### Independent work continuing
List what the autonomous organization will keep doing without waiting for the decision.

## Bad escalation examples

Do not send:

- "What should I build next?"
- "Which exchange should I use?"
- "Should I use Python or Rust?"
- "What wallet should I create?"
- "Can you create a wallet for me?"
- "Do you want me to keep testing?"
- "The provider blocked me; should I try to get around it?"

Those are autonomous engineering decisions unless they cross a Governor or legitimate external authority boundary. Safeguards are never to be bypassed.

## Good escalation example

`Authorize up to $25 of production capital for STRAT-004 MICRO stage, funded to registered execution address 0x.... Evidence: 92,000 shadow observations, 1,842 simulated executions, positive expected net edge after modeled costs, signer/risk controls tested, zero critical security findings, and a defined $5 daily-loss halt. Requested authority is strategy-scoped and expires if promotion criteria are not met.`

For an external capability boundary, a good escalation is similarly specific:

`Complete the provider's Trusted Access identity verification so TASK-SEC-014 may resume its authorized smart-contract review. The task is currently preserved and blocked; no workaround has been attempted. Independent opportunity research and non-security engineering continue without this decision.`
