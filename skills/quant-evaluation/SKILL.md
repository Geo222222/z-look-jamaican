# Quantitative Evaluation Skill

Use this skill for financial strategies, pricing/routing decisions, backtests, simulations, portfolio allocation research, and any claim of statistical/economic edge.

## Core rule

Gross spread is not profit. Backtest profit is not executable profit. Simulated profit is not realized revenue.

## Evaluation stack

1. Define the economic mechanism and null hypothesis.
2. Define data provenance and timestamp semantics.
3. Prevent look-ahead bias and outcome leakage.
4. Model execution realistically.
5. Include all material costs.
6. Report uncertainty and sensitivity.
7. Test out-of-sample where applicable.
8. Compare prediction to live shadow results.
9. Recalibrate or reject when live error is material.

## For DEX/market strategies

Model where relevant:
- pool fees;
- gas and priority fees;
- slippage and price impact;
- liquidity depth;
- transaction latency;
- failed/reverted transaction cost;
- stale state;
- MEV/ordering competition;
- inventory/rebalancing cost;
- bridge/finality risk if cross-chain;
- RPC failures;
- contract-specific constraints;
- capacity limits as trade size increases.

Atomic same-chain opportunities and cross-chain inventory strategies must not be modeled as equivalent execution problems.

## Metrics

Use metrics appropriate to the cadence, including:
- expected net value per opportunity;
- realized net value;
- hit/fill rate;
- false-positive rate;
- average and median edge;
- variance;
- drawdown;
- tail loss;
- turnover;
- capital utilization;
- infrastructure cost;
- prediction error;
- sensitivity to fees/latency/slippage;
- capacity before edge deterioration.

## Experimental discipline

A strategy proposal must specify:
- what would falsify it;
- minimum evidence needed for the next graduation stage;
- assumptions most likely to dominate results;
- how shadow decisions are timestamped before outcomes become known.

## Output

Return a machine-readable evaluation plus concise narrative containing:
- thesis;
- methodology;
- data limitations;
- gross economics;
- cost-adjusted economics;
- uncertainty;
- failure modes;
- current graduation stage;
- recommendation;
- next experiment.
