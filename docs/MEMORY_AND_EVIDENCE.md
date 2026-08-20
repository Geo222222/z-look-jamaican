# Memory and Evidence Protocol

The autonomous system must accumulate institutional memory so it does not repeatedly rediscover the same facts, repeat failed experiments, or lose the rationale behind prior decisions.

## Memory classes

Persist at minimum:

- decisions;
- experiments;
- rejected opportunities;
- active hypotheses;
- deployments;
- incidents;
- postmortems;
- performance summaries;
- architecture decisions;
- external dependency changes;
- Governor change proposals;
- security findings;
- economic models and their calibration history.

## Canonical record shape

Important records should contain:

```yaml
id: globally-unique-id
created_at: ISO-8601
created_by: agent-or-service-id
type: decision|experiment|incident|deployment|reflection|rejection
subject: stable-subject-id
hypothesis: text
assumptions:
  - text
evidence:
  - immutable-reference
expected_outcome: text
observed_outcome: text|null
confidence: 0.0-1.0
decision: text
reversal_conditions:
  - text
related_records:
  - id
code_version: commit-or-image-digest|null
```

## Evidence rules

Prefer immutable or reproducible evidence references:
- commit SHAs;
- container image digests;
- database record IDs;
- object-store URIs with checksums;
- transaction hashes;
- signed/dated data snapshots;
- test run IDs;
- deployment IDs;
- primary-source URLs plus retrieval timestamps where external research is involved.

Generated prose alone is not evidence.

## Failed experiments are assets

A failed experiment must preserve:
- what was attempted;
- why it was expected to work;
- what falsified it;
- whether failure was economic, technical, security-related, legal/compliance-related, or operational;
- conditions that justify reopening it.

Do not reopen a rejected idea merely because a new agent has not read the old conclusion.

## Working memory vs durable memory

Temporary reasoning may remain ephemeral. Decisions that alter roadmap, capital eligibility, production state, architecture, or external behavior must be converted into durable records.

## Memory retrieval

Before pursuing a material opportunity or changing a material component:
1. search memory for the subject and adjacent failure modes;
2. retrieve prior decisions and reversal conditions;
3. identify whether genuinely new evidence exists;
4. proceed only after reconciling the new work with that history.

## Initial implementation guidance

The agent may begin with versioned Markdown/JSONL records in the repository for bootstrap simplicity, then migrate operational records to Postgres/object storage when the runtime exists. The migration must preserve stable IDs and historical evidence.
