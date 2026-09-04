# Same-exchange spot/perpetual mechanism falsification

Experiment: `EXP-HISTORICAL-MECHANISMS-002`  
Decision: **NO_FAMILY_PASSED**  
Nominated family: **none**  
Capital used: **$0.00**; orders sent: **0**

## What was compared

BTCUSDT and ETHUSDT spot were paired only with their Binance USD-M perpetual equivalents. Every spot and perpetual bar was aligned by timestamp; the known one-day auxiliary premium-index gap remained explicit and blocked only affected basis signals. Official funding was applied to perpetual holdings. The twelve-month history was used for falsification, not as live proof.

## Data-quality result

| Exchange | Asset | Aligned 5m bars | Funding rows | Premium unavailable | Range | Gate |
|---|---:|---:|---:|---:|---|---|
| binance | BTC | 105,120 | 1,095 | 288 | 2025-08-01T00:00:00Z to 2026-07-31T23:55:00Z | PASS |
| binance | ETH | 105,120 | 1,095 | 288 | 2025-08-01T00:00:00Z to 2026-07-31T23:55:00Z | PASS |

All 96 source archives were checked against the official SHA-256 files. The 288 missing premium observations per asset were neither filled nor used. A quality pass only means the evidence is admissible; it does not mean the data contains a profitable signal.

## Family decisions at 20 bps per side

| Mechanism family | Passing streams | Required streams | Decision |
|---|---:|---:|---|
| CONFIRMED-FLOW-CONTINUATION-30M | 0 | 4 | REJECT |
| PERPETUAL-LEADS-SPOT-30M | 0 | 2 | REJECT |
| SPOT-LEADS-PERPETUAL-30M | 0 | 2 | REJECT |
| BASIS-CONVERGENCE-6H | 0 | 2 | REJECT |
| CONFIRMED-SLOW-BREAKOUT-6H | 0 | 4 | REJECT |

## Every frozen asset/target stream

| Exchange | Asset | Mechanism | Target | Trades | Mean net | Net compounded | Positive folds | Adjusted p | Max DD | Gate |
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|
| binance | BTC | CONFIRMED-FLOW-CONTINUATION-30M | spot | 1686 | -0.417% | -99.914% | 0.000% | 1 | 99.914% | FAIL |
| binance | BTC | CONFIRMED-FLOW-CONTINUATION-30M | perpetual | 1686 | -0.417% | -99.914% | 0.000% | 1 | 99.914% | FAIL |
| binance | BTC | PERPETUAL-LEADS-SPOT-30M | spot | 0 | 0.000% | 0.000% | 0.000% | 1 | 0.000% | FAIL |
| binance | BTC | SPOT-LEADS-PERPETUAL-30M | perpetual | 0 | 0.000% | 0.000% | 0.000% | 1 | 0.000% | FAIL |
| binance | BTC | BASIS-CONVERGENCE-6H | long_spot_short_perpetual | 36 | -0.776% | -24.463% | 0.000% | 1 | 24.463% | FAIL |
| binance | BTC | CONFIRMED-SLOW-BREAKOUT-6H | spot | 189 | -0.473% | -59.855% | 0.000% | 1 | 59.855% | FAIL |
| binance | BTC | CONFIRMED-SLOW-BREAKOUT-6H | perpetual | 189 | -0.476% | -60.094% | 0.000% | 1 | 60.094% | FAIL |
| binance | ETH | CONFIRMED-FLOW-CONTINUATION-30M | spot | 1171 | -0.412% | -99.221% | 0.000% | 1 | 99.221% | FAIL |
| binance | ETH | CONFIRMED-FLOW-CONTINUATION-30M | perpetual | 1171 | -0.412% | -99.221% | 0.000% | 1 | 99.221% | FAIL |
| binance | ETH | PERPETUAL-LEADS-SPOT-30M | spot | 0 | 0.000% | 0.000% | 0.000% | 1 | 0.000% | FAIL |
| binance | ETH | SPOT-LEADS-PERPETUAL-30M | perpetual | 0 | 0.000% | 0.000% | 0.000% | 1 | 0.000% | FAIL |
| binance | ETH | BASIS-CONVERGENCE-6H | long_spot_short_perpetual | 76 | -0.775% | -44.624% | 0.000% | 1 | 44.624% | FAIL |
| binance | ETH | CONFIRMED-SLOW-BREAKOUT-6H | spot | 182 | -0.448% | -57.417% | 11.111% | 1 | 57.667% | FAIL |
| binance | ETH | CONFIRMED-SLOW-BREAKOUT-6H | perpetual | 182 | -0.454% | -57.867% | 11.111% | 1 | 58.119% | FAIL |

