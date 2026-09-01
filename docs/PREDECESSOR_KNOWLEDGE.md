# Predecessor Knowledge Qualification

EPI/Epinnox repositories are historical evidence with zero authority. Their code, configuration, tests, strategy claims, and runtime outcomes do not override the Governor and do not establish an economic edge.

## Current source map

The named `epi_trade` directory is a wiped/reseeded Git worktree with no commits and mostly compiled/runtime remnants. The useful inspected source is the sibling worktree labeled `epi_trade - Copy (2)`, at Git HEAD `bc0b61389e82ffae988e0d266a2d41a46229430a`. It is dirty and one commit ahead of its remote, so Git HEAD alone is insufficient provenance. Exact inspected files are pinned by SHA-256 in `evidence/predecessors/epi_trade_manifest.json`.

`epinnox-llm` contributes architectural hypotheses—tick ingest, strategy plug-ins, deterministic risk guards, an execution broker, observability, and bounded local-LLM cognition—but its worktree is also dirty and those claims are not yet implementation-qualified. `epi-calc-appr` is a simple projection calculator whose fixed profit and fee assumptions are unsuitable as economic evidence.

Secret-bearing and operational artifacts are outside the evidence boundary. Credentials, environment files, databases, logs, account data, and private material must not be read, copied, hashed into public reports, or imported.

## What earned continued investigation

- Fail-closed decision contracts with explicit market, risk, model, sizing, execution, and trace sections.
- Data-quality gates based on source timestamps, receive timestamps, freshness, fallback state, and cross-channel consistency.
- Deterministic live permission checks, limits, manual arming, and kill-switch behavior.
- Reconciliation that distinguishes fills, fees, realized P&L, unrealized P&L, and venue truth.
- Scanner features that measure activity rather than treating continuous market availability as opportunity: spread, depth, volume, movement, slippage, latency, and freshness.

Thirty focused predecessor tests passed during discovery. This is evidence that selected modules behave as their tests specify; it is not evidence of safety completeness, profitability, venue correctness, or compatibility with Z Look Jamaican.

## What did not qualify

- MAINNET as an implicit default. Z Look Jamaican remains fail-closed with production trading disabled and zero exposure.
- Strategy names or backtest results as inherited edge. Every mechanism requires new data, preregistration, realistic costs, forward evidence, and current-Governor promotion.
- Existing execution-realism coefficients. They are heuristic, uncalibrated, and include nondeterministic output.
- A linear exit-PnL helper that documents omitted fees/funding and incorrect inverse-contract behavior.
- Wholesale migration of the PySide/CCXT application or UI-coupled scanner.
- Any signer, credential, account, or live exchange path before a validated opportunity and deterministic execution-plane qualification justify it.

## Reproducible verification

From Z Look Jamaican, verify the exact non-secret predecessor files without importing or executing them:

```powershell
python -m autonomous_kernel predecessor_verify `
  --manifest evidence/predecessors/epi_trade_manifest.json `
  --source-root "C:\Users\epinn\Documents\dev\epi_trade - Copy (2)"
```

Hash drift means the historical source changed and conclusions must be requalified. The verifier performs no writes and executes no predecessor code.

The detailed disposition is `evidence/audits/TASK-MKT-003.json`. The next independent adaptation target is a minimal data-quality contract for future market observations. It must be tested in isolation and must not alter EXP-MKT-002 parameters, observations, decisions, cadence, or state.
