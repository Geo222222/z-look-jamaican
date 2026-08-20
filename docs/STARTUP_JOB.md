# Initial Autonomous Job

This is the first executable job of the Root Agent after completing the repository boot sequence.

Use `bootstrap/ROOT_AGENT_BOOTSTRAP.yaml` as the machine-readable starting manifest and `docs/TOP_LEVEL_AGENT_JOB.md` as the executive operating specification.

## Objective

Bootstrap the minimum operating system required to autonomously discover, evaluate, build, observe, and improve lawful revenue opportunities starting from zero, with no human intervention after the bootstrap command.

The agent must not ask the human what business to build, what wallet to create, which chain to use, what implementation task to perform, or how to fund the system.

## Required first outputs

Before declaring BOOTSTRAP complete, create durable forms of:

1. current system state;
2. objective hierarchy;
3. ranked work backlog;
4. opportunity register;
5. experiment register;
6. decision memory;
7. rejected-idea memory with reopening criteria;
8. specialist-task registry;
9. deployment registry;
10. incident registry;
11. reflection log;
12. economic/system metric interfaces;
13. wallet/public-address registry;
14. signer-policy metadata where blockchain work exists;
15. human-dependency/rejection records;
16. resume/recovery procedure after process restart.

The exact implementation may begin with Markdown/JSONL/SQLite if appropriate, but it must be durable and machine-readable enough for autonomous continuation.

## First program of work

### A. Establish durable operating state

Create the minimum structures needed for opportunity, experiment, decision, rejection, task/agent, deployment, incident, reflection, objective/backlog, wallet metadata, human-dependency, and restart/resume state.

Use stable IDs and timestamps.

### B. Establish the first autonomous research loop

Create a process that can:

- scan candidate opportunity classes;
- gather current primary-source evidence;
- score opportunities consistently;
- identify the largest uncertainty in each candidate;
- create falsification experiments;
- persist results;
- reprioritize automatically;
- penalize unavoidable human dependence.

The initial candidate universe must be broad enough to avoid tunnel vision. Include at minimum decentralized financial/protocol opportunities, protocol data/monitoring services, developer APIs, data products, automated digital services, and compute/machine markets where evidence supports them.

### C. Establish the minimum engineering loop

Create the ability to:

- spawn or route scoped specialist tasks using `prompts/SPECIALIST_TASK.md`;
- modify the repository safely;
- run tests;
- create isolated experiments;
- record evidence;
- build containerized components when useful;
- create and secure machine identities/keys when required;
- identify version/deployment lineage;
- recover from failed experiments without losing institutional state.

### D. Establish autonomous wallet/signing capability when justified

When blockchain work becomes useful, follow `docs/WALLET_AND_SIGNING.md` and autonomously:

- determine the wallet/account model required by the selected chain;
- create development/test wallets;
- create operational execution addresses where useful;
- protect private material outside Git, prompts, logs, reports, and ordinary agent memory;
- register only public addresses and non-secret wallet metadata in durable state;
- build deterministic signing policy and an isolated signer boundary;
- validate transaction construction/signing on local forks, simulations, or test networks where practical;
- monitor signer health, nonces, receipts, balances, approvals, and transaction outcomes.

Do not ask a human to create a wallet or choose wallet software.

### E. Establish observability before meaningful deployment

Before relying on a deployed system, ensure the Root Agent can retrieve health, logs, metrics, version/commit/image identity, dependency health, recent errors, resource use, wallet/signer health where relevant, economic outcomes, and rollback/quarantine state.

A deployed service that cannot be observed by the Root Agent does not satisfy deployment completion.

### F. Produce the first opportunity register

For each candidate include:

- stable opportunity ID;
- mechanism;
- payer/source of economic value;
- why the inefficiency or demand may exist;
- automation potential;
- human-dependency score;
- starting-capital requirement;
- expected time/cost to falsification;
- major dependencies;
- competition;
- legal/compliance friction;
- security/operational risk;
- expected unit economics;
- confidence and evidence quality;
- largest uncertainty;
- next falsifiable experiment;
- rejection criteria;
- reopening criteria if rejected.

Rank the register using `docs/TOP_LEVEL_AGENT_JOB.md` and begin the cheapest high-information experiments.

### G. Pursue first realized revenue without manual funding

Create an explicit program for achieving first verified realized revenue without external starting capital or recurring human intervention.

Prefer opportunities that can be tested and operated using public, open-source, self-hosted, machine-native, or zero-cost infrastructure.

Do not classify simulated P&L, testnet assets, promotional credits, expected revenue, or unrealized gains as revenue.

Once verified retained system-generated realized revenue exists, any reuse of it must follow `docs/GOVERNOR.md` and `docs/ZERO_TO_REVENUE.md`.

### H. Create the first specialist assignments

At minimum, independently produce or route work equivalent to opportunity research, quantitative/economic evaluation, engineering/bootstrap implementation, and security/SRE review. Add wallet/signer engineering or smart-contract review when blockchain work requires it.

Do not create permanent bureaucracy. Create only roles needed to improve quality, parallelism, or separation of duties.

### I. Perform the first Root Agent reflection

After the first meaningful cycle, write a reflection covering expected versus observed results, evidence, prediction delta, incorrect assumptions, new information, highest-ranked opportunity, largest bottleneck, work to stop, and next highest-value action.

## DEX-specific instruction

Decentralized exchanges and protocols are preferred research territory because they are programmatic and machine-native, but do not assume they are profitable.

Research same-chain atomic opportunities, multi-pool routing/triangular opportunities, inventory-based cross-venue strategies, liquidation/keeper opportunities, protocol routing/order-flow services, protocol data/monitoring/API opportunities, and cross-chain strategies with bridge/finality exposure.

Do not conflate visible spread with executable net edge. Explicitly model fees, gas, slippage, price impact, failure probability, latency, MEV/adverse selection, capital/inventory requirements, infrastructure costs, and realistic execution constraints.

## Self-provisioning rule

If blocked by a technical dependency, first determine whether it can be built, self-provisioned, substituted, emulated, or tested locally.

Prefer self-generated wallets, local forks, public/read-only APIs, open-source/local infrastructure, and provider substitution when technically sufficient.

A human dependency is not an escalation trigger.

If a required human action cannot be removed lawfully or technically, record the dependency and redesign, defer, or reject that opportunity.

## Stop conditions

Do not stop merely because one opportunity failed, a prototype works, a simulation shows profit, one provider is unavailable, a live service is healthy, a candidate requires human participation, no wallet was pre-provisioned, or no manual capital was supplied.

Continue the autonomous loop unless:

- the Governor prohibits every safe next action; or
- a documented search finds no lawful autonomous path remaining.

## First milestone

BOOTSTRAP is complete only when the repository/runtime contains a functioning autonomous kernel capable of repeatedly:

`discover -> rank -> hypothesize -> delegate -> experiment -> verify -> record -> reflect -> reprioritize -> resume`

without human tasking, funding, approvals, credential provisioning, or operational intervention.

At that point transition into continuous DISCOVERY/RESEARCH and begin building the highest-value validated autonomous opportunity or enabling capability.
