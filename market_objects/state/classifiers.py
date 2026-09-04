"""State labels reference measurements; they do not copy measurement values."""

from typing import Any, Mapping, Sequence

from ..core import MarketObjectRef, build_object


def _state_object(
    *, object_id: str, object_type: str, subject: Mapping[str, Any], effective_at: str,
    created_at: str, source_time_range: Mapping[str, Any], inputs: Sequence[Mapping[str, Any]],
    method_name: str, state: str, strength: float, evidence_roles: Mapping[str, str],
    uncertainty: Sequence[str], classifications: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    refs = [MarketObjectRef.to(item["object_id"], evidence_roles[item["object_id"]], expected_object_type=item["object_type"]) for item in inputs]
    evidence_refs = [f"market://{item['object_id']}" for item in inputs]
    return build_object(
        object_id=object_id, object_type=object_type, truth_class="DETERMINISTIC_CLASSIFICATION",
        subject=subject, effective_at=effective_at, created_at=created_at, source_time_range=source_time_range,
        input_refs=refs, method={"name": method_name, "version": "1.0.0", "deterministic": True},
        quality={"status": "VALID", "input_count": len(inputs)},
        payload={"dimension": object_type.removesuffix("_STATE"), "state": state, "strength": round(max(0.0, min(1.0, strength)), 4), "evidence_refs": evidence_refs, "classifications": dict(classifications or {}), "uncertainty": list(uncertainty), "duplicates_measurement_values": False},
    )


def trend_state(*, object_id: str, technical: Mapping[str, Any], structure: Mapping[str, Any], created_at: str) -> Mapping[str, Any]:
    values = technical["payload"]["values"]
    trend = structure["payload"]["trend_structure"]
    ema20, ema50, ema200 = values.get("ema_20"), values.get("ema_50"), values.get("ema_200")
    adx = float(values.get("adx_14") or 0.0)
    if trend == "HIGHER_HIGHS_HIGHER_LOWS" and ema20 and ema50 and ema20 > ema50 and (ema200 is None or ema50 > ema200):
        state = "UP"
    elif trend == "LOWER_HIGHS_LOWER_LOWS" and ema20 and ema50 and ema20 < ema50 and (ema200 is None or ema50 < ema200):
        state = "DOWN"
    elif trend in {"RISING_LOWS_UNDER_RESISTANCE", "FALLING_HIGHS_ABOVE_SUPPORT"}:
        state = "COMPRESSION"
    else:
        state = "RANGE_OR_MIXED"
    strength = min(1.0, adx / 50) if state in {"UP", "DOWN"} else max(0.2, 1 - min(1.0, adx / 50))
    return _state_object(
        object_id=object_id, object_type="TREND_STATE", subject=technical["subject"], effective_at=technical["effective_at"], created_at=created_at,
        source_time_range=technical["source_time_range"], inputs=[technical, structure],
        method_name="STRUCTURE_EMA_ADX_TREND_CLASSIFIER", state=state, strength=strength,
        evidence_roles={technical["object_id"]: "TREND_MATH_EVIDENCE", structure["object_id"]: "TREND_GEOMETRY_EVIDENCE"},
        uncertainty=["Trend is a deterministic label over lagging calculations and confirmed swing geometry, not a forecast."],
        classifications={"structure_class": trend, "directional_strength_band": "STRONG" if adx >= 25 else "WEAK"},
    )


def volatility_state(*, object_id: str, technical: Mapping[str, Any], statistical: Mapping[str, Any], created_at: str) -> Mapping[str, Any]:
    tech = technical["payload"]["values"]
    stats = statistical["payload"]["values"]
    bandwidth = float(tech.get("bollinger_bandwidth_20_2") or 0.0)
    percentile = float(stats.get("absolute_return_percentile") or 0.0)
    if bandwidth < 0.04:
        state, strength = "COMPRESSED", min(1.0, (0.04 - bandwidth) / 0.04 + 0.4)
    elif percentile >= 0.75 and bandwidth >= 0.06:
        state, strength = "EXPANDING", min(1.0, 0.5 * percentile + min(bandwidth, 0.2) * 2.5)
    elif percentile <= 0.30:
        state, strength = "LOW", 1 - percentile
    else:
        state, strength = "NORMAL", 0.6
    return _state_object(
        object_id=object_id, object_type="VOLATILITY_STATE", subject=technical["subject"], effective_at=technical["effective_at"], created_at=created_at,
        source_time_range=technical["source_time_range"], inputs=[technical, statistical], method_name="BANDWIDTH_RETURN_PERCENTILE_VOLATILITY_CLASSIFIER",
        state=state, strength=strength, evidence_roles={technical["object_id"]: "VOLATILITY_TECHNICAL_EVIDENCE", statistical["object_id"]: "VOLATILITY_STATISTICAL_EVIDENCE"},
        uncertainty=["No options-implied volatility or long-run regime model is present."], classifications={"tail_shape": "HEAVY_TAILED" if float(stats.get("excess_kurtosis") or 0) > 1 else "NOT_HEAVY_TAILED_BY_WINDOW"},
    )


def momentum_state(*, object_id: str, technical: Mapping[str, Any], statistical: Mapping[str, Any], created_at: str) -> Mapping[str, Any]:
    tech, stats = technical["payload"]["values"], statistical["payload"]["values"]
    rsi, zscore = float(tech.get("rsi_14") or 50), float(stats.get("return_zscore_30") or 0)
    if rsi >= 55 and zscore > 0:
        state = "POSITIVE"
    elif rsi <= 45 and zscore < 0:
        state = "NEGATIVE"
    else:
        state = "MIXED"
    strength = min(1.0, abs(rsi - 50) / 25 * 0.6 + min(abs(zscore), 3) / 3 * 0.4)
    acceleration = "INCREASING" if abs(zscore) >= 1 else "STABLE_OR_DECAYING"
    return _state_object(
        object_id=object_id, object_type="MOMENTUM_STATE", subject=technical["subject"], effective_at=technical["effective_at"], created_at=created_at,
        source_time_range=technical["source_time_range"], inputs=[technical, statistical], method_name="RSI_RETURN_ZSCORE_MOMENTUM_CLASSIFIER",
        state=state, strength=strength, evidence_roles={technical["object_id"]: "MOMENTUM_TECHNICAL_EVIDENCE", statistical["object_id"]: "MOMENTUM_STATISTICAL_EVIDENCE"},
        uncertainty=["Momentum strength is window-dependent and does not imply continuation."], classifications={"acceleration": acceleration, "persistence": "UNMEASURED_WITHOUT_STATE_HISTORY"},
    )


def liquidity_state(*, object_id: str, microstructure: Mapping[str, Any], created_at: str, normal_spread_bps: float = 5.0, minimum_depth_quote: float = 100000.0) -> Mapping[str, Any]:
    values = microstructure["payload"]["values"]
    spread = float(values["spread_bps"])
    depth = min(float(values["bid_depth_band_quote"]), float(values["ask_depth_band_quote"]))
    if spread <= normal_spread_bps and depth >= minimum_depth_quote:
        state, strength = "HEALTHY", min(1.0, 0.5 + 0.25 * normal_spread_bps / max(spread, 0.01) + 0.25 * min(depth / minimum_depth_quote, 2) / 2)
    elif depth < minimum_depth_quote:
        state, strength = "THIN", min(1.0, 1 - depth / minimum_depth_quote)
    else:
        state, strength = "WIDE_SPREAD", min(1.0, spread / (normal_spread_bps * 3))
    return _state_object(
        object_id=object_id, object_type="LIQUIDITY_STATE", subject=microstructure["subject"], effective_at=microstructure["effective_at"], created_at=created_at,
        source_time_range=microstructure["source_time_range"], inputs=[microstructure], method_name="SPREAD_DEPTH_LIQUIDITY_CLASSIFIER", state=state, strength=strength,
        evidence_roles={microstructure["object_id"]: "LIQUIDITY_MATH_EVIDENCE"}, uncertainty=["Displayed depth is not fill probability or queue position."],
        classifications={"execution_conditions": "NORMAL" if state == "HEALTHY" else "ADVERSE", "threshold_profile": {"normal_spread_bps": normal_spread_bps, "minimum_depth_quote": minimum_depth_quote}},
    )


def participation_state(*, object_id: str, statistical: Mapping[str, Any], microstructure: Mapping[str, Any] | None, created_at: str) -> Mapping[str, Any]:
    volume_z = float(statistical["payload"]["values"].get("volume_zscore_30") or 0)
    inputs = [statistical]
    roles = {statistical["object_id"]: "PARTICIPATION_VOLUME_EVIDENCE"}
    flow = "UNKNOWN"
    if microstructure is not None:
        inputs.append(microstructure)
        roles[microstructure["object_id"]] = "PARTICIPATION_FLOW_EVIDENCE"
        ratio = microstructure["payload"]["values"].get("aggressor_ratio")
        if ratio is not None:
            flow = "BUY" if float(ratio) >= 0.55 else "SELL" if float(ratio) <= 0.45 else "BALANCED"
    state = "ABOVE_NORMAL" if volume_z >= 1 else "BELOW_NORMAL" if volume_z <= -1 else "NORMAL"
    return _state_object(
        object_id=object_id, object_type="PARTICIPATION_STATE", subject=statistical["subject"], effective_at=statistical["effective_at"], created_at=created_at,
        source_time_range=statistical["source_time_range"], inputs=inputs, method_name="VOLUME_AND_FLOW_PARTICIPATION_CLASSIFIER", state=state,
        strength=min(1.0, abs(volume_z) / 3), evidence_roles=roles, uncertainty=["Breadth is unavailable from a single instrument."] if microstructure is None else ["Breadth still requires a market universe."],
        classifications={"directional_flow": flow, "breadth": "UNAVAILABLE"},
    )


def positioning_state(*, object_id: str, funding: Mapping[str, Any], created_at: str, normal_absolute_rate: float = 0.0003) -> Mapping[str, Any]:
    rate = float(funding["payload"]["funding_rate"])
    if rate > normal_absolute_rate:
        state = "CROWDED_LONG_CANDIDATE"
    elif rate < -normal_absolute_rate:
        state = "CROWDED_SHORT_CANDIDATE"
    else:
        state = "FUNDING_NORMAL"
    strength = min(1.0, abs(rate) / max(normal_absolute_rate, 1e-12))
    return _state_object(
        object_id=object_id, object_type="POSITIONING_STATE", subject=funding["subject"], effective_at=funding["effective_at"], created_at=created_at,
        source_time_range=funding["source_time_range"], inputs=[funding], method_name="FUNDING_POSITIONING_CLASSIFIER", state=state, strength=strength,
        evidence_roles={funding["object_id"]: "FUNDING_EVIDENCE"}, uncertainty=["Open interest and liquidation data are unavailable; funding alone cannot identify who opened or closed positions."],
        classifications={"open_interest_direction": "UNAVAILABLE", "liquidation_pressure": "UNAVAILABLE", "crowding_is_hypothesis": True},
    )


def correlation_state(*, object_id: str, relative: Mapping[str, Any], created_at: str) -> Mapping[str, Any]:
    correlation = float(relative["payload"]["values"]["correlation"])
    state = "HIGH_POSITIVE" if correlation >= 0.75 else "POSITIVE" if correlation >= 0.3 else "NEGATIVE" if correlation < 0 else "WEAK"
    return _state_object(
        object_id=object_id, object_type="CORRELATION_STATE", subject=relative["subject"], effective_at=relative["effective_at"], created_at=created_at,
        source_time_range=relative["source_time_range"], inputs=[relative], method_name="ROLLING_CORRELATION_CLASSIFIER", state=state, strength=min(1.0, abs(correlation)),
        evidence_roles={relative["object_id"]: "RELATIONSHIP_EVIDENCE"}, uncertainty=["Rolling correlation is not causal and can change abruptly."],
        classifications={"relative_performance": "OUTPERFORMING" if float(relative["payload"]["values"]["relative_return"]) > 0 else "UNDERPERFORMING"},
    )


def risk_state(*, object_id: str, technical: Mapping[str, Any], statistical: Mapping[str, Any], context: Mapping[str, Any], created_at: str) -> Mapping[str, Any]:
    tech = technical["payload"]["values"]
    stats = statistical["payload"]["values"]
    rsi = float(tech.get("rsi_14") or 50)
    zscore = abs(float(stats.get("return_zscore_30") or 0))
    event_risk = context["payload"].get("scheduled_event_risk", "UNKNOWN")
    extended = rsi >= 72 or rsi <= 28 or zscore >= 2.5
    elevated = extended or event_risk in {"HIGH", "IMMINENT"}
    return _state_object(
        object_id=object_id, object_type="RISK_STATE", subject=technical["subject"],
        effective_at=technical["effective_at"], created_at=created_at,
        source_time_range=technical["source_time_range"], inputs=[technical, statistical, context],
        method_name="EXTENSION_EVENT_RISK_CLASSIFIER", state="ELEVATED" if elevated else "NORMAL",
        strength=min(1.0, max(abs(rsi - 50) / 30, zscore / 3, 1.0 if event_risk == "IMMINENT" else 0.0)),
        evidence_roles={technical["object_id"]: "EXTENSION_EVIDENCE", statistical["object_id"]: "TAIL_EVIDENCE", context["object_id"]: "EVENT_RISK_CONTEXT"},
        uncertainty=["This classifies observable extension and scheduled-event risk; it is not a loss forecast."],
        classifications={"extension": "EXTENDED" if extended else "NOT_EXTENDED", "scheduled_event_risk": event_risk},
    )


def unavailable_state(
    *, object_id: str, object_type: str, subject: Mapping[str, Any], effective_at: str,
    created_at: str, reason: str, required_evidence: Sequence[str],
) -> Mapping[str, Any]:
    if object_type not in {"TREND_STATE", "VOLATILITY_STATE", "MOMENTUM_STATE", "LIQUIDITY_STATE", "PARTICIPATION_STATE", "POSITIONING_STATE", "CORRELATION_STATE", "RISK_STATE"}:
        raise ValueError("unsupported unavailable state type")
    return build_object(
        object_id=object_id, object_type=object_type, truth_class="DETERMINISTIC_CLASSIFICATION",
        subject=subject, effective_at=effective_at, created_at=created_at,
        source_time_range={"start": effective_at, "end": effective_at}, input_refs=[],
        method={"name": "EXPLICIT_MISSING_EVIDENCE_CLASSIFIER", "version": "1.0.0", "deterministic": True},
        quality={"status": "UNAVAILABLE", "reason": reason},
        payload={"dimension": object_type.removesuffix("_STATE"), "state": "UNAVAILABLE", "strength": 0.0,
                 "evidence_refs": [], "classifications": {}, "uncertainty": [reason],
                 "required_evidence": list(required_evidence), "duplicates_measurement_values": False},
    )
