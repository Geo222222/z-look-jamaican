# Z9 — Market Context

Z9 is ZLJ's market-wide contextual representation layer. It answers **what environment surrounds an instrument right now?** It does not decide whether Epinnox should trade.

Canonical flow:

```text
Z1 canonical observations
        ↓
Z2 point-in-time INSTRUMENT_STATE frames
        ↓
Z3 predictions / Z4 models / Z5 lifecycle / Z6 outcomes / Z7 competence
        ↓
Z8 competence-weighted assembly
        ↓
Z9 MARKET_CONTEXT + bounded relevance overlay
        ↓
ZLJ intelligence for Benjamin
```

## Constitutional boundary

Z9 owns representation of broader market context: cross-instrument structure, market breadth, volatility, liquidity, correlation, venue dislocation, spot/derivative basis and an explicitly non-causal lead/lag proxy. Z9 owns **no** trade intent, capital sizing, mandate/risk authorization, credential use, order placement, settlement, or authoritative cross-system proof.

A Z9 frame is derived only from exact Z2 frames. Every source frame ID and SHA-256 content hash is part of the Z9 content hash. Future-known Z2 frames are ineligible for operational materialization, and the builder independently rejects any source that violates its point-in-time cutoff.

## Provider neutrality

Z9 joins canonical economic instrument identities, never provider symbols. Venue/provider-specific parsing remains below Z2. Spot/derivative relationships require matching asset class, base asset and quote asset; no symbol-string guessing is permitted.

## Authoritative operational materialization

Z9 has one canonical runtime path for producing context from durable Z2 evidence:

```text
durable Z2 representation store
        ↓ validate the complete indexed store
select every INSTRUMENT_STATE where
    known_at_ns  <= T
    cutoff_at_ns <= T
        ↓ deterministic instrument/time ordering
retain admissible history for return/volatility/correlation/lead-lag
        ↓
build_market_context(..., cutoff_at_ns=T)
        ↓
verify exact source IDs + content hashes + instrument lineage
        ↓
persist immutable MARKET_CONTEXT
        ↓
re-read artifact + validate Z9 store
```

The operational entrypoint is `materialize_market_context`. It does not allow a caller to cherry-pick an arbitrary subset of durable frames. The complete Z2 store is validated before selection; a corrupted indexed Z2 artifact blocks materialization. Repeating the same cutoff against the same durable evidence is idempotent.

CLI entrypoint:

```bash
python -m autonomous_kernel context_materialize --cutoff-at-ns <T>
```

## Context content

Each `MARKET_CONTEXT` records:

- exact member Z2 frame, status, age, freshness and reliability;
- midpoint, spread, selected depth liquidity and reported-flow ratio;
- point-in-time return history and realized-volatility proxy;
- market-wide aggregate return and positive breadth;
- cross-sectional return dispersion;
- median spread and volatility;
- liquidity concentration (HHI);
- time-aligned pairwise return correlations and median absolute correlation;
- exact spot/derivative basis and annualized basis when a dated expiry is parseable;
- spot-leading / derivative-leading **lag proxy** with sample count, explicitly not causality;
- feature-quality states so missing derivatives or correlation data remain visible;
- regimes for direction, volatility, liquidity, correlation, derivatives and structural dislocation.

Missing context does not become a made-up neutral fact. Feature families are `QUALIFIED`, `DEGRADED` or `UNAVAILABLE`.

## Z8 + Z9 assembly

Z8 remains historically immutable and independently reproducible. Z9 does not rewrite Z7 competence or old Z8 weights.

Contextual assembly starts from each contributor's recorded Z8 weight and applies only an explicit relevance overlay:

```text
context_multiplier =
    feature-quality factor
  × declared-regime relevance factor
  × target-data reliability factor
  × freshness factor
  × diversity adjustment

0.75 <= context_multiplier <= 1.25

final_weight_i =
  (z8_weight_i × context_multiplier_i)
  / Σ(z8_weight × context_multiplier)
```

## Governed ModelContextProfile registry

A `ModelContextProfile` is policy evidence, not a caller hint. Each exact model version may have immutable profile versions declaring:

- Z9 feature dependencies;
- preferred regimes;
- adverse regimes;
- diversity group;
- profile version and content hash.

Profile existence and profile authority are separate. Registration creates an immutable artifact bound to the exact Z5 `model_ref`, model-definition hash and model-artifact hash. Activation is a separate append-only event with evidence references. A later profile version does not rewrite an earlier one.

```text
exact Z5 model identity
        ↓
immutable ModelContextProfile artifact
        ↓ PROFILE_REGISTERED
append-only profile event journal
        ↓ explicit evidence
PROFILE_ACTIVATED
        ↓
active policy as-of time T
```

Canonical contextual assembly does **not** accept a caller-supplied `profiles` collection. It resolves exactly one active registered profile for every contributing model from durable state. Missing or ambiguous profile authority fails closed.

Profile authority must be strictly prior to an assembly: `activated_at_ns < assembly_at_ns`. This prevents a profile activated later with the same timestamp from retroactively changing the meaning of an already-recorded contextual receipt.

The contextual receipt binds the exact profile-set hash and each contributor's profile hash. Lineage validation re-resolves the historically active registered profiles at the causal policy cutoff and verifies those hashes, so a syntactically valid but unregistered profile cannot explain a Z9 weight adjustment.

## Durability

```text
artifacts/market_data/contexts/<context_id>.json   immutable Z9 context
state/market_context.json                          rebuildable discovery index
artifacts/model_context_profiles/<profile_id>.json immutable context policy artifacts
memory/model_context_profile_events.jsonl          append-only registration/activation chain
state/model_context_profiles.json                  rebuildable profile projection
memory/contextual_assemblies.jsonl                 append-only contextual receipt chain
state/contextual_assembly_journal.json             rebuildable contextual journal head
```

A persisted Z9 context requires every source Z2 frame to already be durably stored. The canonical contextual service requires the context artifact and governed active model-context profiles before context can influence assembly.

## Operations

Read-only status:

```bash
python -m autonomous_kernel context_status
```

Full kernel validation validates the Z9 store, model-context profile registry, contextual journal and complete Z8→Z9 lineage.

## Certification

`artifacts/evidence/market/z9-certification-policy-v1.json` freezes the empirical gates before Z9 results exist. Construction can be complete while empirical claims remain `DATA_BLOCKED`.

Market-wide historical support requires at least 12 qualified contexts over 3 UTC dates and 3 six-hour buckets, with at least two qualified spot instruments per context. Spot/derivative support has its own 12-context / 3-date / 3-bucket requirement. Contextual assembly cannot be walk-forward scored until both the Z8 broad prerequisite and the Z9 market-wide prerequisite are met; then it requires 100 resolved contextual predictions across four chronological folds and comparison against the same uncontextualized Z8 baseline.

No certification status can authorize capital or external execution.
