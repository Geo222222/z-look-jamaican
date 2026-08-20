# Autonomous Kernel Runtime

The repository now contains an executable first-generation Root Agent kernel.

## Start

```bash
docker compose up -d --build
```

The API listens on port `8080` by default.

## Observe

- `GET /health` — process health.
- `GET /v1/state` — durable Root Agent state, Governor envelope, top opportunity, experiments, and specialist tasks.
- `POST /v1/cycle` — force one planning/research cycle.
- `GET /v1/tasks` — inspect specialist work.
- `POST /v1/tasks/claim` — lease the highest-priority specialist task to a worker.
- `POST /v1/tasks/{task_id}/complete` — submit structured evidence/results.

## Worker contract

A worker claims work with a stable `worker_id`. The response includes a bounded instruction and lease expiration. The worker returns a result object. Research results should include when applicable:

```json
{
  "summary": "what the evidence shows",
  "evidence": [{"source": "primary source reference", "finding": "..."}],
  "economics": {"gross": null, "expected_net": null, "realized_net": null},
  "risks": ["..."],
  "confidence_delta": 0.10,
  "recommendation": "CONTINUE",
  "next_experiment": "next falsifiable test"
}
```

`confidence_delta` is deterministically clamped to `[-0.5, 0.5]`. A `REJECT` recommendation moves the associated opportunity to `REJECTED`; reopening still requires new evidence under repository doctrine.

## Capital invariant

The runtime starts with external capital `0`. Human funding, borrowing, and leverage are disabled. The deterministic Governor derives any future financial risk envelope only from verified retained realized revenue. At zero retained realized revenue, live trade authority is zero.

## Persistence

Docker Compose uses PostgreSQL. Local tests use SQLite. The same SQLAlchemy models back both so restart/resume state is durable.

## Current maturity

This runtime is the autonomous kernel, not a finished revenue engine. It deliberately marks bootstrap opportunities as unvalidated hypotheses. The next autonomous capability is a primary-source research/experiment worker that can execute the leased tasks and return evidence continuously.
