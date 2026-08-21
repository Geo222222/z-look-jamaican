# Z Look Jamaican

Autonomous economic engineering bootstrap repository.

The repository exists to let a top-level Codex agent start from zero, discover lawful machine-operated economic opportunities, build the infrastructure required to test them, operate validated systems, measure realized outcomes, and continuously improve without requiring the owner to manage implementation work.

## Start command

The intended owner instruction is:

> Read the repository and begin.

That is the bootstrap contract. Codex should read the governing files, inspect the repository/runtime, establish durable state, and begin work in the same session.

## Core operating model

The Root Agent owns:

- opportunity discovery and ranking;
- research and falsification experiments;
- architecture and implementation;
- specialist-agent delegation;
- testing, deployment, observability, and recovery;
- operational wallet creation and signing infrastructure;
- economic accounting and evidence;
- ongoing reprioritization.

The owner does not provide implementation tickets and does not provision the agent's working wallets.

## Wallet and treasury model

There are two distinct wallet domains.

### Operational wallets

The autonomous system creates, persists, secures, monitors, rotates, and uses its own working wallets when a validated activity requires them. Private keys and seed material must remain outside Git, prompts, logs, reports, and ordinary agent memory.

### Owner treasury destinations

The owner supplies withdrawal-only destination addresses in `config/treasury_destinations.yaml`.

The Root Agent may not rewrite those destinations. It must never request or require their private keys. Excess realized assets may eventually be swept from operational wallets to validated active treasury destinations after the sweep system, accounting, signer isolation, reserves, and reconciliation have been implemented and tested.

The current registry includes owner-provided BTC, DOGE, ETH, and a TRON-USDT entry. The TRON-USDT entry is intentionally blocked pending address-format validation.

## Bootstrap read order

Read these first:

1. `AGENTS.md`
2. `docs/CODEX_ENTRYPOINT.md`
3. `docs/GOVERNOR.md`
4. `docs/WALLET_AND_SIGNING.md`
5. `config/treasury_destinations.yaml`
6. `docs/STARTUP_JOB.md`
7. `bootstrap/ROOT_AGENT_BOOTSTRAP.yaml`
8. `prompts/ROOT_AGENT.md`

Then inspect the remaining docs only as needed for the active program. Higher-order Governor rules override lower-level operating guidance.

## Operating loop

`observe -> research -> rank -> hypothesize -> build minimum experiment -> test -> measure -> record -> reflect -> keep / improve / suspend / reject -> repeat`

AI reasoning belongs in the control plane. Deterministic code must enforce transaction construction, signing policy, accounting, risk limits, destination allowlists, and execution-critical safety checks.

## First milestone

The first milestone is not live profit. It is a durable autonomous kernel that can:

- resume from repository/runtime state rather than chat history;
- maintain a ranked opportunity register;
- create and verify experiments;
- build reusable capabilities only when justified;
- create and safely operate working wallets when required;
- maintain auditable economic state;
- monitor deployments and failures;
- independently determine the next useful action.

Live economic activity must be grounded in evidence, measured costs, deterministic controls, and the limits defined by the Governor.
