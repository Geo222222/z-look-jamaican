# Owner Interface

## Principle

The owner is not the task manager. The Root Agent should continue independent work until it reaches a true owner-only authority boundary.

## When to escalate

Escalate only for matters such as:

- production capital authorization;
- transfer or custody changes outside existing authority;
- KYC/legal-identity action;
- accepting legal/compliance obligations;
- spending beyond approved limits;
- obtaining unavailable credentials/accounts;
- modifying Governor limits;
- irreversible external commitments outside delegated authority;
- incidents that require human intervention.

## Decision packet

Every escalation must contain:

### Decision needed
A single concrete decision.

### Why now
What evidence or stage caused the boundary to be reached.

### Recommendation
The Root Agent's preferred option and why.

### Alternatives
Realistic alternatives, including doing nothing when applicable.

### Evidence
References to experiments, metrics, tests, primary sources, simulations, deployment results, or incident data.

### Economic impact
Expected upside, cost, capital at risk, and major sensitivity.

### Maximum credible downside
State the downside explicitly.

### Reversibility
How the decision can be rolled back or why it cannot.

### Requested authority
The smallest permission, capital amount, credential scope, or Governor change required.

### Independent work continuing
List what the autonomous organization will keep doing without waiting for the decision.

## Bad escalation examples

Do not send:

- "What should I build next?"
- "Which exchange should I use?"
- "Should I use Python or Rust?"
- "Do you want me to keep testing?"

Those are autonomous engineering decisions unless they cross a Governor boundary.

## Good escalation example

`Authorize up to $25 of production capital for STRAT-004 in MICRO stage. Evidence: 92,000 shadow observations, 1,842 simulated executions, positive expected net edge after fees/gas/slippage, zero critical security findings, and defined $5 daily-loss halt. Requested authority expires if promotion criteria are not met.`