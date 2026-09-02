# Market Data Quality Contract

`autonomous_kernel.market_data_quality.classify_market_data` is the first independently qualified adaptation prompted by predecessor research. It copies no runtime integration or calibrated constants from EPI/Epinnox.

The caller supplies a provider identity, source-event timestamp, local receive timestamp, decision-observation timestamp, and explicit age limits. The pure function returns schema version 1 with one status:

- `VALID`: complete ordered timestamps within both limits; an action may proceed to other gates.
- `DEGRADED`: transport latency exceeds its declared limit; action is blocked.
- `STALE`: the event is older than its declared limit; action is blocked.
- `UNAVAILABLE`: provenance is missing or the timestamp chain is impossible; action is blocked.

This contract is venue-neutral and read-only. It performs no network request, write, experiment advancement, decision resolution, wallet access, signing, order submission, or state mutation. Limits are consumers' preregistered parameters rather than hidden defaults.

## Consumption-time qualification

Capture-time quality is necessary but not sufficient. A market observation that was `VALID` when captured can be stale by the time a strategy consumes it.

`autonomous_kernel.market_observation_qualification.qualify_observation` therefore re-evaluates the original provider timestamp and receive timestamp at the exact consumption time. Qualification fails closed unless all of the following are true:

1. The immutable observation content hash validates.
2. Stored capture-time quality is `VALID` and action-permitted.
3. Consumption-time freshness is still `VALID` under the declared age limits.
4. Any channel that claims sequence continuity has independently qualified sequence evidence.

A consumer must never infer freshness from the fact that an observation exists in the repository.

## Sequence integrity

Sequenced microstructure streams are qualified from their preserved replay summary, not from venue-specific assumptions. A stream must prove:

- connection-global sequence scope;
- an initial Level 2 snapshot;
- at least one unique message;
- zero observed sequence gaps;
- zero out-of-order messages; and
- a deterministic final order-book hash.

Duplicates are counted and preserved as evidence. They are not treated as gaps, but conflicting duplicates remain an integrity failure in the stream journal/replay layer.

Snapshot-only and non-sequenced channels are explicitly `NOT_APPLICABLE`; they are not falsely labeled sequence-complete.

## Evidence-bound shadow observations

`bind_shadow_decision` creates a prospective cryptographic bond between a shadow decision and the exact qualified market observations it consumed. Each observation binding preserves:

- observation ID;
- provider and instrument;
- channel;
- immutable observation content hash;
- source-event and receive timestamps;
- exact consumption time;
- consumption-time quality result; and
- sequence-integrity result when applicable.

Evidence-bond schema version 2 also binds the decision semantics that the evidence supported:

- decision ID and product;
- observation and actionable timestamps;
- signal-candle timestamp when applicable;
- target position;
- strategy ID and rationale code;
- explicit freshness policy;
- zero-capital effect; and
- absence of execution authority.

Changing the target, strategy attribution, rationale, timing, freshness limits, authority, or any bound market observation after creation changes the expected bond hash and blocks qualification.

The complete decision/evidence core is hashed into `market_evidence_bond`. Monitoring re-computes the bond, reloads the immutable observations, verifies the bound hashes and metadata, and re-runs qualification at the original decision timestamp. Naming an observation ID is therefore not sufficient evidence by itself.

A candle used as signal evidence must also match the decision's recorded signal-candle timestamp.

## Successor qualified shadow state

New evidence-bound decisions belong to `state/qualified_market_shadow.json`, owned by program `QUALIFIED-MARKET-SHADOW-V1` in mode `zero_capital_evidence_bound_shadow`.

This state is deliberately separate from `state/market_shadow.json`. The latter remains the historical EXP-MKT-002 state and is not modified by the successor writer.

`autonomous_kernel.qualified_shadow.record_qualified_shadow_decision` accepts an explicit decision proposal and explicit immutable observation IDs. It does not fetch data, invent a signal, select a strategy, execute an order, access a wallet, sign anything, or move capital. Before persistence it requires:

1. a strictly prospective decision (`actionable_at > observed_at`);
2. a target position of `-1`, `0`, or `1`;
3. explicit strategy and rationale identifiers;
4. explicit positive event-age and transport-age limits;
5. one or more unique observation IDs already present in the immutable market-data store;
6. instrument agreement between every observation and the decision;
7. successful consumption-time market-data qualification; and
8. signal-candle agreement for candle evidence when a signal timestamp is declared.

Persistence is atomic, serialized by the kernel writer lease, and idempotent for the same decision content. Reusing a decision ID with different content is an error. Each persisted decision also carries a full `decision_content_hash`, so canonical validation detects mutation outside the writer even before certification is considered.

The writer snapshots the bytes of `state/market_shadow.json` and verifies they remain unchanged. It therefore has no authority to retrofit or rewrite EXP-MKT-002.

The command-line entry point is:

