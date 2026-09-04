# Continuous Public Market Observer

## Purpose

This service finishes the zero-capital observation infrastructure required before ZLJ can responsibly reason from repeated live microstructure windows.

It is deliberately narrower than a trading system. The currently qualified operational collector reads the public Coinbase BTC-USD feed and persists evidence for later calibration. It has no authentication, account, order, wallet, signer, funding, or capital-moving surface.

ZLJ's canonical observation plane is provider-neutral. Coinbase, Kraken, and Binance may each have provider adapters without implying that all three are operationally captured or qualified. Provider support advances through separate evidence stages:

```text
ADAPTER_IMPLEMENTED
        !=
SOURCE_CAPTURED
        !=
SOURCE_QUALIFIED
        !=
SOURCE_ELIGIBLE_FOR_A_SPECIFIC_QUESTION
```

As of the multi-source market-data v1 work, Kraken's public spot trade/book messages and Binance's public spot trade/diff-depth messages have canonical adapter contracts. This alone does not promote either source into the operational observer.

## Multi-source identity boundary

Provider identity and quote currency remain part of market truth.

- Coinbase `BTC-USD` and Kraken `BTC/USD` / `XBT/USD` resolve to the same canonical USD spot expression: `CRYPTO.SPOT.BTC-USD`.
- Binance `BTCUSDT` resolves to the distinct canonical expression `CRYPTO.SPOT.BTC-USDT`.
- Sharing the BTC economic root does not make USD and USDT prices, notionals, or liquidity directly comparable. A later explicit normalization contract must earn that comparison.

Binance public trade messages expose a buyer-is-market-maker flag. The canonical adapter does not reinterpret that field as provider-reported BUY/SELL aggressor truth; Binance spot trade side therefore remains `UNKNOWN` in this contract.

Binance diff-depth messages are preserved as `BOOK_DELTA` with their exact update-ID range. They are not mislabeled as snapshots. Until a separately qualified Binance snapshot-plus-buffer synchronization mechanism exists, delta-only Binance order-book state remains unavailable for qualified liquidity truth.

## Canonical entrypoints

One bounded tick:

```bash
python -m experiments.market_observer --mode once --root .
```

Continuous daemon:

```bash
python -m experiments.market_observer --mode daemon --root .
```

Containerized daemon:

```bash
docker compose -f docker-compose.observer.yml up -d --build
```

The daemon cadence is configured in `config/market_observer.json`. The initial configuration starts at one 60-second window every 15 minutes.

## Safety boundary

The observer is fail-closed around these invariants:

- public read-only network access only;
- no API keys, authentication, accounts, or credential discovery;
- no orders or execution calls;
- no wallets or signers;
- capital remains exactly `$0.00`;
- no capability promotion from observation alone;
- no inference of actual fees, latency, rejection probability, partial fills, actual fills, strategy edge, capital eligibility, or live readiness;
- one fresh filesystem lease prevents overlapping windows on one durable runtime;
- each new window receives a new stream identity and preregistration before the network capture starts;
- interrupted windows are preserved as failed evidence and are never silently reused;
- required snapshot/update, trade, heartbeat, timestamp, sequence, and market-data-quality gates must pass for whichever provider contract claims those features;
- provider sequence gaps or out-of-order evidence reject continuity claims;
- provider observations are never blended merely because they share a base asset.

## Evidence flow

Each eligible tick creates a frozen preregistration under:

`artifacts/evidence/market/observer/`

The qualified journal writes the compressed stream and compact manifest under:

`artifacts/market_data/streams/`

The canonical market-data observation remains under:

`artifacts/market_data/observations/`

The observer adds one audit receipt under:

`evidence/audits/market_observer/`

Durable scheduling and window history are stored in:

`state/market_observer.json`

The raw L2 replay additionally measures public-observable distributions for:

- activity/message rate;
- L2 update rate;
- trade-message rate;
- spread percentiles;
- base quantity visible within 10 bps of midpoint;
- 10-bps book imbalance;
- deterministic `$100` and `$1,000` book-impact proxies on each side.

The book-impact values are explicitly `PUBLIC_ORDER_BOOK_PROXY_NOT_ACTUAL_FILL`. They describe the visible book at observation time. They do not claim an executable fill, latency model, venue fee, queue position, rejection probability, partial fill, or realized edge.

## Relationship to TASK-DATA-004

`TASK-DATA-004` requires genuinely time-separated windows covering different public activity, spread, and depth conditions. This service is the mechanism that produces those windows without depending on a chat session or a reasoning agent remaining alive.

Infrastructure completion does **not** complete `TASK-DATA-004` by itself. The task remains evidence-gathering work until several independent windows exist and the configured observable bounds can be evaluated reproducibly.

## Process supervision

The Docker Compose service uses `restart: unless-stopped`, no exposed ports, no injected secrets, all Linux capabilities dropped, `no-new-privileges`, and a read-only container filesystem except the explicitly mounted repository evidence/state/runtime directories.

An operator may instead run the daemon under systemd, Windows Task Scheduler, Kubernetes, or another deterministic supervisor. The supervisor must not weaken the observer configuration or add credentials.

## Storage posture

Continuous microstructure evidence grows quickly. `config/market_observer.json` defines a hard maximum observer evidence footprint and a minimum free-disk reserve. Reaching either bound must stop new network windows rather than deleting evidence silently or risking host exhaustion.

After a successful window, the compressed hash-pinned stream is the replay source. The redundant uncompressed runtime journal may be removed only after the observation and audit are durably written. Failed/interrupted journals remain preserved for diagnosis.

## Promotion rule

No number of successful observation windows can independently authorize trading.

The observer may support later evidence for ZLJ market-learning research and Benjamin's downstream judgment. Strategy/model qualification, execution realism, shadow trading, Watchman capital governance, capital eligibility, The Hand execution, and live execution remain separate gates.
