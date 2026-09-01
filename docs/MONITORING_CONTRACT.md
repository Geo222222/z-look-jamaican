# Authoritative Read-Only Monitoring Contract

## Observer entrypoint

The only normalized observer entrypoint is:

```powershell
python -m autonomous_kernel monitor_snapshot --json
```

An alternate repository root may be selected without changing it:

```powershell
python -m autonomous_kernel --root C:\path\to\repository monitor_snapshot --json
```

The command reads local durable state and prints one JSON document to stdout. It performs no writes, recovery, experiment advancement, decision creation or resolution, network access, scheduler action, wallet action, signer access, secret access, or state mutation. Polling this command is safe. Consumers must not treat reading individual filenames heuristically as an API.

## Envelope

The top-level schema is:

```json
{
  "contract": {
    "name": "z-look-jamaican-monitor-snapshot",
    "schema_version": "1.2.0",
    "read_only": true,
    "observed_at": "RFC3339 UTC timestamp",
    "availability_states": ["available", "unknown", "not_earned", "blocked", "unavailable"],
    "unknown_semantics": {}
  },
  "sections": {
    "section_name": {
      "provenance": {},
      "availability": {},
      "freshness": {},
      "data": {}
    }
  }
}
```

Contract versions use semantic versioning. A removed/renamed field or changed meaning requires a major version; an additive field or section requires a minor version; a clarification or compatible correction requires a patch version. Object key order is not contractual.

Every section includes:

- `provenance.source`: source class (`repository_files`, `capability_absent`, or another explicitly named source class);
- `provenance.source_id`: stable monitoring-section ID;
- `provenance.path`: the single canonical path, otherwise `null`;
- `provenance.paths`: all canonical inputs, in deterministic order;
- `provenance.observed_at`: when this read occurred; it is not an event time;
- `provenance.authoritative_at`: latest source-declared event/update time, or `null` when the source has none;
- `provenance.schema_version`: provenance-envelope version, currently integer `1`;
- `provenance.integrity.algorithm`: `sha256`;
- `provenance.integrity.by_path`: hash of every existing canonical input. A `null` hash means the declared source path is absent;
- `availability.state` and `availability.reason`;
- `freshness.expectation`, `freshness.state`, and, where cadence applies, `freshness.age_seconds`;
- `data`: normalized data for the section.

## Absence and authority semantics

- `available`: a canonical source establishes the returned value. Zero is a value only when that source actually establishes zero.
- `unknown`: a canonical source exists but cannot establish the value. For example, repository accounting cannot assert unrelated external balances.
- `not_earned`: the capability or economic outcome has not passed its evidence/readiness gate. This is not `$0.00` and is not an error.
- `blocked`: an explicit Governor, policy, validation, destination-validation, or readiness gate prevents the capability. Read-only metadata may still be displayed.
- `unavailable`: no canonical source/capability exists. The frontend must not infer a value from nearby files.

`observed_at` is snapshot time. `authoritative_at` is source time. Event fields such as `created_at`, `captured_at`, `opened_at`, `resolved_at`, `signal_candle_timestamp`, `actionable_at`, and `evaluation_end` retain their source-specific meanings below. All ISO strings are UTC RFC3339. Shadow decision epoch values are Unix seconds UTC.

## Canonical surface registry

### Background deterministic jobs — `sections.background_jobs`

- Canonical definition source: mutable authoritative `state/background_jobs.json`; derived per-run claims and receipts live under `runtime/background_jobs/` and are never executed by the monitor.
- Schema: stable job ID, enabled flag, allowlisted Python module, zero-effect policy, timeout, and stable run records containing `id`, `not_before`, string arguments, derived state, and an optional receipt.
- `not_before` is the earliest UTC launch time, not evidence time. Receipt `started_at`/`completed_at` describe process execution. Provider and observation timestamps remain inside the resulting evidence bundle.
- States are `SCHEDULED`, `READY`, `SUCCEEDED`, `FAILED`, or `BLOCKED`. `READY` does not mean the external scheduler is alive; it means the deterministic run may be launched.
- Polling the snapshot or `python -m autonomous_kernel jobs_status` is read-only. Only the explicit `jobs_run_due` command may create a claim and detached worker.
- Registry validation forbids shell execution, non-allowlisted modules, credentials, and capital effects. Outputs must never contain secrets or private key material.

### System heartbeat / health — `sections.system_health`

