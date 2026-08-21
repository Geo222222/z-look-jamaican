# Minimal Autonomous Kernel

## Purpose

The kernel is the smallest durable control-plane implementation justified by the bootstrap state. It makes current state, objectives, tasks, opportunities, experiments, decisions, metrics, incidents, deployments, wallet metadata, and recovery checkpoints inspectable without relying on conversation history.

It does **not** trade, sign, move capital, store credentials, deploy production services, or grant authority. Those capabilities require separate deterministic execution-plane components and the stage gates in `docs/GOVERNOR.md` and `docs/WALLET_AND_SIGNING.md`.

## Commands

Run from the repository root:

```powershell
python -m autonomous_kernel validate
python -m autonomous_kernel status
python -m autonomous_kernel next-work
python -m autonomous_kernel recover
python -m unittest discover -s tests -v
```

State mutation is deliberately narrow:

```powershell
python -m autonomous_kernel task-status --task-id TASK-ID --status completed
python -m autonomous_kernel transition --to DISCOVERY --trigger "bootstrap criteria passed" --decision-id DECISION-ID --evidence path-or-record-id
```

Both mutation commands validate existing state first, acquire a single-writer lease, prepare a durable transaction journal, and replace JSON state files atomically. If interruption occurs between files, `recover` idempotently rolls the prepared transaction forward. State transitions are restricted by `docs/ROOT_AGENT_STATE_MACHINE.md` and journaled in `state/transitions.jsonl`; completed state transactions are recorded in `state/transactions.jsonl`.

## Durable layout

- `state/`: current state, objectives, ranked backlog, specialists, deployments, incidents, public wallet metadata, transitions, and resume checkpoint.
- `memory/`: append-only decision, experiment, rejection, and reflection records.
- `opportunities/`: ranked candidate register with explicit scoring and falsification paths.
- `evidence/`: reproducible local observations and dated external source references.
- `metrics/`: system and economic measurements; simulated, unrealized, and realized values remain separate.
- `accounting/`: repository-scoped ledger. An empty ledger does not assert an external balance.

Every format has `schema_version: 1` where it is a JSON document. Stable IDs and evidence references must survive later migrations.

## Recovery

After interruption:

1. run `python -m autonomous_kernel validate`;
2. inspect `python -m autonomous_kernel status` and `state/resume.json`;
3. check Git/worktree state and any active incident before mutation;
4. run `python -m autonomous_kernel next-work`;
5. resume only work permitted by the Governor snapshot and task scope.

If validation reports a pending transaction, run `recover`. If any other validation fails, stop the affected write path, preserve evidence, and repair or reconcile state. The writer lease clears itself only when it can prove a same-host process no longer exists. Do not weaken validation to make a failing state appear healthy.

## Current limitations

- The file store enforces one writer. It is not a concurrent job queue; parallel specialists must return results to the Root Agent instead of mutating canonical state.
- Multi-file changes use a prepared roll-forward journal, not database transactions. This provides crash recovery for the kernel's bounded mutation commands; a SQLite/Postgres migration becomes justified if concurrent writers or broader transactional services become real requirements.
- External-source freshness is a property of evidence records and experiments, not guaranteed by the store.
- No scheduler or deployment is justified yet. A later runtime should add liveness, readiness, structured logs, metrics, version identity, and rollback before being relied upon.
- Operational wallet creation remains deferred until a validated experiment specifies a chain and purpose. No private material may be stored in this repository.
