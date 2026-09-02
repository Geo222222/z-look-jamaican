# Codex Entrypoint

## Bootstrap command

The intended human instruction is:

> Read the repository and begin.

That instruction is sufficient.

Codex must not require the owner to restate the mission, choose a model strategy, choose a stack, define the architecture, create a task list, or explain what to build.

The current mission boundary is:

> **ZLJ sees. Benjamin decides. Watchman governs. The Hand executes. The Book remembers and proves.**

ZLJ is Epinnox's market-perception and model-production system. Older repository language that gives ZLJ production wallets, capital decisions, treasury settlement, or general economic execution is predecessor language and is non-governing unless intentionally migrated to the owning organ.

## Required boot sequence

On receipt of the bootstrap command, the top-level Codex session must:

1. read `AGENTS.md`;
2. read `docs/MISSION.md`;
3. read `docs/CONSTITUTION.md`;
4. read `docs/GOVERNOR.md`;
5. read `docs/DECISION_PROTOCOL.md`;
6. read `docs/MEMORY_AND_EVIDENCE.md`;
7. read `docs/WALLET_AND_SIGNING.md` for the explicit Hand-ownership clarification if wallet/signing predecessor material is relevant;
8. read `docs/STARTUP_JOB.md`;
9. read `bootstrap/ROOT_AGENT_BOOTSTRAP.yaml` as implementation state, not as authority overriding the governing Markdown;
10. assume the Root Agent role in `prompts/ROOT_AGENT.md`;
11. inspect repository, git, runtime, deployments, provider/credential boundaries, and existing state before changing architecture;
12. establish durable machine-readable state independent of conversation history;
13. begin the first useful ZLJ program in the same session.

Use the remaining repository documents as references when their subjects become relevant. Where an older document conflicts with the ownership model above, do not extend the conflicting behavior. Preserve it as historical context and follow the current governing files.

## External financial capability interpretation

Do not ask the owner to create production operational wallets for ZLJ.

ZLJ does not own production custody, signing, transfers, sweeps, settlement, or live order submission. Those are external financial action capabilities belonging to **The Hand** after **Watchman** authorizes a **Benjamin** decision.

ZLJ may:

- consume approved public/read-only market data;
- use authentication needed for approved read-only data providers;
- operate local simulations, replay, shadow systems, test doubles, or otherwise permitted non-capital validation;
- retain predecessor wallet/signing code only for reproducibility, analysis, testing, or migration evidence.

## Initial behavior

After reading the boot sequence, briefly state what ZLJ program is starting and begin executing it. A plan-only response is not completion.

The first priority is to strengthen the autonomous market-perception/model kernel and current evidence state, not to build a general economic enterprise or a giant speculative platform.

## Persistence

Conversation context is not institutional memory. The system must persist enough ZLJ state that a later Codex session can resume data/model work from the repository/runtime without requiring the previous chat transcript.

Material cross-organ lineage should be bridgeable into The Book rather than making conversation history or ZLJ alone the authority for Epinnox history.

## Owner relationship

The owner provides mission sovereignty and genuine owner-only decisions. The owner is not the implementation manager.

When work reaches another organ's boundary, Codex should produce the needed interface/evidence requirement and stop there rather than silently taking ownership.
