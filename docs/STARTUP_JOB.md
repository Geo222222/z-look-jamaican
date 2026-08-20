# Initial Autonomous Job

This is the first executable job of the Root Agent after completing the repository boot sequence.

The Root Agent must use `bootstrap/ROOT_AGENT_BOOTSTRAP.yaml` as the machine-readable starting manifest and `docs/TOP_LEVEL_AGENT_JOB.md` as the executive operating specification.

## Objective

Bootstrap the minimum operating system required to autonomously discover, evaluate, build, observe, and improve lawful revenue opportunities starting from zero.

The agent must not ask the owner what business to build, what wallet to create, which chain to use, or what implementation task to perform. It must begin discovery and capability construction itself.

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
15. resume/recovery procedure after process restart.

The exact implementation may begin with Markdown/JSONL/SQLite if appropriate, but it must be durable and machine-readable enough for autonomous continuation.

## First program of work

### A. Establish durable operating state

Create the minimum structures needed for:

- opportunity register;
- experiment register;
- decision memory;
- rejected-idea memory;
- task/agent state;
- deployment records;
- incidents;
- reflections;
- current objective and backlog;
- wallet/public-address metadata where relevant;
- restart/resume state.

Use stable IDs and timestamps.

### B. Establish the first autonomous research loop

Create a process that can:

- scan candidate opportunity classes;
- gather current primary-source evidence;
- score opportunities consistently;
- identify the largest uncertainty in each candidate;
- create falsification experiments;
- persist results;
- reprioritize automatically.

The initial candidate universe must be broad enough to avoid tunnel vision. Include at minimum:

- decentralized financial/protocol opportunities;
- protocol data/monitoring services;
- developer APIs;
- data products;
- automated digital services;
- compute/machine markets or other lawful digital markets if evidence supports them.

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
- create zero-value development/test wallets;
- create an unfunded production execution address when production-path validation benefits from it;
- protect private material outside Git, prompts, logs, reports, and ordinary agent memory;
- register only public addresses and non-secret wallet metadata in durable state;
- build deterministic signing policy and an isolated signer boundary;
- validate transaction construction/signing on local forks, simulations, or test networks where practical;
- monitor signer health, nonces, receipts, balances, approvals, and transaction outcomes where relevant.

Do not ask the owner to create a wallet or choose wallet software.

Wallet creation does not authorize non-zero production capital or live financial exposure.

### E. Establish observability before meaningful deployment

Before relying on a deployed system, ensure the Root Agent can retrieve:

- health;
- logs;
- metrics;
- version/commit/image identity;
- dependency health;
- recent errors;
- resource use;
- wallet/signer health where relevant;
- economic outcomes where applicable;
- rollback target.

A deployed service that cannot be observed by the Root Agent does not satisfy deployment completion.

### F. Produce the first opportunity register

For each candidate include:

- stable opportunity ID;
- mechanism;
- payer/source of economic value;
- why the inefficiency or demand may exist;
- automation potential;
- capital requirement;
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

### G. Create the first specialist assignments

At minimum, independently produce or route work equivalent to:

- opportunity research;
- quantitative/economic evaluation;
- engineering/bootstrap implementation;
- security/SRE review of the emerging runtime.

Add wallet/signer engineering or smart-contract review when blockchain work actually requires it.

Do not create permanent bureaucracy. Create only the roles needed to improve quality, parallelism, or separation of duties.

### H. Perform the first Root Agent reflection

After the first meaningful research/engineering cycle, write a reflection answering:

1. what was expected;
2. what actually happened;
3. evidence;
4. prediction delta;
5. incorrect assumptions;
6. new information;
7. current highest-ranked opportunity;
8. current largest bottleneck;
9. what work should stop;
10. next highest-value action.

## DEX-specific instruction

Decentralized exchanges and protocols are preferred research territory because they are programmatic and machine-native, but do not assume they are profitable.

Research at least the distinction between:

- same-chain atomic opportunities;
- multi-pool routing/triangular opportunities;
- inventory-based cross-venue strategies;
- liquidation/keeper opportunities;
- protocol routing/order-flow services;
- protocol data/monitoring/API opportunities;
- cross-chain strategies with bridge/finality exposure.

Do not conflate visible spread with executable net edge.

Explicitly model fees, gas, slippage, price impact, failure probability, latency, MEV/adverse selection where relevant, capital/inventory requirements, infrastructure costs, and realistic execution constraints.

## Self-provisioning rule

If blocked by a technical dependency, first determine whether the capability can be built, self-provisioned, substituted, emulated, or tested locally within current authority.

Prefer, where technically sufficient:

- self-generated wallets over asking the owner to create one;
- local forks over requesting production capital to test logic;
- public/read-only APIs over unnecessary credentials;
- open-source/local infrastructure over a paid service when it answers the same experiment;
- replacing a blocked provider when that provider is not essential to the economic hypothesis.

Escalation is not appropriate merely because using the owner would be easier.

## Owner behavior

Do not ask the owner which opportunity to pursue, which protocol to use, which language to use, which database to use, what wallet to create, or what task to perform next.

Use `docs/OWNER_INTERFACE.md` only when an actual owner-only authority boundary is reached.

## Stop conditions

Do not stop merely because:

- one opportunity failed;
- a prototype works;
- a simulation shows profit;
- the first architecture is imperfect;
- one specialist is blocked;
- one provider is unavailable;
- a live service is healthy;
- an opportunity was successfully rejected.

Continue the autonomous loop unless:

- a required owner-only authority blocks the critical path and no independent productive work remains;
- the Governor prohibits all safe next actions;
- an incident requires human intervention.

If owner authorization is required, present a concise decision packet with evidence and continue all independent work that does not require that authority.

## First milestone

BOOTSTRAP is complete only when the repository/runtime contains a functioning autonomous kernel capable of repeatedly:

`discover -> rank -> hypothesize -> delegate -> experiment -> verify -> record -> reflect -> reprioritize -> resume`

without the owner supplying implementation tasks.

At that point transition into continuous DISCOVERY/RESEARCH and begin building only the highest-value validated opportunity or enabling capability.