```text
python -m autonomous_kernel qualified_shadow_record \
  --decision-id <id> \
  --product <instrument> \
  --observed-at <epoch-seconds> \
  --actionable-at <future-epoch-seconds> \
  --target-position <-1|0|1> \
  --strategy-id <strategy-id> \
  --rationale-code <rationale-code> \
  --observation-id <immutable-observation-id> \
  --max-event-age-seconds <explicit-limit> \
  --max-transport-age-seconds <explicit-limit>
```

`--signal-candle-timestamp` is supplied when candle evidence is part of the declared signal lineage. Additional `--observation-id` arguments may bind multiple observations to the same decision.

This command is a persistence/qualification boundary, not a strategy engine. An upstream deterministic or model-governed strategy component must separately earn the authority to propose the target and rationale.

## Operational observer handoff

The continuous observer now has a deliberately neutral acceptance handoff. `config/qualified_shadow.json` preregisters the only allowed v1 behavior:

- handoff mode `PERCEPTION_ACCEPTANCE_ONLY`;
- target position `0`;
- strategy ID `PERCEPTION-PIPELINE-QUALIFICATION-V1`;
- rationale `NO_TRADING_SIGNAL_PERCEPTION_ACCEPTANCE`;
- explicit 30-second event-age and transport-age limits;
- capital effect `NONE`; and
- execution authority `false`.

`experiments/market_observer.py` invokes `autonomous_kernel.joined_shadow_observer.join_observer_window` only after `run_observer_once` returns a successful `CAPTURED` window. The handoff accepts only the canonical immutable `microstructure_stream_summary` for the same stream ID. It then re-evaluates freshness and sequence integrity at consumption time and, when qualified, writes a target-0 evidence-bound successor decision.

The operational order is:

```text
public capture
  -> immutable stream/observation validation
  -> consumption-time freshness + sequence qualification
  -> neutral target-0 successor evidence bond
  -> successor-state validation / monitor certification
  -> verified raw-journal compaction
```

A stale first handoff is skipped and creates no successor decision. A handoff implementation/configuration error is reported separately and does not turn a successful public capture into a false market-data failure or a trading action. Raw-journal compaction still follows its independent verification contract.

Retries are keyed by observer window identity. A retry may return `ALREADY_JOINED_NEUTRAL_PERCEPTION` only after revalidating the existing successor state, the exact bound immutable observation, the evidence bond, and current successor certification. Corrupted or semantically altered joined state fails closed rather than masquerading as an idempotent success.

This target-0 handoff proves the perception-to-evidence-to-decision join. It does **not** constitute a BUY, SELL, HOLD strategy recommendation, an economic-edge claim, an execution request, or authority to move capital.

## Monitoring and certification

The authoritative monitor exposes the complete read-only qualification audit under `sections.market_data.data.qualification`.

The market plane reports observation quality, sequence-integrity states, immutable-store validation, and stream-bundle replay validation. The shadow-evidence plane separately reports:

- legacy decision count and `LEGACY_UNJOINED` decisions;
- successor decision count;
- successor joined, qualified, and blocked counts;
- all joined/qualified/blocked counts across the audit; and
- the resulting successor certification state.

A bound object appearing in legacy EXP-MKT-002 input may be audited for integrity, but it cannot earn successor certification. Certification is earned only from decisions originally persisted through the successor evidence-bound state.

Successor joined-shadow certification becomes `QUALIFIED` only when at least one successor decision is evidence-bound, every successor joined decision independently re-qualifies, the immutable market-data store validates, and all preserved stream bundles validate. Missing, stale, tampered, mismatched, sequence-invalid, or semantically altered evidence makes certification `BLOCKED`.

With an empty successor state the certification remains `NOT_EARNED`. That remains the repository's correct runtime state until the owner's active observer produces a genuine fresh window and the operational handoff consumes it prospectively. Tests may prove the transition synthetically; synthetic fixtures do not earn runtime certification.

Canonical durable-state validation also requires the successor state and neutral handoff policy in a full kernel repository. Deleting either, mutating a `decision_content_hash`, removing an evidence bond, introducing capital effect, enabling execution authority, duplicating a decision ID, corrupting the policy, or making the summary inconsistent therefore causes repository validation to fail closed.

## EXP-MKT-002 boundary

EXP-MKT-002 is intentionally not retrofitted to this contract mid-experiment. Changing its observation schema or gate while it is running would contaminate the preregistered forward test. Existing decisions that never recorded a market-evidence bond are reported as `LEGACY_UNJOINED`; matching them to later-discovered data is forbidden.

Future experiments and shadow strategies may preregister this contract before their first observation. Their decisions can then earn `QUALIFIED` joined-shadow status prospectively through the successor state.

## Venue neutrality

Coinbase is the current evidence source, not an architectural dependency. The qualification contract operates on normalized observation provenance and replay evidence rather than Coinbase-specific field names above the adapter boundary. Future Kraken, Alpaca, and other approved venue adapters must produce the same canonical observation guarantees before their data can be joined into ZLJ's perception layer.