## Cost sensitivity: mean net return per trade

| Asset | Mechanism | Target | 5 bps/side | 10 bps/side | 20 bps/side | 40 bps/side | Gross break-even/side |
|---:|---|---|---:|---:|---:|---:|---:|
| BTC | CONFIRMED-FLOW-CONTINUATION-30M | spot | -0.117% | -0.217% | -0.417% | -0.817% | -0.836 bps |
| BTC | CONFIRMED-FLOW-CONTINUATION-30M | perpetual | -0.117% | -0.217% | -0.417% | -0.817% | -0.837 bps |
| BTC | PERPETUAL-LEADS-SPOT-30M | spot | 0.000% | 0.000% | 0.000% | 0.000% | 0.000 bps |
| BTC | SPOT-LEADS-PERPETUAL-30M | perpetual | 0.000% | 0.000% | 0.000% | 0.000% | 0.000 bps |
| BTC | BASIS-CONVERGENCE-6H | long_spot_short_perpetual | -0.176% | -0.376% | -0.776% | -1.576% | 0.594 bps |
| BTC | CONFIRMED-SLOW-BREAKOUT-6H | spot | -0.173% | -0.273% | -0.473% | -0.873% | -3.663 bps |
| BTC | CONFIRMED-SLOW-BREAKOUT-6H | perpetual | -0.176% | -0.276% | -0.476% | -0.876% | -3.820 bps |
| ETH | CONFIRMED-FLOW-CONTINUATION-30M | spot | -0.112% | -0.212% | -0.412% | -0.812% | -0.596 bps |
| ETH | CONFIRMED-FLOW-CONTINUATION-30M | perpetual | -0.112% | -0.212% | -0.412% | -0.812% | -0.596 bps |
| ETH | PERPETUAL-LEADS-SPOT-30M | spot | 0.000% | 0.000% | 0.000% | 0.000% | 0.000 bps |
| ETH | SPOT-LEADS-PERPETUAL-30M | perpetual | 0.000% | 0.000% | 0.000% | 0.000% | 0.000 bps |
| ETH | BASIS-CONVERGENCE-6H | long_spot_short_perpetual | -0.175% | -0.375% | -0.775% | -1.575% | 0.634 bps |
| ETH | CONFIRMED-SLOW-BREAKOUT-6H | spot | -0.148% | -0.248% | -0.448% | -0.848% | -2.421 bps |
| ETH | CONFIRMED-SLOW-BREAKOUT-6H | perpetual | -0.154% | -0.254% | -0.454% | -0.854% | -2.708 bps |

## Direct spot-versus-perpetual comparison

| Exchange | Asset | Shared signal | Spot mean net | Perpetual mean net | Perpetual - spot |
|---|---:|---|---:|---:|---:|
| binance | BTC | CONFIRMED-FLOW-CONTINUATION-30M | -0.417% | -0.417% | -0.000% |
| binance | ETH | CONFIRMED-FLOW-CONTINUATION-30M | -0.412% | -0.412% | -0.000% |
| binance | BTC | CONFIRMED-SLOW-BREAKOUT-6H | -0.473% | -0.476% | -0.003% |
| binance | ETH | CONFIRMED-SLOW-BREAKOUT-6H | -0.448% | -0.454% | -0.006% |

## Interpretation and boundary

Do not tune this experiment. Preserve the rejection and design a new hypothesis only from a stated economic mechanism.

A historical pass would only nominate a prospective experiment. It would not establish Coinbase transferability, actual fees, queue position, fill probability, latency, margin/liquidation behavior, capacity, or realized profit. Level 2 data belongs later, when a survivor needs execution calibration; it is not required to decide whether these bar-level economic mechanisms are already dead after conservative costs.

## Reproduce

```powershell
python -m experiments.historical_mechanisms run --root .
```

Preregistration SHA-256: `0574aafc7d3e676681758c0d7e7ccf1e5de5eec43702775b74046dbe31c842bf`  
Manifest SHA-256: `7c80252ade4e9742cf05d25af28be465e98252ce74033ff52b72e6e8a297b5d4`
