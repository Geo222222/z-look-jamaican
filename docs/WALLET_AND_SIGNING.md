# Wallet and Signing Lifecycle

## Principle

Wallet bootstrap is part of the autonomous system's own job. The owner is not expected to create, configure, or custody the system's operational wallets on the agent's behalf.

The Root Agent must be capable of creating and operating the wallet infrastructure it needs while remaining inside the Governor.

## Bootstrap responsibility

When blockchain work becomes justified, the Root Agent must autonomously:

1. determine which chain/account model is required;
2. create an isolated wallet or account for that purpose;
3. generate keys using a cryptographically secure implementation appropriate to the chain;
4. keep private material out of source control, prompts, logs, reports, and ordinary agent memory;
5. store private material in a dedicated secret boundary such as a secrets manager, encrypted keystore, hardware-backed signer, or isolated signing service;
6. register only public addresses and non-secret metadata in durable system state;
7. create deterministic signing policy around the key;
8. test signing on local forks, simulations, or test networks when available;
9. monitor balances, nonces, approvals, transactions, receipts, and signer health;
10. rotate, quarantine, or replace keys when compromise is suspected;
11. maintain recovery and incident procedures appropriate to the value controlled.

Creating a wallet is not equivalent to authorizing financial exposure.

## Wallet classes

The system should separate wallet purpose and privilege. Examples include:

### Research/Test wallet
For local forks, testnets, protocol integration tests, signing tests, and zero-value validation.

### Observation identity
A public address used for address-based simulations, allowlist testing, or protocol-specific identity where no valuable signing authority is required.

### Production execution wallet
A restricted wallet used only after a strategy has passed the required stage gates and the Governor authorizes non-zero production exposure.

### Treasury or reserve wallet
If the system eventually requires one, it must be isolated from ordinary execution. Execution services should not hold unrestricted treasury authority.

The Root Agent may create additional purpose-specific wallet classes when least privilege requires them.

## Secret boundary

The Root Agent may design and implement its own signer infrastructure, but private keys must not be directly available to every specialist agent or container.

Preferred pattern:

`AI control plane -> deterministic transaction proposal -> policy/risk gate -> isolated signer -> network`

The signer should enforce machine-readable rules such as:

- allowed chain IDs;
- allowed contracts or destination classes;
- maximum transaction value;
- maximum cumulative exposure;
- allowed function selectors when practical;
- deadline/nonce validity;
- strategy/stage authorization;
- emergency pause state;
- current Governor limits.

The LLM must not be the sole enforcement mechanism.

## Zero-start behavior

At initial bootstrap:

- the agent may create zero-value development/test wallets without owner involvement;
- the agent may build production-grade wallet/signing infrastructure before production capital exists;
- production wallets may be generated and registered before funding;
- production capital remains `0` until the Governor authorizes otherwise;
- a generated wallet must never be treated as permission to trade or transfer value.

This lets the autonomous organization construct and validate its complete transaction path from zero while preserving the capital boundary.

## Funding and earned assets

If a validated strategy eventually requires initial production capital, the Root Agent must present a Governor decision packet containing the public funding address, requested amount, strategy/stage, maximum authorized exposure, evidence, and expiry/revocation conditions.

If the system earns assets through an already-authorized production activity, those assets remain subject to the Governor and accounting rules. Earnings do not automatically expand risk limits or spending authority.

## Approval hygiene

Token approvals and smart-contract permissions are capital permissions. The system must:

- minimize approval amounts and duration where practical;
- inventory active approvals;
- monitor unexpected allowance changes;
- revoke obsolete approvals when economically justified;
- include approval exposure in risk analysis.

## Compromise behavior

On suspected key, signer, RPC, dependency, or contract compromise:

1. stop affected write paths;
2. disable the signer or policy path where possible;
3. preserve evidence without revealing secrets;
4. identify assets and approvals exposed;
5. move to a known-safe operational state only through authorized deterministic controls;
6. rotate or replace affected keys;
7. independently review the incident and remediation;
8. do not resume merely because the service is healthy again.

## Invariant

**The Root Agent owns wallet engineering. The Governor owns capital authority.**