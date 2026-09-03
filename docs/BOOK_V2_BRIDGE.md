# ZLJ -> The Book v2 Bridge

ZLJ owns perception/model truth and may sign only `ZLJ.*` Book Evidence Protocol v2 events.

## Producer identity

Runtime signing uses an Ed25519 key held inside ZLJ's secret boundary.

Required runtime secret inputs:

- `ZLJ_BOOK_KEY_ID`
- `ZLJ_BOOK_ED25519_PRIVATE_KEY_B64`

The private key must never be committed to Git, persisted in ordinary ZLJ memory, included in reports, or sent to The Book.

The corresponding public identity contains:

```text
producer: ZLJ
key_id: <runtime key id>
allowed_event_prefixes: [ZLJ.]
public_key_b64: <public key only>
```

The Book registers the public key and namespace authority. ZLJ cannot sign as Benjamin, Watchman, The Hand, The Martians, or The Book.

## Target events

The bridge is intended for material evidence such as:

- `ZLJ.ECONOMIC_INSTRUMENT_GRAPH`
- `ZLJ.EXPERIENCE_JOURNAL_COMMITMENT`
- `ZLJ.INTELLIGENCE`
- `ZLJ.PREDICTION`
- `ZLJ.MODEL_QUALIFICATION`
- `ZLJ.CALIBRATION`
- `ZLJ.DATA_QUALITY_INCIDENT`
- `ZLJ.EVALUATION`
- `ZLJ.JOURNAL_COMMITMENT`

### Economic Instrument Graph

An activated/versioned Economic Instrument Graph is material because later market-experience joins depend on its structural claims: same economic root, spot/derivative relationship, term-structure membership, quote family, basket membership, and other declared structural relationships.

The graph must not contain time-varying empirical claims such as `futures currently lead spot`. Those belong in causal Market Experience / relationship state and are evaluated from evidence at a particular cutoff.

### Market Experience

High-volume Market Experience frames remain ZLJ-owned learning artifacts. A frame binds exact graph/context/source-frame lineage and contains only information knowable at its cutoff; later outcomes are separate objects and never mutate the original experience.

The Book does **not** receive one receipt for every observation, representation, or Market Experience frame. ZLJ periodically emits `ZLJ.EXPERIENCE_JOURNAL_COMMITMENT` containing a compact commitment to a contiguous local journal range, including:

- journal identity;
- start/end sequence;
- event count;
- first/last journal-event hashes;
- digest of the committed event-hash range;
- last experience identity/cutoff;
- commitment known-at time.

The underlying raw ticks, source observations, and representation-frame lists stay in ZLJ unless a later evidence request or materiality policy requires a specific artifact. This preserves both complete ZLJ learning history and The Book's minimum-necessary-evidence principle.

Not every observation or feature becomes an individual Book receipt. High-volume complete histories use journal commitments under The Book materiality policy.

## Timing

Every v2 envelope cryptographically binds:

- `occurred_at`
- `source_event_at` where applicable
- `known_at`
- `produced_at`
- `valid_from` / `valid_until` where applicable

This is required to preserve anti-hindsight and calibration integrity.

Market Experience adds an additional rule: a causal experience at cutoff `T` may reference only graph/context/source state knowable no later than `T`. Future realized paths and outcomes are attached later through separate evaluation objects.

## Failure posture

If the signer is unavailable or key material is missing, ZLJ may continue local perception/model work according to its own operating policy, but it must not pretend Book evidence was durably emitted. Material bridge delivery is handled through the durable outbox milestone rather than by silently dropping evidence.
