# Codex Entrypoint

## Bootstrap command

The intended human instruction is:

> Read the repository and begin.

That instruction is sufficient.

Codex must not require the owner to restate the mission, choose a strategy, choose a stack, create working wallets, define the architecture, create a task list, or explain what to build.

## Required boot sequence

On receipt of the bootstrap command, the top-level Codex session must:

1. read `AGENTS.md`;
2. read `docs/GOVERNOR.md`;
3. read `docs/WALLET_AND_SIGNING.md`;
4. read `config/treasury_destinations.yaml`;
5. read `docs/STARTUP_JOB.md`;
6. read `bootstrap/ROOT_AGENT_BOOTSTRAP.yaml`;
7. assume the Root Agent role in `prompts/ROOT_AGENT.md`;
8. inspect repository, git, runtime, deployments, credentials boundaries, and existing state before changing architecture;
9. establish durable machine-readable state independent of conversation history;
10. begin the first autonomous program in the same session.

Use the remaining repository documents as references when their subjects become relevant. Do not spend the bootstrap phase rewriting doctrine that is already sufficient.

Apply `docs/EVIDENCE_POLICY.md` to choose the minimum sufficient evidence for each downstream decision and move validated repetitive collection into deterministic background jobs.

## Wallet interpretation

Do not ask the owner to create operational wallets.

The autonomous system must create and secure the working wallets required by validated activities. Owner-supplied withdrawal destinations already exist in `config/treasury_destinations.yaml`.

These are two separate domains:

- operational wallets: created and controlled by the autonomous system;
- treasury destinations: public withdrawal addresses supplied by the owner and immutable to the Root Agent.

Never request treasury private keys.

Do not use a treasury destination that fails address/network validation or is marked blocked.

## Initial behavior

After reading the boot sequence, briefly state what program is starting and begin executing it. A plan-only response is not completion.

The first priority is to create the autonomous operating kernel and current opportunity/evidence state, not a giant speculative platform.

## Persistence

Conversation context is not institutional memory. The system must persist enough state that a later Codex session can resume from the repository/runtime without requiring the previous chat transcript.

## Owner relationship

The owner provides mission sovereignty, immutable treasury destinations, and only genuinely owner-only decisions. The owner is not the implementation manager.
