"""Build an inspectable composable market-object graph from verified history."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from experiments.historical_mechanisms import MANIFEST_SHA256, SYMBOLS, iso_timestamp, load_funding, load_klines
from market_objects.context.calendar import calendar_context
from market_objects.evidence.funding import funding_observation
from market_objects.evidence.portfolio import portfolio_observation
from market_objects.evidence.price import price_observation
from market_objects.measurements.normalized import normalized_price_series
from market_objects.measurements.relative import relative_calculation
from market_objects.measurements.statistical import statistical_calculation
from market_objects.measurements.technical import technical_calculation
from market_objects.opportunities.candidate import opportunity_candidate
from market_objects.state.classifiers import (correlation_state, liquidity_state, momentum_state,
    participation_state, positioning_state, risk_state, trend_state, unavailable_state, volatility_state)
from market_objects.store import MarketObjectStore
from market_objects.stories.engine import determine_market_story
from market_objects.strategies.applicability import scan_registry
from market_objects.strategies.registry import load_registry
from market_objects.structure.patterns import pattern_detection
from market_objects.structure.price import price_structure
from market_objects.transitions.state_transition import state_transition
from market_objects.world import world_snapshot


CREATED_AT = "2026-09-02T03:00:00Z"


def _slug(value: str) -> str:
    return value.upper().replace("_", "-").replace("/", "-")


def _aggregate(rows: Sequence[Any], size: int = 48) -> list[dict[str, Any]]:
    usable = rows[-(len(rows) // size * size):]
    result = []
    for start in range(0, len(usable), size):
        group = usable[start:start + size]
        result.append({"timestamp_ms": group[0].timestamp_ms, "open": group[0].open,
            "high": max(row.high for row in group), "low": min(row.low for row in group),
            "close": group[-1].close, "quote_volume": sum(row.quote_volume for row in group),
            "trade_count": sum(row.trade_count for row in group),
            "taker_buy_quote_volume": sum(row.taker_buy_quote_volume for row in group)})
    return result


def _evidence(asset: str, surface: str, rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    instrument = f"{asset}-USDT-{surface.upper()}"
    prefix = f"EVIDENCE-BINANCE-{asset}-{surface.upper()}-4H"
    return [price_observation(object_id=f"{prefix}-{row['timestamp_ms']}", instrument=instrument,
        exchange="binance", asset=asset, timestamp=iso_timestamp(row["timestamp_ms"]), interval="4H",
        open_price=row["open"], high_price=row["high"], low_price=row["low"], close_price=row["close"],
        volume=0, quote_volume=row["quote_volume"], trade_count=row["trade_count"],
        taker_buy_quote_volume=row["taker_buy_quote_volume"], source_record_id=f"verified-5m-aggregate:{surface}:{row['timestamp_ms']}",
        source_sha256=MANIFEST_SHA256, created_at=CREATED_AT) for row in rows]


def build_graph(root: Path, bars: int = 250) -> Mapping[str, Any]:
    store = MarketObjectStore(root)
    registry = load_registry(root / "config/strategy_registry.json")
    all_objects: list[Mapping[str, Any]] = []
    series: dict[tuple[str, str], Mapping[str, Any]] = {}
    bundles: dict[tuple[str, str], list[Mapping[str, Any]]] = {}

    evidence_batch = []
    for asset, symbol in SYMBOLS.items():
        for surface, source_surface in (("spot", "spot_klines"), ("perpetual", "perpetual_klines")):
            rows = _aggregate(load_klines(root, source_surface, symbol))[-bars:]
            evidence = _evidence(asset, surface, rows)
            evidence_batch.extend(evidence)
            bundles[(asset, surface)] = [evidence[-1]]
        funding_times, funding_rates = load_funding(root, symbol)
        funding = funding_observation(object_id=f"EVIDENCE-BINANCE-{asset}-FUNDING-{funding_times[-1]}",
            instrument=f"{asset}-USDT-PERPETUAL", exchange="binance", asset=asset,
            timestamp=iso_timestamp(funding_times[-1]), funding_rate=funding_rates[-1], interval_hours=8,
            source_record_id=f"verified-funding:{funding_times[-1]}", source_sha256=MANIFEST_SHA256, created_at=CREATED_AT)
        evidence_batch.append(funding)
        bundles[(asset, "perpetual")].append(funding)
    store.persist_many(evidence_batch)
    all_objects.extend(evidence_batch)

    measurement_batch = []
    for asset in SYMBOLS:
        for surface in ("spot", "perpetual"):
            ids = [obj for obj in evidence_batch if obj["subject"]["asset"] == asset and obj["object_type"] == "MARKET_OBSERVATION" and surface.upper() in obj["subject"]["instrument"]]
            current = normalized_price_series(object_id=f"MEAS-BINANCE-{asset}-{surface.upper()}-4H-CURRENT", evidence_objects=ids, created_at=CREATED_AT)
            previous = normalized_price_series(object_id=f"MEAS-BINANCE-{asset}-{surface.upper()}-4H-PRIOR", evidence_objects=ids[:-1], created_at=CREATED_AT)
            measurement_batch.extend([current, previous]); series[(asset, surface)] = current
    store.persist_many(measurement_batch); all_objects.extend(measurement_batch)

    derived_batch = []
    for item in measurement_batch:
        suffix = item["object_id"].removeprefix("MEAS-")
        derived_batch.extend([technical_calculation(object_id=f"MATH-TECH-{suffix}", price_series=item, created_at=CREATED_AT),
                              statistical_calculation(object_id=f"MATH-STAT-{suffix}", price_series=item, created_at=CREATED_AT)])
    relatives = {}
    for asset in SYMBOLS:
        relative = relative_calculation(object_id=f"MATH-REL-{asset}-SPOT-PERP-4H", subject_series=series[(asset, "spot")], benchmark_series=series[(asset, "perpetual")], created_at=CREATED_AT)
        derived_batch.append(relative); relatives[asset] = relative
    store.persist_many(derived_batch); all_objects.extend(derived_batch)

    later_batch = []
    for asset in SYMBOLS:
        for surface in ("spot", "perpetual"):
            subject_series = series[(asset, surface)]
            current_tech = next(x for x in derived_batch if x["object_id"] == f"MATH-TECH-BINANCE-{asset}-{surface.upper()}-4H-CURRENT")
            prior_tech = next(x for x in derived_batch if x["object_id"] == f"MATH-TECH-BINANCE-{asset}-{surface.upper()}-4H-PRIOR")
            current_stat = next(x for x in derived_batch if x["object_id"] == f"MATH-STAT-BINANCE-{asset}-{surface.upper()}-4H-CURRENT")
            prior_stat = next(x for x in derived_batch if x["object_id"] == f"MATH-STAT-BINANCE-{asset}-{surface.upper()}-4H-PRIOR")
            prior_series = next(x for x in measurement_batch if x["object_id"] == f"MEAS-BINANCE-{asset}-{surface.upper()}-4H-PRIOR")
            structure = price_structure(object_id=f"STRUCT-{asset}-{surface.upper()}-CURRENT", price_series=subject_series, created_at=CREATED_AT)
            prior_structure = price_structure(object_id=f"STRUCT-{asset}-{surface.upper()}-PRIOR", price_series=prior_series, created_at=CREATED_AT)
            pattern = pattern_detection(object_id=f"PATTERN-{asset}-{surface.upper()}-CURRENT", price_series=subject_series, technical=current_tech, created_at=CREATED_AT)
            context = calendar_context(object_id=f"CONTEXT-{asset}-{surface.upper()}-CURRENT", instrument=subject_series["subject"]["instrument"], exchange="binance", asset=asset, as_of=subject_series["effective_at"], created_at=CREATED_AT)
            later_batch.extend([structure, prior_structure, pattern, context])
            current_states = [trend_state(object_id=f"STATE-TREND-{asset}-{surface.upper()}-CURRENT", technical=current_tech, structure=structure, created_at=CREATED_AT),
                volatility_state(object_id=f"STATE-VOL-{asset}-{surface.upper()}-CURRENT", technical=current_tech, statistical=current_stat, created_at=CREATED_AT),
                momentum_state(object_id=f"STATE-MOM-{asset}-{surface.upper()}-CURRENT", technical=current_tech, statistical=current_stat, created_at=CREATED_AT),
                participation_state(object_id=f"STATE-PART-{asset}-{surface.upper()}-CURRENT", statistical=current_stat, microstructure=None, created_at=CREATED_AT),
                correlation_state(object_id=f"STATE-CORR-{asset}-{surface.upper()}-CURRENT", relative=relatives[asset], created_at=CREATED_AT),
                risk_state(object_id=f"STATE-RISK-{asset}-{surface.upper()}-CURRENT", technical=current_tech, statistical=current_stat, context=context, created_at=CREATED_AT)]
            liquidity = unavailable_state(object_id=f"STATE-LIQ-{asset}-{surface.upper()}-CURRENT", object_type="LIQUIDITY_STATE", subject=subject_series["subject"], effective_at=subject_series["effective_at"], created_at=CREATED_AT, reason="Historical candles contain no contemporaneous order-book depth or spread.", required_evidence=["ORDER_BOOK_OBSERVATION", "TRADE_OBSERVATION"])
            current_states.append(liquidity)
            if surface == "perpetual":
                funding = next(x for x in evidence_batch if x["object_type"] == "FUNDING_OBSERVATION" and x["subject"]["asset"] == asset)
                current_states.append(positioning_state(object_id=f"STATE-POS-{asset}-PERPETUAL-CURRENT", funding=funding, created_at=CREATED_AT))
            else:
                current_states.append(unavailable_state(object_id=f"STATE-POS-{asset}-SPOT-CURRENT", object_type="POSITIONING_STATE", subject=subject_series["subject"], effective_at=subject_series["effective_at"], created_at=CREATED_AT, reason="Spot history has no derivative positioning evidence.", required_evidence=["FUNDING_OBSERVATION", "OPEN_INTEREST_OBSERVATION"]))
            prior_states = [trend_state(object_id=f"STATE-TREND-{asset}-{surface.upper()}-PRIOR", technical=prior_tech, structure=prior_structure, created_at=CREATED_AT),
                volatility_state(object_id=f"STATE-VOL-{asset}-{surface.upper()}-PRIOR", technical=prior_tech, statistical=prior_stat, created_at=CREATED_AT),
                momentum_state(object_id=f"STATE-MOM-{asset}-{surface.upper()}-PRIOR", technical=prior_tech, statistical=prior_stat, created_at=CREATED_AT)]
            later_batch.extend(current_states + prior_states)
            transitions = [state_transition(object_id=f"TRANS-{asset}-{surface.upper()}-{cur['payload']['dimension']}", previous_state=prev, current_state=cur, created_at=CREATED_AT) for prev, cur in zip(prior_states, current_states[:3])]
            later_batch.extend(transitions)
            story_inputs = [structure, pattern, context, *current_states, *transitions]
            story = determine_market_story(object_id=f"STORY-{asset}-{surface.upper()}-CURRENT", subject=subject_series["subject"], effective_at=subject_series["effective_at"], created_at=CREATED_AT, objects=story_inputs)
            # Persist up through transitions before composing later layers.
            store.persist_many(later_batch); later_batch = []
            store.persist(story)
            referenced = [*bundles[(asset, surface)], current_tech, current_stat, relatives[asset], *story_inputs]
            matches = scan_registry(registry=registry, story=story, referenced_objects=referenced, created_at=CREATED_AT, object_id_prefix=f"MATCH-{asset}-{surface.upper()}")
            store.persist_many(matches)
            portfolio = portfolio_observation(object_id=f"EVIDENCE-PORTFOLIO-{asset}-{surface.upper()}-BLOCKED", instrument=subject_series["subject"]["instrument"], exchange="binance", asset=asset, timestamp=subject_series["effective_at"], current_exposure_usd="0", available_risk_budget_usd="0", strategy_exposure_allowed=False, source_record_id="governor:research-only", source_sha256=hashlib.sha256(b"governor:research-only").hexdigest(), created_at=CREATED_AT)
            store.persist(portfolio)
            opportunity = opportunity_candidate(object_id=f"OPPORTUNITY-{asset}-{surface.upper()}-CURRENT", applicability=matches[0], liquidity_state=liquidity, portfolio=portfolio, created_at=CREATED_AT, entry_plan={}, exit_plan={}, risk_plan={}, economics={"expected_gross_payoff_bps":"0", "expected_total_cost_bps":"20", "minimum_required_net_edge_bps":"1"})
            store.persist(opportunity)
            indexed = [bundles[(asset, surface)][0], subject_series, current_tech, current_stat, relatives[asset], *story_inputs, story, *matches, portfolio, opportunity]
            world = world_snapshot(object_id=f"WORLD-BINANCE-{asset}-{surface.upper()}-4H-{subject_series['effective_at'][:10].replace('-', '')}", instrument=subject_series["subject"]["instrument"], exchange="binance", asset=asset, as_of=subject_series["effective_at"], created_at=CREATED_AT, objects=indexed)
            store.persist(world)
            all_objects.extend(indexed + [world])
    index = store.rebuild_index()
    return {"world_snapshots": [item for item in index["items"] if item["object_type"] == "MARKET_WORLD_SNAPSHOT"], "index": index}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--bars", type=int, default=250)
    args = parser.parse_args()
    result = build_graph(args.root.resolve(), args.bars)
    print(json.dumps({"object_count": result["index"]["object_count"], "layer_counts": result["index"]["layer_counts"], "world_snapshots": result["world_snapshots"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