- Canonical sources: `state/current_state.json`, `state/resume.json`, `state/market_shadow.json`, `metrics/system.jsonl`, plus the read-only `autonomous_kernel.store.validate` result.
- Schema: `system_id`, `root_state`, `strategy_stage`, validation status/checks/errors, `heartbeat`, and historical `system_metrics`.
- Heartbeat fields: automation stable IDs, expected interval (900 seconds), last shadow observation, age, and `fresh|stale|unknown`. Fresh means age is at most two expected intervals; it does not prove the external scheduler is live.
- Authority: current/resume/shadow files are mutable authoritative state; system metrics are append-only historical records; validation and freshness are derived read-only values.
- Polling: safe. Expected frontend interval is 15–60 seconds; polling does not advance the heartbeat.
- IDs: `system_id`; automation IDs relate to `state/resume.json` and deployment/audit records.

### Active experiment — `sections.active_experiment`

- Canonical sources: `state/market_shadow.json`, the matching ID in `memory/experiments.jsonl`, and the frozen preregistration artifact.
- Schema: `experiment_id`, matching experiment records, `mode`, current summary, and related active task IDs.
- Current stable relationship: `state/market_shadow.json.experiment_id -> memory/experiments.jsonl.id`; active task IDs relate to `state/backlog.json.id`.
- Authority: shadow is mutable authoritative observation state; experiment memory is append-only; preregistration is immutable evidence.
- Freshness: expected every 15 minutes while the heartbeat is active. Polling is safe.

### Experiment history — `sections.experiment_history`

- Canonical source: append-only `memory/experiments.jsonl`.
- Schema: source records are returned losslessly in `items`. Common fields are stable `id`, `type`, `parent_objective_id`, `status`, hypothesis, expected/observed outcome, method, evidence IDs, decision, confidence, and `created_at`. Result records may relate to an earlier experiment through `related_records` rather than replace it.
- Timestamps: `created_at` is record creation time. The journal is event-driven, not periodic.
- Polling: safe.

### Experiment registry — `sections.experiment_registry`

- Canonical source: mutable authoritative `state/experiments.json`.
- Schema: lifecycle status, immutable preregistration path/hash, observation store, restart command, reconciliation method, evidence/failure gates, lineage, and resolution.
- Relationships: `id` relates to experiment memory and observation state; `lineage.capability_id` relates to `state/capabilities.json.id`.
- Authority: this is the canonical lifecycle/index. Hash-pinned preregistration artifacts remain parameter authority; declared observation stores remain observation authority.
- Polling: safe and event-driven.

### Capability registry — `sections.capability_registry`

- Canonical source: mutable authoritative `state/capabilities.json`.
- Schema: ordered lifecycle, deterministic promotion rule, capability `id`, kind, current state, operational status, experiment/evidence relationships, next required evidence, `live_enabled`, and append-only evidence-bound transitions.
- Invariant: promotion advances exactly one state with evidence. Models may recommend but cannot mutate the state around validation. `live_enabled` remains false under the current Governor.
- Polling: safe and event-driven.

### Prospective and resolved decisions — `sections.decisions`

- Canonical source: mutable `state/market_shadow.json` for the active shadow experiment.

### Market-data plane — `sections.market_data`

- Canonical sources: rebuildable authoritative `state/market_data.json` and its declared immutable observation bundles.
- Schema: lossless index, observation count, quality counts, raw/normalized separation, replayability, and explicit EXP-MKT-002 retrofit state.
- Each bundle separates provider payload/provenance from normalized values, retains source/receive/observe timestamps, links normalized data to `raw_observation_id`, records the deterministic quality result, and protects content with SHA-256.
- Authority: immutable bundles are observation truth; the index is deterministic and recoverable. Orphan bundles may be recovered by rebuilding the index; corrupt bundles fail closed.
- Freshness: event-driven for future preregistered experiments. It is not the existing EXP-MKT-002 observer and polling never captures data.
- Polling: safe.
- Schema: `experiment_id`, `prospective`, `resolved`, `counts`, `timestamp_violation_ids`, and `shadow_net_return_sum`.
- Stable decision ID: `SHADOW-{product}-{signal_candle_timestamp}`. It relates to `experiment_id` and product.
- Common decision fields: `product`; `observed_at` (Unix seconds when durably decided); `signal_candle_timestamp` (five-minute candle start); `actionable_at` (earliest modeled action boundary and must exceed observation); target/baseline target; activity/weekday inputs; and `status`.
- Resolved-only fields: `resolved_at` (reconciliation run time), `evaluation_end` (price boundary), gross return, transition cost, and net return. These are simulated/shadow returns, never realized P&L.
- Freshness: 15-minute heartbeat. Polling is safe and never resolves a decision.

### Evidence events — `sections.evidence_events`

- Canonical source: append-only `evidence/sources.jsonl`.
- Schema: stable `id`, `captured_at`, `source_type`, observation, canonical artifact `path`, artifact `sha256`, optional command and URLs.
- Relationship: evidence IDs are referenced by opportunities, experiments, decisions, reflections, and incidents.
- Integrity: repository validation recomputes registered artifact hashes. Polling is safe.

