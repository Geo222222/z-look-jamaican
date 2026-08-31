# Z Look Jamaican Command Center

Read-only frontend bound exclusively to the authoritative monitoring contract introduced by `c63ff73010b9da43617c9292461182e83168bdf6`.

The monitor does **not** scan or infer repository schemas. Every refresh executes:

```bash
python -m autonomous_kernel monitor_snapshot --json
```

and accepts only contract `z-look-jamaican-monitor-snapshot` schema `1.0.0` with `read_only=true`.

## Run

From the repository root:

```powershell
Copy-Item .env.monitor.example .env.monitor
docker compose --env-file .env.monitor -f docker-compose.monitor.yml up -d --build
```

Open `http://localhost:3000`.

## Surfaces

- Overview
- Opportunities
- Experiments and prospective/resolved decisions
- Evidence, data quality, reflections
- Operational-wallet public metadata only
- Treasury destination/readiness state
- Governor, exposure, realized economics
- Deployments and model/provider qualification state
- Runtime logs and incidents
- Provenance, freshness, SHA-256 integrity, authority timestamps

## Safety boundary

The container mounts the repository read-only, drops Linux capabilities, uses `no-new-privileges`, and exposes only GET/HEAD/OPTIONS HTTP methods. It does not import mutation functions or gain access to signer/private-key material.

Availability is never coerced to zero/empty. The UI preserves the contract states: `available`, `unknown`, `not_earned`, `blocked`, and `unavailable`.
