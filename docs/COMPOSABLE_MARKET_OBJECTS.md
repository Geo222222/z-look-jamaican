# Composable market object model

Z Look Jamaican now represents a market as an immutable, reference-linked object graph:

`evidence → measurement → derived math → structure/perception/context → state → transition → story → strategy applicability → opportunity`

`MARKET_WORLD_SNAPSHOT` is a reference-only index. It does not duplicate the world into one mutable JSON document. Every object records its truth class, source time, method/version, quality, parent references, content hash, and a hard-coded absence of execution or capital authority.

## Epistemic boundaries

- Evidence preserves source records without interpretation.
- Measurements normalize evidence.
- Derived math calculates indicators/statistics without assigning meaning.
- Structure describes deterministic geometry. Chart perception is a secondary channel and cannot override underlying data.
- States classify a dimension and point to evidence; missing inputs become explicit `UNAVAILABLE` objects.
- Transitions compare prior and current state objects.
- Stories are competing hypotheses with contradictions and invalidation—not facts.
- Strategy applicability matches the story graph to the versioned registry. It is not a trade.
- Opportunities require defined entry, exit, risk, cost, liquidity, portfolio, and earned-economics gates. They still cannot authorize execution.

## Inspect tonight's historical graph

Build/rebuild from the checksum-verified Binance history:

```powershell
python -m experiments.market_object_replay --bars 250
```

Start with `state/market_objects.json`, then open one of the four `WORLD-BINANCE-*` snapshots under `artifacts/market_objects/index/`. Follow `market://...` references through story, transition, state, structure, math, measurement, and raw evidence files.

The replay covers BTC and ETH, both Binance spot and the same-exchange USDT perpetual equivalent. Historical candles do not contain contemporaneous order-book depth/spread, so liquidity is deliberately unavailable and every present opportunity candidate is blocked. That is the intended behavior: missing data cannot be silently converted into confidence.

## Learning contract

Applicability is a context label. Later realized P&L, fills, costs, adverse excursion, and invalidation are outcome labels. Outcome evidence must be time-forward and may never leak into the object graph that produced the earlier applicability decision. Training examples pin registry version, object hashes, classifier versions, and horizon.
