# Memory and Evidence Protocol

ZLJ must accumulate enough durable research, model, data-quality, and experiment memory to avoid repeatedly rediscovering the same facts or repeating failed model work.

That local memory is **not the same thing as The Book**.

- **ZLJ local/durable memory** supports research, engineering, model qualification, calibration, and recovery.
- **The Book** is the authoritative cross-system evidence/memory/proof substrate for material Epinnox lineage.
- **Benjamin** owns the interpretation and use of decision memory for capital reasoning.

## Memory classes

Persist at minimum where relevant:

- market/data-source decisions;
- experiments;
- rejected hypotheses;
- active hypotheses;
- model versions and qualification state;
- prediction records;
- calibration and competence history;
- drift/distribution-shift evidence;
- incidents and postmortems;
- performance summaries by instrument/horizon/regime;
- architecture decisions;
- external dependency changes;
- security findings;
- feature and label definitions;
- model succession decisions.

ZLJ should not attempt to become the canonical store for Benjamin decisions, Watchman authorizations, Hand executions, or institution-wide accounting.

## Canonical record shape

Important ZLJ records should contain enough information to reconstruct what was knowable and how an intelligence object was produced:

```yaml
id: globally-unique-id
created_at: ISO-8601
created_by: agent-or-service-id
type: observation|experiment|prediction|evaluation|incident|model_decision|reflection
subject: stable-subject-id
instrument: optional
horizon: optional
hypothesis: text|null
assumptions:
  - text
evidence:
  - immutable-or-governed-reference
source_timestamp: optional
ingested_at: optional
known_at: optional
expected_outcome: text|null
observed_outcome: text|null
confidence: 0.0-1.0|null
qualification_state: optional
model_version: optional
feature_version: optional
reversal_or_invalidation_conditions:
  - text
related_records:
  - id
code_version: commit-or-image-digest|null
book_evidence_ref: optional
```

## Evidence rules

Prefer immutable or reproducible evidence references:

- commit SHAs;
- container image digests;
- database record IDs;
- object-store URIs with checksums;
- signed/dated data snapshots;
- test run IDs;
- model artifact hashes;
- deployment IDs;
- primary-source URLs plus retrieval timestamps where external research is involved;
- The Book evidence references for material cross-organ lineage.

Generated prose alone is not evidence.

## Prediction and outcome separation

Predictions must be recorded before their outcome label becomes knowable. Later outcomes must be attached without rewriting the original prediction.

For short-horizon trading intelligence, preserve enough timing information to prevent look-ahead leakage and to evaluate calibration honestly.

## Failed experiments are assets

A failed experiment must preserve:

- what was attempted;
- why it was expected to work;
- what falsified it;
- whether failure was statistical, data-quality, latency, technical, security-related, legal/compliance-related, or operational;
- conditions that justify reopening it.

Do not reopen a rejected idea merely because a new agent has not read the old conclusion.

## Working memory vs durable memory

Temporary reasoning may remain ephemeral. Decisions that alter ZLJ architecture, production data/model state, model qualification, calibration policy, or the intelligence contract presented to Benjamin must become durable records.

Material intelligence used in an Epinnox capital decision must be able to carry provenance into The Book without requiring The Book to store every raw feature, prompt, dataset row, or private research artifact.

## Bridge to Benjamin and The Book

A useful lineage is:

```text
ZLJ observation / model evidence
        |
        v
ZLJ intelligence object
        |
        +------> The Book: provenance / material evidence reference
        |
        v
Benjamin decision context
        |
        v
Benjamin decision
        |
        v
Watchman governance
        |
        v
The Hand execution
        |
        v
The Book outcome lineage
        |
        +------> later evaluation/calibration feedback to ZLJ and Benjamin
```

ZLJ owns its prediction/model history. The Book owns authoritative cross-organ lineage. Benjamin owns decision cognition. These responsibilities should remain separate even when the same event is referenced by all three.

## Memory retrieval

Before changing a material model, feature, data source, or market hypothesis:

1. search ZLJ memory for the subject and adjacent failure modes;
2. retrieve prior experiments, model decisions, and reversal conditions;
3. identify whether genuinely new evidence exists;
4. reconcile the new work with relevant Book lineage where the prior capability influenced Epinnox decisions;
5. proceed only after that history is understood.

## Initial implementation guidance

Versioned Markdown/JSONL, Postgres, object storage, model registries, and The Book references may coexist. Storage technology is secondary to stable identity, reproducibility, provenance, timing integrity, and clear ownership.