### Evidence/data-quality status — `sections.data_quality`

- Canonical sources: evidence registry, `state/incidents.json`, shadow state, and read-only repository validation.
- Schema: repository validation, evidence-integrity status, timestamp violation count/IDs, resolved data-quality incidents, and open incidents.
- Authority: incidents are mutable authoritative lifecycle state; computed checks are derived at read time.
- A failed integrity or prospective-timestamp check sets this surface to `blocked`. Polling is safe.

### Opportunity register and rankings — `sections.opportunities`

- Canonical source: mutable authoritative `opportunities/register.json`.
- Schema is returned losslessly: register status/method and `items`. Each opportunity has stable `id`, optional contiguous `rank`, priority score, title/domain/status, mechanism, payer, economics, uncertainty, evidence quality, risks, next experiment, gates, rejection/reopening criteria, dimension scores, and evidence IDs.
- `rank: null` means unranked/rejected/deferred; it is not rank zero. Scores rank information-gathering work and are not profit forecasts.
- Freshness is event-driven after evidence or reranking. Polling is safe.

### Reflections / conclusions — `sections.reflections`

- Canonical source: append-only `memory/reflections.jsonl`.
- Schema is returned losslessly in `items`; stable `id` relates to subject/program, decisions and evidence. Common fields capture expectation, observation, prediction error, incorrect assumptions, bottleneck, stop/continue actions, uncertainty, decision and next action.
- `created_at` is the reflection record time. Polling is safe.

### Goals and active tasks — `sections.goals_tasks`

- Canonical sources: mutable authoritative `state/objectives.json`, `state/backlog.json`, `state/agents.json`, and `state/resume.json`.
- Objective schema: stable `id`, kind, parent objective ID, status, objective and success evidence.
- Task schema: stable `id`, parent objective ID, title, status, priority score/basis, dependencies, acceptance, reversal condition, and update time.
- Assignment schema: task ID, agent ID, role, status, scope, production authority and capital authority.
- Relationships: parent/dependency/task IDs are validated by the kernel. `active_task_ids` is the authoritative resume set. Polling is safe.

### Retained revenue / realized P&L — `sections.economics`

- Canonical sources: authoritative `accounting/ledger.json` plus append-only `metrics/economic.jsonl`.
- Schema: currency, realized ledger entries, summed realized fields, `retained_revenue_state`, economic metrics, and `shadow_pnl_excluded_from_realized=true`.
- Authority rule: only ledger entries can establish realized/retained economics. Modeled, unrealized, or shadow values never become realized by appearing in metrics.
- An empty ledger is `not_earned`, not proof of an external `$0.00` balance. Metric `created_at` is record time; ledger `updated_at` is mutation time when present. Polling is safe.

### Current financial exposure — `sections.financial_exposure`

- Canonical sources: Governor snapshot, accounting ledger and operational-wallet registry.

### Execution plane — `sections.execution_plane`

- Canonical sources: `state/capabilities.json` and immutable `receipts/execution/*.json` when receipts exist.
- Schema: operating mode, live-enable state, authorization policy, adapter identity, receipt count, and lossless execution receipts.
- Receipt relationships: request → risk authorization → execution result → accounting reconciliation, joined by `request_id` and protected by request/content SHA-256 hashes.
- Current semantics: `PRE_LIVE_ZERO_EXPOSURE`; no venue adapter, fills, capital movement, or live authorization exists.
- Polling: safe. Receipt creation occurs only through an explicit execution-plane write path, never through the monitor.

### Accounting reconciliation — `sections.accounting_reconciliation`

- Canonical sources: authoritative realized `accounting/ledger.json` and immutable execution receipts.
- Schema: receipt count, discrepancy count/items, external-venue-truth availability, and explicit exclusion of shadow/simulation from realized economics.
- Reconciliation state is exactly one of `NOT_APPLICABLE`, `NO_EXTERNAL_TRUTH`, `MATCHED`, `DIVERGED`, or `ERROR`. `discrepancy_count` is `null` unless an authoritative comparison was actually performed. Zero means a performed comparison found zero discrepancies.
- `truth_sources` identifies what was compared. `DECLARED_SHADOW_FILL_EVENTS` can be `MATCHED` while `external_venue_truth` remains unavailable. `external_comparison_performed` and `external_discrepancy_count` prevent a shadow match from being misrepresented as venue reconciliation.
- Truth hierarchy: requests are intent; fills are execution truth; venue/account balances are external truth; realized ledger entries require reconciliation. Pre-live no-effect receipts establish zero internal financial effect but do not assert unrelated external balances.
- Polling: safe and event-driven.
- Schema: repository-recorded exposure, production authorization, maximum concurrent exposure, capital-movement state, wallet count and `external_untracked_exposure`.
- Current repository-recorded zero is authoritative inside this system because live capital and wallets are absent and limits are zero. Untracked external exposure remains `unknown`.
- Polling is safe.

