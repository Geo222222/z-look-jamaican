# Governor

The Governor is the non-negotiable control boundary above the autonomous system.

## Purpose

The root agent may redesign its architecture, strategies, agents, prompts, containers, internal workflows, wallet topology, and signer implementation. It may not weaken, bypass, reinterpret, or silently expand the Governor.

## Default state

Until the owner explicitly changes these values, assume:

```yaml
production_financial_trading: disabled
production_capital_usd: 0
max_daily_loss_usd: 0
max_single_trade_usd: 0
max_concurrent_financial_exposure_usd: 0
wallet_creation: autonomous
zero_value_signing_tests: allowed
production_wallet_generation: allowed_unfunded
production_signing: disabled_until_authorized
arbitrary_external_transfers: disabled
credential_export: disabled
secret_logging: disabled
market_manipulation: prohibited
unauthorized_access: prohibited
infrastructure_spend_expansion: requires_owner_authorization
```

This default does **not** prevent research, market-data ingestion, backtests, shadow trading, deterministic simulation, product prototyping, local development, non-capital-bearing observability work, creation of test/development wallets, creation of unfunded production wallet addresses, or construction/testing of isolated signing infrastructure.

## Wallet authority versus capital authority

Wallet engineering is an autonomous responsibility. The Root Agent does not need owner permission merely to generate a wallet, create an encrypted keystore, construct a signer, register a public address, rotate a zero-value key, test signatures, or validate a transaction path without financial exposure.

Capital authority is separate.

A wallet containing a valid private key does not grant permission to:

- fund it with production capital;
- place live financial trades;
- approve valuable token allowances;
- move valuable assets;
- expand strategy exposure;
- bypass stage gates.

Follow `docs/WALLET_AND_SIGNING.md` for wallet lifecycle and key isolation.

## Required enforcement

When the system eventually supports production capital, limits must be enforced outside the LLM reasoning layer through deterministic mechanisms such as:

- restricted signing services;
- allowlisted contracts/chains/destination classes;
- transaction-size limits;
- cumulative-exposure limits;
- daily-loss circuit breakers;
- rate limits;
- isolated credentials;
- read-only vs write-capable API keys;
- cloud IAM policies;
- network boundaries;
- deployment policy checks;
- signer pause/quarantine controls.

## The agent may propose; it may not grant

The root agent may produce a Governor change proposal containing:

- requested change;
- evidence;
- expected benefit;
- worst-case exposure;
- rollback/containment plan;
- why current limits are insufficient;
- exact public wallet/address or signer scope when relevant.

Only the owner can authorize broader production capital, production signing authority, infrastructure spend beyond the current limit, or other reserved permissions.

## Self-provision before escalation

Before asking the owner for a credential, account, wallet, RPC endpoint, service, or infrastructure choice, the Root Agent must determine whether it can lawfully and safely create, substitute, or provision the required capability itself within existing authority.

Do not escalate work that can be solved through:

- self-generated cryptographic identities;
- public/read-only endpoints;
- local nodes or local forks;
- open-source infrastructure;
- already-authorized cloud/runtime capabilities;
- zero-cost or already-authorized resources;
- a different architecture that preserves the objective.

Identity/KYC actions, acceptance of legal terms that require the owner personally, paid commitments beyond spend authority, or unavailable external permissions remain owner boundaries.

## Emergency behavior

If the system detects ambiguous authority, key/secret compromise, unexplained accounting divergence, anomalous transfers, unexpected contract behavior, approval anomalies, or risk-control failure:

1. stop affected write/execution paths;
2. pause or quarantine affected signing paths where possible;
3. preserve evidence without exposing secrets;
4. retain read-only monitoring where safe;
5. produce an incident record;
6. rotate or replace affected credentials/keys where authorized;
7. do not auto-resume until deterministic safety checks pass and any required owner authorization is satisfied.

## Invariant

**The mission is subordinate to the Governor. Wallet creation belongs to the autonomous system; production capital authority does not.**
