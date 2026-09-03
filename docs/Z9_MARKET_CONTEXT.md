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

A Z9 frame is derived only from exact Z2 frames. Every source frame ID and SHA-256 content hash is part of the Z9 content hash. A source whose `known_at_ns` or cutoff is later than the requested Z9 cutoff is rejected rather than silently filtered.

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

Every model must supply a versioned, hashable `ModelContextProfile` declaring its feature dependencies, preferred/adverse regimes and diversity group. No hidden model-family heuristic exists. Missing a profile fails closed.

The contextual receipt binds the base Z8 receipt, base Z8 prediction, exact Z9 context, complete profile set, component predictions, all factor values, final weights and reason codes. The final contextual prediction remains a normal Z3 prediction so Z6 can resolve it without learning a second outcome truth model.

## Durability

```text
artifacts/market_data/contexts/<context_id>.json  immutable Z9 context
state/market_context.json                         rebuildable discovery index
memory/contextual_assemblies.jsonl                append-only hash chain
state/contextual_assembly_journal.json            rebuildable journal head
```

A persisted Z9 context requires every source Z2 frame to already be durably stored. The canonical contextual service requires the context artifact before it can influence assembly.

## Operations

Read-only status:

```bash
python -m autonomous_kernel context_status
```

Full kernel validation also validates the Z9 store, contextual journal and Z8→Z9 lineage.

## Certification

`artifacts/evidence/market/z9-certification-policy-v1.json` freezes the empirical gates before Z9 results exist. Construction can be complete while empirical claims remain `DATA_BLOCKED`.

Market-wide historical support requires at least 12 qualified contexts over 3 UTC dates and 3 six-hour buckets, with at least two qualified spot instruments per context. Spot/derivative support has its own 12-context / 3-date / 3-bucket requirement. Contextual assembly cannot be walk-forward scored until both the Z8 broad prerequisite and the Z9 market-wide prerequisite are met; then it requires 100 resolved contextual predictions across four chronological folds and comparison against the same uncontextualized Z8 baseline.

No certification status can authorize capital or external execution.
