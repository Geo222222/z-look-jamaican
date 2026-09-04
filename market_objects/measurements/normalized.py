"""Normalization layer: source fields become typed measurements, not interpretation."""

from typing import Any, Mapping

from ..core import MarketObjectRef, build_object


def normalized_price_measurement(
    *, object_id: str, evidence: Mapping[str, Any], created_at: str,
) -> Mapping[str, Any]:
    if evidence.get("object_type") != "MARKET_OBSERVATION":
        raise ValueError("normalized price measurement requires MARKET_OBSERVATION evidence")
    payload = evidence["payload"]
    return build_object(
        object_id=object_id, object_type="NORMALIZED_MEASUREMENT", truth_class="NORMALIZED_MEASUREMENT",
        subject=evidence["subject"], effective_at=evidence["effective_at"], created_at=created_at,
        source_time_range=evidence["source_time_range"],
        input_refs=[MarketObjectRef.to(evidence["object_id"], "NORMALIZES", expected_object_type="MARKET_OBSERVATION")],
        method={"name": "PRICE_MEASUREMENT_NORMALIZER", "version": "1.0.0", "deterministic": True},
        quality={"status": evidence["quality"]["status"], "inherited_from": f"market://{evidence['object_id']}"},
        payload={
            "evidence_ref": f"market://{evidence['object_id']}",
            "values": {key: payload[key] for key in ("open", "high", "low", "close", "volume", "quote_volume", "trade_count", "taker_buy_quote_volume")},
            "units": {"price": "quote_asset", "volume": "base_asset", "quote_volume": "quote_asset"},
        },
    )


def normalized_price_series(
    *, object_id: str, evidence_objects: list[Mapping[str, Any]], created_at: str,
) -> Mapping[str, Any]:
    if not evidence_objects or any(item.get("object_type") != "MARKET_OBSERVATION" for item in evidence_objects):
        raise ValueError("normalized price series requires MARKET_OBSERVATION objects")
    ordered = sorted(evidence_objects, key=lambda item: item["effective_at"])
    subject = ordered[0]["subject"]
    if any(item["subject"] != subject for item in ordered):
        raise ValueError("price series evidence must share one subject")
    timestamps = [item["effective_at"] for item in ordered]
    if len(timestamps) != len(set(timestamps)):
        raise ValueError("price series timestamps must be unique")
    refs = [MarketObjectRef.to(item["object_id"], "NORMALIZES", expected_object_type="MARKET_OBSERVATION") for item in ordered]
    rows = [
        {"timestamp": item["effective_at"], **{key: item["payload"][key] for key in ("open", "high", "low", "close", "volume", "quote_volume", "trade_count", "taker_buy_quote_volume")}}
        for item in ordered
    ]
    return build_object(
        object_id=object_id, object_type="NORMALIZED_MEASUREMENT", truth_class="NORMALIZED_MEASUREMENT",
        subject=subject, effective_at=ordered[-1]["effective_at"], created_at=created_at,
        source_time_range={"start": ordered[0]["effective_at"], "end": ordered[-1]["effective_at"], "interval": subject.get("interval")},
        input_refs=refs, method={"name": "PRICE_SERIES_NORMALIZER", "version": "1.0.0", "deterministic": True},
        quality={"status": "VALID", "input_count": len(ordered)},
        payload={"evidence_refs": [f"market://{item['object_id']}" for item in ordered], "row_count": len(rows), "rows": rows},
    )
