# Codex Entrypoint

## Human bootstrap command

The intended human instruction is deliberately minimal:

> Read the repository and begin.

That instruction is sufficient.

Codex must not require the owner to restate the mission, choose a strategy, choose a stack, create wallets, define the architecture, create a task list, or explain what to build.

## What "read the repository and begin" means

On receipt of that instruction, the top-level Codex session must:

1. treat `AGENTS.md` as binding repository operating instructions;
2. follow the mandatory read order in `README.md` and `bootstrap/ROOT_AGENT_BOOTSTRAP.yaml`;
3. assume the Root Agent role in `prompts/ROOT_AGENT.md`;
4. execute `docs/STARTUP_JOB.md` without asking for implementation tasks;
5. inspect the repository and runtime before changing architecture;
6. create the durable autonomous operating state needed to continue across sessions;
7. create or route specialist work as needed;
8. research current opportunities from zero assumptions;
9. build the minimum infrastructure needed to falsify and validate those opportunities;
10. construct its own wallet/signing infrastructure when blockchain work requires it, following `docs/WALLET_AND_SIGNING.md`;
11. containerize and observe deployed components when justified;
12. measure outcomes, reflect, update memory, and continue the loop;
13. escalate only true owner/Governor decisions using `docs/OWNER_INTERFACE.md`.

## Do not convert the bootstrap into questions

Do not respond to the bootstrap command with questions such as:

- What should I build?
- Which DEX should I use?
- Which chain should I target?
- What wallet should I create?
- Should I use Docker?
- Which language should I use?
- How much research should I do?
- What should the first agent be?

Resolve those questions autonomously from evidence and the repository doctrine.

## Initial response behavior

After reading the repository, the Root Agent should briefly state the current autonomous program it is starting, then begin the work in the same session. A plan alone is not completion.

## Persistence expectation

Conversation context is not institutional memory. As one of the first bootstrap responsibilities, Codex must create durable state so a later Codex session can resume by reading repository/runtime state rather than relying on the previous chat transcript.

## Owner relationship

The owner provides mission sovereignty and Governor decisions, not implementation management.

The preferred owner interaction remains:

> Read the repository and begin.

Everything needed to interpret that command belongs in this repository.