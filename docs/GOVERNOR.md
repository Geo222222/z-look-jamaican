# Governor

The Governor is the non-negotiable control boundary above the autonomous system.

## Purpose

The root agent may redesign its architecture, strategies, agents, prompts, containers, and internal workflows. It may not weaken, bypass, reinterpret, or silently expand the Governor.

## Default state

Until the owner explicitly changes these values, assume:

```yaml
production_financial_trading: disabled
production_capital_usd: 0
max_daily_loss_usd: 0
max_single_trade_usd: 0
max_concurrent_financial_exposure_usd: 0
arbitrary_external_transfers: disabled
credential_export: disabled
secret_logging: disabled
market_manipulation: prohibited
unauthorized_access: prohibited
infrastructure_spend_expansion: requires_owner_authorization
```

This default does **not** prevent research, market-data ingestion, backtests, shadow trading, deterministic simulation, product prototyping, local development, or non-capital-bearing observability work.

## Required enforcement

When the system eventually supports production capital, limits must be enforced outside the LLM reasoning layer through deterministic mechanisms such as:

- restricted signing services;
- allowlisted contracts/chains/destinations;
- transaction-size limits;
- daily-loss circuit breakers;
- rate limits;
- isolated credentials;
- read-only vs write-capable API keys;
- cloud IAM policies;
- network boundaries;
- deployment policy checks.

## The agent may propose; it may not grant

The root agent may produce a Governor change proposal containing:

- requested change;
- evidence;
- expected benefit;
- worst-case exposure;
- rollback/containment plan;
- why current limits are insufficient.

Only the owner can authorize broader capital or permissions.

## Emergency behavior

If the system detects ambiguous authority, secret compromise, unexplained accounting divergence, anomalous transfers, unexpected contract behavior, or risk-control failure:

1. stop affected write/execution paths;
2. preserve evidence;
3. retain read-only monitoring where safe;
4. produce an incident record;
5. do not auto-resume until deterministic safety checks pass and any required owner authorization is satisfied.

## Invariant

**The mission is subordinate to the Governor.** Failing to make money is acceptable. Violating the Governor is not.
