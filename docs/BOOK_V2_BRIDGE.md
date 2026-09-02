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

- `ZLJ.INTELLIGENCE`
- `ZLJ.PREDICTION`
- `ZLJ.MODEL_QUALIFICATION`
- `ZLJ.CALIBRATION`
- `ZLJ.DATA_QUALITY_INCIDENT`
- `ZLJ.EVALUATION`
- `ZLJ.JOURNAL_COMMITMENT`

Not every observation or feature becomes an individual Book receipt. High-volume complete histories use journal commitments under The Book materiality policy.

## Timing

Every v2 envelope cryptographically binds:

- `occurred_at`
- `source_event_at` where applicable
- `known_at`
- `produced_at`
- `valid_from` / `valid_until` where applicable

This is required to preserve anti-hindsight and calibration integrity.

## Failure posture

If the signer is unavailable or key material is missing, ZLJ may continue local perception/model work according to its own operating policy, but it must not pretend Book evidence was durably emitted. Material bridge delivery is handled through the durable outbox milestone rather than by silently dropping evidence.
