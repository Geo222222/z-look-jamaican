# Wallet, Signing, and Treasury Lifecycle

## Core principle

The autonomous system creates and controls its own working wallets. The owner provides only final treasury withdrawal destinations.

Operational wallet custody and owner treasury custody must remain separate.

## Operational wallets

When blockchain work is justified, the Root Agent must autonomously:

1. determine the required chain/account model;
2. create a purpose-specific working wallet;
3. generate keys using a cryptographically secure implementation appropriate to the chain;
4. persist private material only inside an encrypted or isolated secret boundary;
5. never expose private material in Git, prompts, logs, reports, ordinary memory, or analytics;
6. register only public addresses and non-secret metadata in durable state;
7. build deterministic signing and destination policy around the key;
8. test transaction construction and signing in local forks, simulations, testnets, or other safe environments when practical;
9. monitor balances, nonces, approvals, transactions, receipts, fees, and signer health;
10. rotate, quarantine, or replace keys when required;
11. preserve accounting and recovery state across restarts.

Operational wallets are working capital identities. They are not treasury wallets.

## Wallet classes

Use least privilege and separate purpose where appropriate:

- research/test wallet;
- observation identity;
- revenue-receiving wallet;
- production execution wallet;
- fee/gas wallet;
- settlement/sweep wallet.

Do not collapse every function into one hot wallet merely for convenience.

## Owner treasury destinations

Owner withdrawal destinations are defined in:

`config/treasury_destinations.yaml`

These entries are public destination metadata, not operational credentials.

The Root Agent:

- must never request or require the private keys for owner treasury destinations;
- must not modify or replace owner treasury addresses;
- must not infer a replacement address;
- must validate network/address compatibility before the first transfer;
- must refuse to use any destination marked blocked, invalid, disabled, or pending validation;
- must reconcile every sweep from source wallet to confirmed receipt state.

Current owner-provided destinations include BTC, DOGE, ETH, and a TRON-USDT entry. The TRON-USDT entry is blocked pending address validation because the supplied value uses EVM `0x` encoding rather than the normal Tron mainnet address representation.

## Treasury sweep architecture

Preferred path:

`AI control plane -> sweep proposal -> deterministic treasury policy -> isolated signer -> network -> receipt/reconciliation`

A treasury sweep subsystem should enforce at minimum:

- active destination allowlist;
- chain and asset match;
- preflight address validation;
- minimum sweep threshold;
- operating reserve;
- network-fee reserve;
- maximum single-transfer value;
- cumulative daily limits where configured;
- transaction simulation/preflight where supported;
- nonce/replay protection;
- confirmation policy;
- receipt reconciliation;
- duplicate-sweep prevention;
- emergency pause state;
- durable audit record.

## Sweep policy

The agent should retain enough working capital to keep validated operations functioning while avoiding unnecessary accumulation in hot wallets.

A sweep decision should distinguish:

- gross receipts;
- realized net profit;
- liabilities;
- pending settlement;
- working capital;
- gas/network reserve;
- funds already committed to open obligations;
- excess sweepable balance.

Only excess sweepable balance is eligible for treasury transfer.

Do not sweep funds whose economic state is uncertain, disputed, pending, or required to satisfy known obligations.

## Signing boundary

Private keys must not be directly available to every specialist agent, process, or container.

Preferred design:

`reasoning/control plane -> deterministic transaction request -> policy/risk gate -> isolated signer -> network`

The isolated signer should enforce machine-readable constraints including:

- allowed chain IDs;
- allowed asset/contracts;
- allowed destinations;
- maximum transaction value;
- cumulative exposure limits;
- allowed function selectors where practical;
- nonce/deadline validity;
- current operating stage;
- emergency pause state.

The LLM must not be the sole enforcement mechanism.

## Token approvals

Token approvals are capital permissions. The system must:

- minimize approval amount and duration where practical;
- inventory active approvals;
- monitor unexpected allowance changes;
- revoke obsolete approvals when justified;
- include approval exposure in risk accounting.

## Compromise behavior

On suspected wallet, signer, RPC, dependency, contract, or destination compromise:

1. stop affected write paths;
2. disable or isolate the signer where possible;
3. preserve evidence without exposing secrets;
4. identify assets, approvals, transactions, and destinations affected;
5. prevent additional loss;
6. rotate or replace operational keys as appropriate;
7. independently review remediation;
8. reconcile chain state before resuming.

## Invariants

- The Root Agent owns operational-wallet engineering.
- Owner treasury addresses are withdrawal-only destinations.
- Treasury private keys never enter the autonomous system.
- The Root Agent cannot rewrite the treasury registry.
- A blocked or invalid destination cannot receive funds.
- Every capital-moving action must pass deterministic policy and accounting controls.
