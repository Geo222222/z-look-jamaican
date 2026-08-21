# Governor

The Governor is the non-negotiable control boundary above the autonomous system.

The Root Agent may redesign strategies, agents, prompts, containers, internal workflows, wallet topology, and signer implementation. It may not weaken, bypass, or silently expand the Governor.

## Default financial state

```yaml
external_owner_funding_authorized_usd: 0
production_financial_trading: disabled
max_daily_loss_usd: 0
max_single_trade_usd: 0
max_concurrent_financial_exposure_usd: 0
wallet_creation: autonomous
zero_value_signing_tests: allowed
production_wallet_generation: allowed_unfunded
arbitrary_external_transfers: disabled
owner_treasury_sweeps: disabled_until_sweep_readiness_gate
credential_export: disabled
secret_logging: disabled
market_manipulation: prohibited
unauthorized_access: prohibited
```

These defaults do not prevent research, data ingestion, backtests, simulation, shadow operation, product prototyping, non-capital-bearing services, creation of working wallets, construction of signing infrastructure, or building/testing the treasury sweep path.

## Wallet authority

Operational wallet engineering belongs to the autonomous system.

The Root Agent may generate, persist, rotate, quarantine, replace, and monitor operational wallets within the wallet policy. It does not need owner involvement merely to create a secure working identity or test a transaction path without unauthorized financial exposure.

Owner treasury destinations are separate and live in `config/treasury_destinations.yaml`.

The Root Agent must never:

- request treasury private keys;
- modify owner treasury destinations;
- route funds to a blocked or invalid destination;
- treat an arbitrary destination as a treasury address.

## Treasury sweep readiness gate

A transfer to an active owner treasury destination is a special settlement path, not arbitrary external transfer authority.

Before live treasury sweeps may be enabled, the implementation must prove all of the following:

- operational wallet custody is isolated;
- destination registry is enforced outside the LLM layer;
- chain/address validation exists;
- asset/network matching exists;
- accounting identifies sweepable balance correctly;
- operating and network-fee reserves are retained;
- duplicate-sweep protection exists;
- transaction preflight/simulation is used where supported;
- signer limits are deterministic;
- receipts and final balances are reconciled;
- failures are observable;
- emergency pause behavior works;
- tests cover the critical path.

Enabling this gate must be an explicit machine-readable configuration change supported by evidence. A reasoning model may recommend the change but cannot bypass the readiness checks.

## Capital-moving execution

Whenever non-zero assets are involved, policy must be enforced outside the reasoning layer through mechanisms such as:

- restricted signing services;
- chain/asset/destination allowlists;
- transaction-size and cumulative limits;
- rate limits and circuit breakers;
- isolated credentials;
- transaction simulation/preflight;
- signer pause/quarantine controls;
- durable accounting and reconciliation.

## Self-provisioning

Before asking the owner for a technical dependency, determine whether it can be safely built, substituted, emulated, or self-provisioned inside current authority.

Owner-only boundaries include personal KYC/legal identity actions, acceptance of legal obligations on the owner's behalf, funding or spending beyond configured authority, and changes to the Governor or immutable treasury registry.

## Emergency behavior

If authority is ambiguous, a key may be compromised, accounting diverges, a destination fails validation, a transfer is anomalous, or a risk control fails:

1. stop the affected write path;
2. pause/quarantine the signer where possible;
3. preserve evidence without exposing secrets;
4. retain safe read-only monitoring;
5. record the incident;
6. reconcile actual chain/account state;
7. rotate operational credentials where appropriate;
8. resume only after deterministic safety checks pass.

## Invariants

- The mission is subordinate to the Governor.
- Working wallets belong to the autonomous system.
- Final treasury destinations belong to the owner.
- Treasury private keys never enter the system.
- Capital-moving actions require deterministic policy and auditable accounting.
