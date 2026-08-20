# Security and SRE Skill

Use this skill for production deployment, secrets, wallets/signers, incident response, telemetry, reliability, and autonomous repair workflows.

## Security posture

Assume any component touching money, credentials, signing, external write APIs, or autonomous deployment is high impact.

Apply:
- least privilege;
- secret isolation;
- immutable/versioned production artifacts;
- explicit network and IAM boundaries;
- dependency provenance;
- auditability;
- containment before repair during incidents.

## Secret rules

Never:
- commit secrets;
- print private keys or seed phrases;
- include secrets in prompts or durable agent memory;
- place unrestricted production credentials in general builder containers.

Prefer scoped credentials, separate read/write roles, restricted signers, environment/secret stores, and revocable tokens.

## Production financial execution

When enabled by the Governor, production signing should be mediated by deterministic policy enforcement that can validate:
- allowed networks;
- allowed contracts/destinations;
- maximum amount/exposure;
- daily loss/circuit-breaker state;
- transaction intent;
- nonce/replay conditions;
- simulation status where applicable.

The root LLM should not directly hold unrestricted wallet secrets.

## SRE requirements

Every production service should eventually provide:
- liveness;
- readiness;
- structured logs;
- useful metrics;
- bounded retries/timeouts;
- restart recovery;
- durable critical state;
- dependency health visibility;
- alert/incident triggers;
- deployment identity/version.

## Autonomous incident loop

`detect → contain → preserve evidence → diagnose → reproduce in sandbox → patch → test → security/risk review → canary/shadow → compare → promote or rollback → postmortem`

Never let urgency justify bypassing the Governor.

## Self-healing boundaries

The system may automatically restart failed services, disable a degraded strategy, rotate to a preapproved redundant read provider, or roll back to a known-good artifact when deterministic policy permits.

It should not automatically broaden permissions, spend limits, capital limits, or destination allowlists to restore service.

## Deployment evidence

Record:
- source commit;
- image digest/version;
- configuration version;
- test results;
- deployment timestamp;
- health result;
- rollback target;
- post-deployment observation result.

## Threat modeling

For material new components consider:
- credential theft;
- prompt/tool injection against control-plane agents;
- malicious external data;
- dependency compromise;
- RPC/API spoofing or stale data;
- smart-contract failure/admin risk;
- unsafe transaction construction;
- privilege escalation;
- data poisoning;
- logging leaks;
- denial of service;
- runaway spend;
- compromised deployment pipeline.
