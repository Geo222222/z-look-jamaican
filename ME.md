

Ownership:

| Repo         | Owns                                                                                                                                                                                                |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Benjamin** | decision intelligence, trading cognition, opportunity evaluation, trade/no-trade, sizing reasoning, thesis/invalidation, confidence, temporal reasoning modes, calibration, cognitive qualification |
| **ZLJ**      | raw market acquisition, normalization, microstructure, features, state estimation, predictive models, forecasts, intelligence objects                                                               |
| **The Book** | evidence, decision history, outcomes, accounting/receipts, learning corpus                                                                                                                          |
| **Watchman** | limits, authorization, policy, capital/risk governance                                                                                                                                              |
| **The Hand** | orders, execution, fills, venue adapters                                                                                                                                                            |

The document itself already exposes that boundary very clearly: ZLJ continuously observes the market and produces models/forecasts, while **Benjamin interprets that state and produces TRADE / NO TRADE plus entry, size, target, invalidation, horizon, confidence, and reasoning**. 

### What specifically belongs in ZLJ

There is one major section of the attached material that **should become a ZLJ architectural contract too**:

```text
RAW MARKET
     ↓
Market Data
     ↓
Derived Features
     ↓
Market State
     ↓
Models
     ↓
Intelligence Objects
     ↓
BENJAMIN
```

That is explicitly the ZLJ side of the interface. The document defines ZLJ as the market-state production system, processing trades, L1/L2 books and candles into derived features, market states, predictions and intelligence objects. 

So I would **not copy the whole document into ZLJ**.

Instead, create a ZLJ document such as:

```text
docs/contracts/BENJAMIN_MARKET_INTELLIGENCE_CONTRACT.md
```

Its job is narrower:

```text
ZLJ's obligation to Benjamin

1. What observations ZLJ can produce
2. What derived features exist
3. What market-state objects exist
4. What predictive/model objects exist
5. Required provenance
6. Required timestamps/freshness
7. Confidence/calibration metadata
8. Validity horizon
9. Failure/degraded states
10. Serialization/schema
```

The `ForecastObject` example from the attached material is exactly the sort of object that should cross that boundary. 

And we should probably eventually make that an actual shared schema rather than merely Markdown.

Something like:

```text
ZLJ
│
├── ObservationObject
├── FeatureObject
├── MarketStateObject
├── ForecastObject
└── OpportunityEvidenceObject
        │
        ▼
     BENJAMIN
```

But importantly:

**ZLJ does not produce `TradeDecision`.**

That belongs to Benjamin.

The attached document explicitly makes this distinction: ZLJ can provide probability, expected movement, regime, confidence and evidence, but Benjamin asks whether the move survives fees, spread, slippage, exposure, model dependence, regime calibration and downside before determining whether an actual edge exists. 

---

# What belongs exclusively in Benjamin

Almost everything after that interface.

### Benjamin's v1 ontology

Instrument, market, spread, depth, volatility, opportunity, risk, position, order, trade thesis, invalidation, outcome, etc. are the concepts Benjamin must reason about. 

### Benjamin's three horizons

```text
Scalp
Intraday
Swing
```

Those are **reasoning modes**, not ZLJ products. 

ZLJ might produce forecasts at:

```text
5 sec
30 sec
5 min
1 hour
1 day
```

But Benjamin decides:

> I am currently solving a SCALP decision.

That's Benjamin state.

### Benjamin's qualification benchmark

The section saying that profit alone isn't sufficient and defining things like:

```text
expected edge
realized edge
fees
slippage
net P&L
profit factor
drawdown
MFE
MAE
calibration
performance by regime/model/horizon
```

belongs to the **Benjamin qualification system**. 

That's how we determine:

> Is Benjamin actually becoming competent?

Not:

> Is ZLJ working?

ZLJ has its own qualification requirements—data freshness, model calibration, sequence integrity, prediction performance, etc.

---

# And definitely Benjamin: the cognition layers

This:

```text
               BENJAMIN
                   │
       ┌───────────┼───────────┐
       ▼           ▼           ▼
     Reflex     Tactical    Reflective
```

is absolutely **Benjamin architecture**. 

In fact, I would make that one of Benjamin's foundational design documents.

Something like:

```text
benjamin/
└── docs/
    └── architecture/
        ├── BENJAMIN_V1_MANDATE.md
        ├── COGNITIVE_ARCHITECTURE.md
        ├── TRADING_ONTOLOGY.md
        ├── TEMPORAL_REASONING.md
        ├── DECISION_CONTRACT.md
        ├── QUALIFICATION_STANDARD.md
        └── integrations/
            ├── ZLJ_CONTRACT.md
            ├── BOOK_CONTRACT.md
            ├── WATCHMAN_CONTRACT.md
            └── HAND_CONTRACT.md
```

The attached document itself can probably become the source material for **`BENJAMIN_V1_MANDATE.md`**.

Its constitutional statement is already there:

> Benjamin v1 is Epinnox's short-horizon market decision intelligence, responsible for identifying, evaluating, selecting, sizing, managing and closing short-duration opportunities using qualified evidence. 

That's a Benjamin constitution.

---

## The Book material doesn't move into ZLJ either

The document also identifies market memory, decision memory, and outcome memory and then combines them into learning. 

That's really an **integration requirement between Benjamin and The Book**.

Benjamin defines:

> This is what I need remembered so I can learn.

The Book defines:

> This is how that record is durably, immutably and reproducibly stored.

Again: clean separation.

---

So I would establish this rule now:

> **If the document explains how markets are observed or modeled, it belongs to ZLJ.**
>
> **If it explains how those observations become a capital decision, it belongs to Benjamin.**

And therefore **the attached document belongs in Benjamin**.

ZLJ should receive only the extracted producer-side specification describing exactly **what Benjamin expects ZLJ to see, calculate, qualify, and hand over**.

That gives us an extremely useful architectural boundary:

```text
                    ZLJ
                     │
                     │ qualified
                     │ market intelligence
                     ▼
              ┌──────────────┐
              │              │
              │   BENJAMIN   │
              │              │
              └──────┬───────┘
                     │
                     │ qualified
                     │ decision intent
                     ▼
                 WATCHMAN
                     │
                     ▼
                  THE HAND
```

I would make **Benjamin the canonical owner of the overall document**, then derive a narrow **Benjamin ↔ ZLJ contract** into both repos so neither codebase ever becomes confused about which side of that line it owns.
