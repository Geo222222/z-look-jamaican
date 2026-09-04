# Continuous Public Market Observer

## Purpose

This service finishes the zero-capital observation infrastructure required before ZLJ can responsibly reason from repeated live microstructure windows.

It is deliberately narrower than a trading system. The currently qualified operational collector reads the public Coinbase BTC-USD feed and persists evidence for later calibration. It has no authentication, account, order, wallet, signer, funding, or capital-moving surface.

ZLJ's canonical observation plane is provider-neutral. Coinbase, Kraken, and Binance may each have provider adapters and capture mechanisms without implying that all three are operationally qualified. Provider support advances through separate evidence stages:

```text
ADAPTER_IMPLEMENTED
        !=
SOURCE_CAPTURED
        !=
SOURCE_QUALIFIED
        !=
SOURCE_ELIGIBLE_FOR_A_SPECIFIC_QUESTION
```

As of multi-source market-data v1, Kraken public spot trade/book messages and Binance public spot trade/diff-depth messages have canonical adapter contracts and a bounded public read-only raw-first capture path. That mechanism still claims only `CAPTURED_NOT_SOURCE_QUALIFIED` until a separate prospective source-qualification procedure earns more.

## Multi-source identity boundary

Provider identity and quote currency remain part of market truth.

- Coinbase `BTC-USD` and Kraken `BTC/USD` / `XBT/USD` resolve to the same canonical USD spot expression: `CRYPTO.SPOT.BTC-USD`.
- Binance `BTCUSDT` resolves to the distinct canonical expression `CRYPTO.SPOT.BTC-USDT`.
- Sharing the BTC economic root does not make USD and USDT prices, notionals, or liquidity directly comparable. A later explicit normalization contract must earn that comparison.

A source specification binds one exact provider, provider symbol, canonical instrument and declared event-family set. A message that is syntactically valid for the provider but belongs to a different subscribed instrument is preserved in the raw journal and rejected from canonical promotion under that source identity.

### Kraken continuity and integrity semantics

Kraken provider truth is not flattened into Coinbase sequence semantics.

- Kraken trade `trade_id` is retained as an instrument-scoped sequence value.
- Kraken order-book updates are preserved in the append-only raw journal in local receive order.
- Kraken order-book `checksum` is retained as CRC32 integrity evidence in the canonical payload; it is **not** represented as a canonical sequence number.
- A future Kraken book-qualification procedure must verify the provider's checksum/replay requirements explicitly rather than treating the checksum itself as event ordering.

### Binance semantics

Binance public trade messages expose a buyer-is-market-maker flag. The canonical adapter does not reinterpret that field as provider-reported BUY/SELL aggressor truth; Binance spot trade side therefore remains `UNKNOWN` in this contract.

Binance diff-depth messages are preserved as `BOOK_DELTA` with their exact `U:u` update-ID range and instrument-scoped sequence semantics. They are not mislabeled as snapshots. Until a separately qualified Binance snapshot-plus-buffer synchronization mechanism exists, delta-only Binance order-book state remains unavailable for qualified liquidity truth.

## Capture paths

The existing Coinbase observer remains a specialized, already-qualified L2 capture/replay path. Its connection-global sequence handling is intentionally not generalized to other providers.

Kraken and Binance use the provider-neutral raw-first capture boundary in `autonomous_kernel.observation.public_sources`:

```text
public websocket message
        ↓
append-only raw provider journal
        ↓
hash-chain + immutable compressed bundle
        ↓
provider-specific canonical adapter
        ↓
canonical observation batch
        ↓
separate prospective source qualification
```

The raw message is persisted before canonicalization. Therefore a malformed, off-spec, or newly changed provider event remains available as evidence even when canonical promotion fails closed.

The bounded capture function refuses reuse of a non-empty stream identity, enforces message/time/byte bounds, uses public unauthenticated WebSocket endpoints only, and carries no account, order, wallet, signing, transfer, or capital surface.

## Canonical operational entrypoints

Existing qualified Coinbase observer — one bounded tick:

```bash
python -m experiments.market_observer --mode once --root .
```

Existing qualified Coinbase observer — continuous daemon:

```bash
python -m experiments.market_observer --mode daemon --root .
```

Containerized Coinbase daemon:

```bash
docker compose -f docker-compose.observer.yml up -d --build
```

The daemon cadence is configured in `config/market_observer.json`. The initial configuration starts at one 60-second window every 15 minutes.

