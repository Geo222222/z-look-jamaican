# Wallet, Signing, and Treasury Lifecycle

> **Ownership clarification:** this file remains because ZLJ previously explored wallet/signing infrastructure. Under the current Epinnox architecture, production wallet, signing, transfer, settlement, and treasury-action capability belongs to **The Hand**, not ZLJ.

This document therefore describes **predecessor/test knowledge and the boundary that must be preserved when capability is migrated or integrated with The Hand**. It does not grant ZLJ production custody or capital-moving authority.

## Epinnox ownership rule

> **ZLJ sees. Benjamin decides. Watchman governs. The Hand executes. The Book remembers and proves.**

Production financial write paths follow:

`Benjamin decision -> Watchman authorization -> The Hand capability/adaptor -> external financial system -> execution/outcome receipt -> The Book`

ZLJ may observe public/read-only wallet, chain, venue, or market state where that information is useful to market intelligence. It may also use isolated test fixtures, simulations, testnets, zero-value signing tests, or predecessor code for engineering evidence where permitted.

It must not treat those facilities as production action authority.

## The Hand's wallet/custody responsibility

When production blockchain or custody capability is justified, The Hand is the organ that should own the operational implementation for:

1. purpose-specific wallet/custody identities;
2. private-key or signer isolation;
3. deterministic transaction construction;
4. allowed chain/account/asset policy;
5. destination policy;
6. nonce/replay/idempotency protection;
7. transaction simulation/preflight where supported;
8. balance, approval, receipt, and signer-health monitoring;
9. key rotation/quarantine/recovery;
10. durable execution and reconciliation receipts.

Benjamin does not hold the keys. Watchman does not construct transactions. ZLJ does not execute them.

## Wallet classes

Where The Hand eventually requires separate capabilities, least privilege may distinguish:

- research/test wallet;
- observation identity where authentication is unavoidable;
- production execution wallet;
- fee/gas wallet;
- settlement/sweep wallet;
- other purpose-specific custody identities.

Do not collapse every function into one hot wallet merely for convenience.

## Owner treasury destinations

Historical ZLJ configuration may contain owner withdrawal destination metadata such as `config/treasury_destinations.yaml`.

That file is not a constitutional declaration that ZLJ owns treasury settlement. Any future production use must be adopted by the appropriate The Hand/Watchman contract and independently validated there.

No agent should:

- request treasury private keys;
- infer or silently replace a destination;
- route funds to an unverified destination;
- expose private material in Git, prompts, logs, reports, ordinary model memory, or analytics.

## Treasury action architecture

The target ownership model is:

`Benjamin capital intent -> Watchman policy/authorization -> The Hand treasury capability -> isolated signer/provider -> external rail -> receipt/reconciliation -> The Book`

A treasury capability in The Hand should enforce at minimum:

- active destination allowlist;
- chain/asset/account compatibility;
- preflight validation;
- reserves and available-balance checks supplied from authoritative financial state;
- maximum action value and cumulative limits from Watchman policy;
- transaction simulation/preflight where supported;
- nonce/replay/idempotency protection;
- confirmation policy;
- receipt reconciliation;
- duplicate-action prevention;
- emergency pause state;
- durable evidence linkage.

## Signing boundary

Private keys must not be directly available to Benjamin, ZLJ models, general-purpose reasoning agents, or every specialist process.

Preferred design:

`Watchman-authorized action -> The Hand deterministic request -> capability policy -> isolated signer/provider -> external system`

The Hand may choose among technically equivalent adapters only where the authorization and policy explicitly permit that routing. It may not change the economic intent merely because a different integration is available.

## Token approvals and comparable permissions

Approvals, mandates, standing instructions, API trading permissions, and other durable financial write privileges are capital permissions. They belong within The Hand's governed capability inventory and Watchman's authority model.

They must be minimized, inventoried, monitored, and revoked when no longer justified.

## Compromise behavior

On suspected wallet, signer, provider, credential, contract, or destination compromise:

1. stop affected write paths;
2. disable or isolate the affected The Hand capability where possible;
3. preserve evidence without exposing secrets;
4. identify assets, permissions, transactions, and destinations affected;
5. prevent additional loss;
6. rotate or replace credentials/keys as appropriate;
7. independently review remediation;
8. reconcile external state before resuming;
9. preserve the incident and recovery lineage in The Book.

## ZLJ invariant

- ZLJ may study and observe wallet/chain state.
- ZLJ may retain predecessor/test wallet engineering for reproducibility.
- ZLJ does not own production wallet custody, signing, transfer, sweep, or settlement authority.
- Production external financial actions belong to The Hand after Watchman authorization.
