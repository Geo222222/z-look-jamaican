# ZLJ Operator Console

The former read-only monitor is now the browser surface for the ZLJ Operator Console. It preserves all existing monitor endpoints while adding Z1–Z9 stage, certification, and governed control APIs.

## Safe default

`docker-compose.monitor.yml` remains **read-only**. The repository is mounted `:ro` and operator mutations are not enabled. This mode supports the complete visual console plus read-only operations such as kernel validation.

```bash
docker compose -f docker-compose.monitor.yml up --build
```

Open `http://127.0.0.1:3000`.

## Governed control

The browser never mutates kernel files directly. Commands flow through:

```text
browser → FastAPI → autokernel operator_command → domain service → receipt
```

A mutating command additionally requires `ZLOOK_OPERATOR_MUTATIONS_ENABLED=true` to be set outside the UI **and** a writable source root. Do not enable this on the existing read-only monitor deployment. A dedicated operator deployment profile should be introduced only when its filesystem, authentication, and authorization controls are explicitly designed and certified.

`LIVE_EXECUTION`, `CAPITAL_AUTHORIZATION`, and `ORDER_PLACEMENT` are constitutionally locked in ZLJ regardless of operator mode.

See `docs/ZLJ_OPERATOR_CONSOLE.md` for the complete control and visual contract.
