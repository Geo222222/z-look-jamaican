# Z Look Jamaican Command Center

Read-only evidence and operations monitor for the autonomous cryptocurrency system.

## Purpose

The command center gives a human observer a live view of authoritative runtime evidence without becoming part of the experiment or execution path. It does not create opportunities, modify experiments, sign transactions, move funds, rotate keys, change Governor policy, or write to the mounted repository.

## Start on the MSI

From the repository root:

```powershell
Copy-Item .env.monitor.example .env.monitor -ErrorAction SilentlyContinue
docker compose --env-file .env.monitor -f docker-compose.monitor.yml up -d --build
```

Open `http://localhost:3000`.

Stop with:

```powershell
docker compose --env-file .env.monitor -f docker-compose.monitor.yml down
```

## Read-only boundary

The compose service mounts the repository as `/zlook:ro`, drops all Linux capabilities, uses `no-new-privileges`, runs with a read-only container filesystem, exposes the service only on `127.0.0.1`, and the application rejects HTTP mutation methods.

The monitor never mounts wallet secret directories intentionally. Its scanner also excludes path names and record fields that look like private keys, seed phrases, credentials, passwords, keystores, or secrets.

## What it discovers

The monitor recursively inspects approved durable-state directories when they exist: `state/`, `runtime/`, `evidence/`, `experiments/`, `memory/`, `logs/`, `artifacts/`, `receipts/`, and `metrics/`.

It also reads `config/treasury_destinations.yaml`, `config/governor.yaml` if present, and `docs/GOVERNOR.md`.

JSON, JSONL/NDJSON, YAML and log sources are normalized into the monitoring API. Missing capabilities are displayed as **not yet earned** instead of being populated with fake data.

## API

All endpoints are GET-only: `/api/health`, `/api/overview`, `/api/experiments`, `/api/opportunities`, `/api/evidence`, `/api/wallets`, `/api/treasury`, `/api/governor`, `/api/deployments`, `/api/logs`, `/api/provenance`, and `/api/events` for Server-Sent Events.

## Evidence provenance

Every scanned source receives a SHA-256 digest, modification timestamp, byte count, record count, and source path. The Evidence page exposes these values so the UI can always answer where a displayed claim came from.

## Integration contract with Codex

Codex does not need to call this monitor. It only needs to continue writing durable authoritative state according to repository instructions. The monitor discovers that state independently. When Codex later stabilizes machine-readable schemas, adapters can become stricter without changing the UI or contaminating existing experiments.
