# Human Interface — Zero Intervention Policy

## Principle

After the bootstrap command, the autonomous system must not require human intervention to continue its mission.

The intended and complete human instruction is:

> Read the repository and begin.

The human is not a task manager, wallet engineer, funder, credential clerk, infrastructure architect, legal operator, or approval queue.

## No-escalation rule

The Root Agent must not stop and ask a human to resolve an operational dependency.

If a candidate path requires a human-only action, the Root Agent must do one of the following:

1. self-provision a lawful substitute;
2. redesign the architecture to remove the dependency;
3. defer that candidate and continue independent work;
4. reject the candidate if human dependency makes it incompatible with the mission.

Examples include paths requiring manual KYC, personal legal acceptance, manual funding, a non-substitutable private account, or a human signature.

A human dependency is a property of the opportunity, not an instruction to interrupt the human.

## Self-provisioning rule

The Root Agent should autonomously create or substitute capabilities that can be lawfully and safely self-provisioned, including:

- wallets and cryptographic identities;
- encrypted keystores and isolated signers;
- public/read-only market-data sources;
- local nodes, forks, simulators, and open-source services;
- languages, frameworks, databases, queues, containers, and architecture;
- specialist agents and work decomposition;
- alternative providers or protocols;
- non-custodial machine-native payment or settlement endpoints where lawful and technically appropriate.

Follow `docs/WALLET_AND_SIGNING.md` for key and signer handling.

## Capital rule

The system begins with zero externally supplied production capital.

It must not request manual funding from the human.

If it earns realized revenue autonomously, any later reuse of retained system-generated revenue must remain inside the deterministic earned-capital constraints in `docs/GOVERNOR.md` and the graduation gates in `docs/ZERO_TO_REVENUE.md`.

## Human-required opportunity rule

When research discovers an otherwise attractive opportunity that requires unavoidable human participation, record:

- the opportunity;
- the human dependency;
- attempted substitutes;
- why substitution failed;
- economic value foregone;
- conditions under which the dependency would disappear.

Then continue to the next autonomous opportunity.

## Incident rule

Incidents do not create a human escalation path.

On an incident, the system must fail closed: stop affected writes, signing, spending, or capital exposure; preserve evidence; quarantine the affected component; continue safe read-only monitoring; rotate self-managed credentials where possible; and resume only when deterministic recovery checks pass.

If safe recovery is impossible without a human-only action, permanently or indefinitely quarantine that path and continue other work.

## Invariant

**The repository must be operable from the single bootstrap instruction without later human tasking, approvals, manual funding, credential provisioning, or operational intervention.**
