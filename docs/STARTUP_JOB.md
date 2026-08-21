# Initial Autonomous Job

This is the first executable job of the Root Agent after the bootstrap read sequence.

## Objective

Build the smallest durable autonomous operating kernel that can discover, rank, test, build, observe, and improve lawful revenue opportunities from zero without requiring owner task assignment.

Do not ask the owner what business to build, what chain to use, what working wallet to create, which language to use, or what implementation task to perform.

## Phase 1 — Inspect before building

Establish the actual current state:

- repository and git state;
- existing code and unfinished work;
- runtime/deployment state;
- available tools and credentials boundaries;
- tests and CI;
- existing persistent state;
- existing wallets/public addresses if any;
- configured treasury destinations;
- known incidents or blocked paths.

Do not assume repository documentation proves implementation exists.

## Phase 2 — Create durable operating state

Before BOOTSTRAP is complete, create machine-readable durable forms of:

- current state;
- objective hierarchy;
- ranked backlog;
- opportunity register;
- experiment register;
- decision memory;
- rejection/reopening memory;
- specialist-task registry;
- deployment registry;
- incident registry;
- reflection log;
- economic metrics;
- system-health metrics;
- operational wallet/public-address registry;
- transaction/accounting ledger where applicable;
- resume/recovery state.

Start simple. JSONL, SQLite, or similarly inspectable storage is acceptable. Earn complexity.

## Phase 3 — Opportunity discovery

Build the first current opportunity register from evidence rather than assumptions.

Initial domains may include:

- decentralized protocols and machine-native markets;
- protocol data, monitoring, keeper, or routing services;
- developer APIs;
- data products;
- automated digital services;
- compute/machine markets;
- other lawful opportunities with strong automation potential.

For each candidate record:

- mechanism;
- payer/source of economic value;
- automation potential;
- capital requirement;
- expected economics;
- largest uncertainty;
- evidence quality;
- cost and time to falsify;
- security/operational risk;
- legal/compliance friction;
- competition;
- next falsifiable experiment;
- rejection criteria;
- reopening criteria.

Rank candidates. Begin with cheap, high-information experiments.

## Phase 4 — Engineering loop

Create only the infrastructure required by the highest-value current experiments.

The kernel must be able to:

- create scoped specialist work;
- modify the repository safely;
- run tests;
- isolate experiments;
- preserve evidence;
- identify deployment/version lineage;
- observe running components;
- recover after interruption;
- update the ranked backlog from measured results.

Do not build a giant framework before current experiments require it.

## Phase 5 — Wallet and treasury capability

When blockchain work becomes justified, implement `docs/WALLET_AND_SIGNING.md`.

The Root Agent creates its own working wallets. It must not ask the owner for wallet software, seed phrases, or operational private keys.

Owner treasury destinations already exist in `config/treasury_destinations.yaml` and are withdrawal-only.

Build, in stages:

1. operational wallet generation and persistence;
2. isolated secret storage/signing boundary;
3. public-address registry;
4. deterministic transaction policy;
5. chain/asset adapters justified by active work;
6. balance, nonce, fee, approval, receipt, and transaction monitoring;
7. accounting/reconciliation;
8. treasury destination validation;
9. treasury sweep proposal and policy engine;
10. duplicate-sweep protection and incident pause controls.

Do not enable a live sweep merely because the code exists. The current treasury registry intentionally blocks any entry that has not passed network/address validation.

## Phase 6 — Observability

A relied-upon deployment is incomplete until the Root Agent can determine at least:

- version/commit/image;
- health;
- dependency health;
- logs;
- errors;
- meaningful metrics;
- workload;
- resource use;
- wallet/signer state where applicable;
- economic outcomes where applicable;
- last successful action;
- rollback target.

## Phase 7 — First reflection and continuation

After the first meaningful experiment/build cycle record:

1. what was expected;
2. what happened;
3. evidence;
4. material prediction error;
5. incorrect assumptions;
6. current highest-ranked opportunity;
7. current largest bottleneck;
8. what should stop;
9. what should continue;
10. next highest-value action.

Then continue. Do not wait for an owner task if independent useful work remains.

## Bootstrap completion criterion

BOOTSTRAP is complete only when the system can repeatedly:

`discover -> rank -> hypothesize -> experiment -> verify -> record -> reflect -> reprioritize -> resume`

and can autonomously build the operational capabilities required by the highest-ranked validated work.
