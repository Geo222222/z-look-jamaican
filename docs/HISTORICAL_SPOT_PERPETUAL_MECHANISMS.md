# Historical spot/perpetual mechanism workbench

## Purpose

This is a bounded falsification instrument, not another general operating-system layer. It asks whether a stated economic mechanism survives unseen historical periods, funding, and conservative costs before Z Look Jamaican spends weeks collecting prospective or Level 2 evidence.

History occupies one place in the prediction path:

1. Public history cheaply rejects mechanisms with no durable gross edge.
2. A fully passing family may nominate one new, separately preregistered prospective shadow on the exact intended exchange and products.
3. Prospective data tests whether the historical relationship survives forward observation and venue transfer.
4. Level 2 and execution telemetry calibrate spread, queue, slippage, latency, fill, capacity, and rejection assumptions for a survivor.
5. The deterministic Governor remains the final capital boundary. Historical or prospective returns cannot bypass it.

## What is paired

The comparison key is `(exchange, asset)`. Each row names both markets:

| Exchange | Asset | Spot leg | Future-equivalent leg |
|---|---|---|---|
| Binance | BTC | Binance spot `BTCUSDT` | Binance USD-M perpetual `BTCUSDT` |
| Binance | ETH | Binance spot `ETHUSDT` | Binance USD-M perpetual `ETHUSDT` |

No Binance leg is compared with a futures price from another exchange. Binance is the first bounded venue because its official public archive exposes checksum files for spot klines, USD-M perpetual klines, premium-index klines, and funding rates. Adding another exchange requires a new preregistration and that exchange's own spot/future pair and cost model.

## What the data means

Data quality and economic quality are separate gates.

- Data quality asks whether the archives are authentic, timestamps are ordered, spot and perpetual bars are complete and aligned, volumes are internally coherent, and missing auxiliary fields are explicit.
- Economic quality asks whether signals entered at the next bar open have positive net expectancy in later walk-forward folds after funding and 5, 10, 20, and 40 basis points per trading side.

The five-minute bars contain OHLC prices, quote volume, trade count, and taker-buy quote volume. They support completed-bar returns and executed-flow imbalance. They do not expose queue position or full Level 2 state.

Derived paired features include:

- spot and perpetual bar returns;
- spot and perpetual taker imbalance;
- same-exchange perpetual basis and basis change;
- official premium index;
- official funding crossed during a position;
- paired spot/perpetual breakout confirmation.

## Frozen mechanisms

The workbench evaluates exactly five families and fourteen asset/target streams:

- confirmed flow continuation, separately measured on spot and perpetual for BTC and ETH;
- perpetual-leading-spot continuation for BTC and ETH;
- spot-leading-perpetual continuation for BTC and ETH;
- equal-notional long-spot/short-perpetual basis convergence for BTC and ETH;
- confirmed slow breakout, separately measured on spot and perpetual for BTC and ETH.

Thresholds are learned only from rows before each 30-day test fold after a 90-day warmup. Entry is the next five-minute open. Streams cannot overlap their own positions. Directional trades pay two sides; the basis pair pays four. Positive funding is subtracted from a long perpetual and added to a short perpetual.

## Data-quality failure and recovery

`EXP-HISTORICAL-MECHANISMS-001` failed closed before returns were evaluated. All 96 archives passed their official SHA-256 checksums and both spot/perpetual pairs had 105,120 continuous rows, but both premium-index series omitted the same 288 five-minute observations on 2026-06-29 UTC.

`EXP-HISTORICAL-MECHANISMS-002` is a hash-pinned child. It changes no economic rule. Complete spot/perpetual rows remain intact; the auxiliary premium value is explicitly unavailable for those 288 timestamps, is never interpolated, and blocks only a basis signal needing the missing current premium observation.

## Reproduce and inspect

```powershell
python -m experiments.historical_mechanisms run --root .
```

Durable outputs:

- `artifacts/evidence/market/exp-historical-mechanisms-001-result.json`: the preserved strict-alignment failure;
- `artifacts/evidence/market/exp-historical-mechanisms-002-preregistration.json`: the child design frozen before economic evaluation;
- `artifacts/market_data/historical/binance-spot-perpetual-5m-2025-08_2026-07.manifest.json`: source URLs, official checksums, archive members, row counts, and timestamps;
- `artifacts/evidence/market/exp-historical-mechanisms-002-result.json`: machine-readable fold, stream, cost, funding, quality, and decision evidence;
- `artifacts/evidence/market/exp-historical-mechanisms-002-report.md`: human-readable result.

The Binance archive format is documented in the [official Binance public-data repository](https://github.com/binance/binance-public-data/blob/master/README.md). The mechanism choices are consistent with public research on [order-flow imbalance](https://arxiv.org/abs/1011.6402), while the walk-forward and multiple-testing posture exists to avoid turning repeated backtests into fabricated confidence.