The Kraken/Binance `capture_public_source_window(...)` boundary is currently an internal bounded capture mechanism. It does not become an operational source-qualification command merely because the function exists. A later prospective qualification phase owns the explicit operator/experiment entrypoint and evidence criteria.

## Safety boundary

The observer and public-source capture plane are fail-closed around these invariants:

- public read-only network access only;
- no API keys, authentication, accounts, or credential discovery;
- no orders or execution calls;
- no wallets or signers;
- capital remains exactly `$0.00`;
- no capability promotion from observation alone;
- no inference of actual fees, latency, rejection probability, partial fills, actual fills, strategy edge, capital eligibility, or live readiness;
- one fresh filesystem lease prevents overlapping qualified Coinbase windows on one durable runtime;
- each new capture receives a unique stream identity;
- interrupted or rejected raw-source evidence is preserved and never silently rewritten;
- required snapshot/update, trade, timestamp, continuity, integrity, and market-data-quality gates must pass only where the specific provider contract actually exposes those facts;
- provider continuity semantics must not be invented by translating another venue's sequencing model;
- provider observations are never blended merely because they share a base asset.

## Evidence flow

For the existing qualified Coinbase observer, each eligible tick creates a frozen preregistration under:

`artifacts/evidence/market/observer/`

Its qualified journal writes the compressed stream and compact manifest under:

`artifacts/market_data/streams/`

The canonical market-data observation remains under:

`artifacts/market_data/observations/`

The observer adds one audit receipt under:

`evidence/audits/market_observer/`

Durable scheduling and window history are stored in:

`state/market_observer.json`

For the newer Kraken/Binance raw-first capture boundary, immutable provider streams are written under:

`artifacts/market_data/provider_streams/`

and their derived canonical batches are written by `CanonicalBatchStore`. The provider-stream manifest remains explicitly `CAPTURED_NOT_SOURCE_QUALIFIED`.

The Coinbase raw L2 replay additionally measures public-observable distributions for:

- activity/message rate;
- L2 update rate;
- trade-message rate;
- spread percentiles;
- base quantity visible within 10 bps of midpoint;
- 10-bps book imbalance;
- deterministic `$100` and `$1,000` book-impact proxies on each side.

The book-impact values are explicitly `PUBLIC_ORDER_BOOK_PROXY_NOT_ACTUAL_FILL`. They describe the visible book at observation time. They do not claim an executable fill, latency model, venue fee, queue position, rejection probability, partial fill, or realized edge.

## Relationship to TASK-DATA-004

`TASK-DATA-004` requires genuinely time-separated windows covering different public activity, spread, and depth conditions. The observer infrastructure is the mechanism that produces those windows without depending on a chat session or a reasoning agent remaining alive.

Infrastructure completion does **not** complete `TASK-DATA-004` by itself. The task remains evidence-gathering work until several independent windows exist and the configured observable bounds can be evaluated reproducibly.

## Process supervision

The Docker Compose service for the existing Coinbase observer uses `restart: unless-stopped`, no exposed ports, no injected secrets, all Linux capabilities dropped, `no-new-privileges`, and a read-only container filesystem except the explicitly mounted repository evidence/state/runtime directories.

An operator may instead run a collector under systemd, Windows Task Scheduler, Kubernetes, or another deterministic supervisor. The supervisor must not weaken the capture contract or add credentials.

## Storage posture

Continuous microstructure evidence grows quickly. `config/market_observer.json` defines a hard maximum observer evidence footprint and a minimum free-disk reserve for the existing daemon. Reaching either bound must stop new network windows rather than deleting evidence silently or risking host exhaustion.

After a successful qualified Coinbase window, the compressed hash-pinned stream is the replay source. The redundant uncompressed runtime journal may be removed only after the observation and audit are durably written. Failed/interrupted journals remain preserved for diagnosis.

Kraken/Binance raw-first capture likewise never silently deletes rejected provider evidence as part of canonicalization. Retention/compaction policy must be explicit before continuous operational rollout.

## Promotion rule

No number of successful observation windows can independently authorize trading.

Likewise, adapter correctness or a successful bounded public-source capture does not by itself make a provider source eligible for a registered ZLJ question. Source qualification, question eligibility, model qualification, execution realism, shadow trading, Watchman capital governance, capital eligibility, The Hand execution, and live execution remain separate gates.
