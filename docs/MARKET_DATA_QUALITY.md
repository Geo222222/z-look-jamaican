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

`bind_shadow_decision` creates a prospective cryptographic bond between a shadow decision and the exact qualified market observations it consumed. Each binding preserves:

- observation ID;
- provider and instrument;
- channel;
- immutable observation content hash;
- source-event and receive timestamps;
- exact consumption time;
- consumption-time quality result; and
- sequence-integrity result when applicable.

The complete binding set is hashed into `market_evidence_bond`. Monitoring re-computes the bond, reloads the immutable observations, verifies the bound hashes and metadata, and re-runs qualification at the original decision timestamp. Naming an observation ID is therefore not sufficient evidence by itself.

A candle used as signal evidence must also match the decision's recorded signal-candle timestamp.

## Monitoring and certification

The authoritative monitor exposes the complete read-only qualification audit under `sections.market_data.data.qualification`.

The market plane reports observation quality, sequence-integrity states, immutable-store validation, and stream-bundle replay validation. The shadow-evidence plane separately reports legacy unjoined decisions, prospectively joined decisions, qualified joins, blocked joins, and the resulting certification state.

Prospective joined-shadow certification is earned only when at least one decision was originally bound to qualified evidence and every joined decision remains verifiable. Missing, stale, tampered, mismatched, or sequence-invalid evidence blocks qualification.

## EXP-MKT-002 boundary

EXP-MKT-002 is intentionally not retrofitted to this contract mid-experiment. Changing its observation schema or gate while it is running would contaminate the preregistered forward test. Existing decisions that never recorded a market-evidence bond are reported as `LEGACY_UNJOINED`; matching them to later-discovered data is forbidden.

Future experiments and shadow strategies may preregister this contract before their first observation. Their decisions can then earn `QUALIFIED` joined-shadow status prospectively.

## Venue neutrality

Coinbase is the current evidence source, not an architectural dependency. The qualification contract operates on normalized observation provenance and replay evidence rather than Coinbase-specific field names above the adapter boundary. Future Kraken, Alpaca, and other approved venue adapters must produce the same canonical observation guarantees before their data can be joined into ZLJ's perception layer.