### Operational wallet public metadata — `sections.wallets`

- Canonical source: mutable authoritative `state/operational_wallets.json`.
- Schema: `public_metadata`, observation, decision, and the invariant `private_material_exposed=false`.
- Expected future public item fields must have stable wallet ID, chain/network, public address, lifecycle/validation state, purpose and safe health metadata. Private keys, seed phrases, mnemonics, signing material, API keys, credentials and secret-store locations must never be returned.
- Empty current registry is `not_earned`. Polling is safe.

### Treasury destinations and validation — `sections.treasury`

- Canonical source: immutable owner-controlled `config/treasury_destinations.yaml`; its anchored SHA-256 and active/blocked IDs are in `state/current_state.json`.
- Normalized schema: registry version/purpose, mutation/private-key policy, public destination items (`id`, asset, network, public address, status, validation state), sweep enabled/state/reason and registry hash anchor.
- Relationship: destination ID is stable and distinct from operational wallet IDs. `blocked_pending_validation` maps to `validation_state=blocked`.
- Surface availability is currently `blocked` because sweeps have not earned the readiness gate; destination metadata remains displayable. Polling is safe. Treasury private keys never exist in this system and must never be requested or exposed.

### Governor limits/state — `sections.governor`

- Canonical sources: authoritative policy `docs/GOVERNOR.md` and validated snapshot `state/current_state.json.governor`.
- Schema: current limits/state, capabilities, owner-only blockers and validation status.
- Timestamp: the state file update time; policy text has no runtime event timestamp. Changes are event-driven and require review. Polling is safe.

### Deployments/version identity — `sections.deployments`

- Canonical sources: `state/deployments.json`, `state/resume.json` active automation IDs, and `.git/HEAD`/its referenced ref read without invoking Git.
- Schema: Git commit/branch/ref identity, deployment registry records, active automation IDs, product deployment state and live external scheduler status.
- The repository registry is authoritative for registered deployments. A repository automation audit proves registration at its audit time; live provider state remains `unknown` unless a future canonical provider-status adapter is added. No product deployment is `not_earned`.
- Polling is safe and does not refresh Git or contact a provider.

### Incidents — `sections.incidents`

- Canonical source: mutable authoritative `state/incidents.json`.
- Schema is returned losslessly. Stable incident IDs relate to affected experiments/tasks and evidence. `opened_at` is detection/open time; `resolved_at` is resolution time; status controls lifecycle.
- Freshness is event-driven. Polling is safe.

### Runtime/system logs — `sections.runtime_logs`

- Canonical source: none currently registered.
- Schema: `items`, currently empty. Availability is `unavailable`; the frontend must not crawl arbitrary `logs/`-looking paths or present conversation text as logs.
- A future log adapter must define source, retention, redaction, stable event IDs and timestamps before this state can change.

### Model/provider qualification — `sections.model_provider_qualification`

- Canonical qualification source: none currently registered. Availability is `unavailable`.
- Provider-related incidents remain visible through stable incident IDs and do not constitute a qualification registry.
- The frontend must not infer qualification from model names, conversations or successful runs.

## Exposure prohibition

The snapshot allowlists repository control-plane files. It never reads environment files, secret stores, credential directories, signer state, process environments, browser/account sessions, private keys, seed phrases, mnemonics, API keys, passwords or credential values. The following must never enter a monitoring schema or frontend payload:

- private keys or seed material;
- signer handles that permit signing rather than public health observation;
- API keys, bearer tokens, cookies, passwords or recovery codes;
- secret-store paths/identifiers when they reveal custody structure;
- raw environment variables;
- unredacted provider or customer credentials;
- treasury private keys (which are neither required nor expected).

Public operational-wallet addresses and owner treasury destination addresses may be displayed. They grant no signing authority.

## Consumer rules

1. Poll only the snapshot command, not guessed files.
2. Key records by stable IDs, never array position or title.
3. Compare event timestamps to `observed_at`; do not substitute snapshot time for event time.
4. Preserve all five availability states visually and semantically.
5. Never combine shadow return with realized economics.
6. Treat failed validation, evidence integrity or timestamp integrity as a blocked/degraded system.
7. Ignore additive fields under the same major version safely.
8. Do not write back through this contract; it has no mutation endpoint.
