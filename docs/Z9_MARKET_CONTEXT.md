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

A Z9 frame is derived only from exact Z2 frames. Every source frame ID and SHA-256 content hash is part of the Z9 content hash. The operational materializer selects only durable Z2 frames whose `known_at_ns`, representation cutoff and latest source-event time are all no later than requested cutoff `T`. Any selected source that violates that point-in-time boundary fails closed.

## Canonical operational materialization

The pure `build_market_context()` function remains available for research and deterministic unit tests. It is **not** the authoritative durable runtime path.

The canonical operational path is:

```text
durable Z2 representation artifacts
        ↓
rebuild + validate Z2 discovery index
        ↓
select all INSTRUMENT_STATE frames knowable at cutoff T
        ↓
build_market_context(..., cutoff_at_ns=T)
        ↓
verify exact builder lineage == selected durable source set
        ↓
MarketContextStore.persist
        ↓
reload exact durable context
        ↓
immutable materialization receipt
```

Command:

```bash
python -m autonomous_kernel context_materialize --cutoff-at-ns <T>
```

Materialization policy `Z9_DURABLE_POINT_IN_TIME_MATERIALIZER_V1` binds the cutoff, selection rule, exact source frame IDs/hashes/instruments, source-set hash, context hash, builder version and persisted context-artifact hash.

A context that was manually built and persisted without this receipt is valid research data but is **not accepted by the canonical Z8+Z9 runtime service**.

## Provider neutrality

Z9 joins canonical economic instrument identities, never provider symbols. Venue/provider-specific parsing remains below Z2. Spot/derivative relationships require matching asset class, base asset and quote asset; no symbol-string guessing is permitted.

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

## Governed ModelContextProfile registry

A model's context policy is not runtime caller input.

Each model/version registers an immutable `ModelContextProfile` declaring:

```text
MODEL-A@1.0.0
   ↓
ContextProfile@1.0
├─ feature dependencies
├─ preferred regimes
├─ adverse regimes
├─ diversity group
└─ profile hash
```

Registration is bound to the governed model registry's exact model reference, model-definition hash, model-artifact hash and original model registration time. A profile also carries governance evidence refs and its own registration time.

The identity pair `(model_ref, profile_version)` is immutable. Changing context policy requires a new profile version; rewriting an existing version fails closed.

Point-in-time resolution is mandatory. At assembly time `T`, the canonical service selects only the latest profile for each contributor that was registered by `T`. A future profile version cannot leak backward into replay.

Registration command accepts a JSON declaration:

```bash
python -m autonomous_kernel context_profile_register \
  --profile path/to/profile.json \
  --registered-at-ns <T> \
  --evidence <governance-evidence-ref>
```

Example declaration:

```json
{
  "model_ref": "MODEL-A@1.0.0",
  "profile_version": "1.0",
  "feature_dependencies": ["LIQUIDITY", "CORRELATION"],
  "preferred_regimes": {"structure": ["ORDERLY"]},
  "adverse_regimes": {"volatility": ["HIGH"]},
  "diversity_group": "FLOW_FAMILY"
}
```

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

The low-level deterministic weighting function still accepts explicit profile objects so the mathematics can be unit-tested and replayed independently. The **canonical durable service does not**. It resolves the exact governed profiles itself and fails closed if any contributor lacks a profile valid at the assembly cutoff.

The contextual receipt binds the base Z8 receipt, base Z8 prediction, exact Z9 context, complete profile set, each profile hash, component predictions, all factor values, final weights and reason codes. Therefore a later audit can answer, for example:

> Model A's weight changed because profile hash `H`, registered for `MODEL-A@1.0.0` before this assembly, declared LIQUIDITY/CORRELATION relevance and the observed Z9 regimes triggered these recorded factors.

The final contextual prediction remains a normal Z3 prediction so Z6 can resolve it without learning a second outcome truth model.

## Durability

```text
artifacts/market_data/contexts/<context_id>.json                  immutable Z9 context
artifacts/market_data/context_materializations/<context_id>.json immutable canonical materialization proof
state/market_context.json                                         rebuildable context discovery index
artifacts/model_context_profiles/<profile_id>.json                immutable governed model context policy
state/model_context_profiles.json                                 rebuildable profile discovery index
memory/contextual_assemblies.jsonl                                append-only hash chain
state/contextual_assembly_journal.json                            rebuildable journal head
```

A persisted Z9 context requires every source Z2 frame to already be durably stored. The canonical contextual service requires both the context artifact and canonical materialization receipt before context can influence assembly.

## Operations

Read-only status:

```bash
python -m autonomous_kernel context_status
```

Materialize at cutoff:

```bash
python -m autonomous_kernel context_materialize --cutoff-at-ns <T>
```

Full kernel validation validates the governed model-profile registry, Z9 context store, canonical materialization receipts, contextual journal and Z8→Z9 lineage.

## Certification

`artifacts/evidence/market/z9-certification-policy-v1.json` freezes the empirical gates before Z9 results exist. Construction can be complete while empirical claims remain `DATA_BLOCKED`.

Market-wide historical support requires at least 12 qualified contexts over 3 UTC dates and 3 six-hour buckets, with at least two qualified spot instruments per context. Spot/derivative support has its own 12-context / 3-date / 3-bucket requirement. Contextual assembly cannot be walk-forward scored until both the Z8 broad prerequisite and the Z9 market-wide prerequisite are met; then it requires 100 resolved contextual predictions across four chronological folds and comparison against the same uncontextualized Z8 baseline.

No certification status can authorize capital or external execution.
