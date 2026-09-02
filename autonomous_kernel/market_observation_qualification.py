"""Read-only qualification and evidence binding for market observations.

The contract is venue-neutral. It verifies observation integrity, re-evaluates
freshness at the moment an observation is consumed, enforces sequence integrity
for sequenced microstructure streams, and creates deterministic evidence bonds
between market observations and prospective shadow decisions.

It never performs network access, trading, signing, wallet mutation, or capital
movement. Existing shadow history without an original evidence bond is reported
as legacy-unjoined and is never retroactively upgraded.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .market_data import validate_market_data_store, validate_observation
from .market_data_quality import VALID, classify_market_data
from .microstream import validate_stream_bundles
from .operations import canonical_hash


QUALIFIED = "QUALIFIED"
BLOCKED = "BLOCKED"
NOT_APPLICABLE = "NOT_APPLICABLE"
LEGACY_UNJOINED = "LEGACY_UNJOINED"
NOT_EARNED = "NOT_EARNED"


def sequence_integrity(observation: Mapping[str, Any]) -> Mapping[str, Any]:
    """Classify sequence integrity for one immutable market observation."""
    normalized = observation.get("normalized", {})
    observation_type = str(normalized.get("type", ""))

    if observation_type != "microstructure_stream_summary":
        return {
            "state": NOT_APPLICABLE,
            "required": False,
            "reason": "observation channel is not a sequenced microstructure stream",
        }

    summary = normalized.get("summary", {})
    gaps = summary.get("gaps")
    out_of_order = summary.get("out_of_order")
    sequence_scope = summary.get("sequence_scope")
    snapshot_count = int(summary.get("level2_snapshot_count", 0) or 0)
    unique_count = int(summary.get("unique_message_count", 0) or 0)
    final_book_hash = str(summary.get("final_book_hash", ""))

    reasons = []
    if sequence_scope != "CONNECTION_GLOBAL":
        reasons.append("connection_global_sequence_scope_not_proven")
    if not isinstance(gaps, list):
        reasons.append("sequence_gap_evidence_missing")
    elif gaps:
        reasons.append("sequence_gaps_observed")
    if not isinstance(out_of_order, list):
        reasons.append("out_of_order_evidence_missing")
    elif out_of_order:
        reasons.append("out_of_order_messages_observed")
    if snapshot_count < 1:
        reasons.append("level2_snapshot_missing")
    if unique_count < 1:
        reasons.append("stream_has_no_unique_messages")
    if not final_book_hash:
        reasons.append("deterministic_final_book_hash_missing")

    return {
        "state": QUALIFIED if not reasons else BLOCKED,
        "required": True,
        "sequence_scope": sequence_scope,
        "gap_count": len(gaps) if isinstance(gaps, list) else None,
        "out_of_order_count": len(out_of_order) if isinstance(out_of_order, list) else None,
        "duplicate_count": int(summary.get("duplicate_count", 0) or 0),
        "unique_message_count": unique_count,
        "level2_snapshot_count": snapshot_count,
        "level2_update_count": int(summary.get("level2_update_count", 0) or 0),
        "final_book_hash": final_book_hash or None,
        "reasons": reasons,
    }


def qualify_observation(
    observation: Mapping[str, Any],
    *,
    consumed_at: int,
    max_event_age_seconds: int = 30,
    max_transport_age_seconds: int = 30,
) -> Mapping[str, Any]:
    """Re-evaluate an observation at consumption time and fail closed."""
    integrity_errors = validate_observation(observation)
    raw = observation.get("raw", {})
    stored_quality = observation.get("quality", {})
    source_event_at = raw.get("source_event_at")
    received_at = raw.get("received_at")
    clock_skew_tolerance = int(stored_quality.get("clock_skew_tolerance_seconds", 0) or 0)

    current_quality = classify_market_data(
        provider=raw.get("provider"),
        source_event_at=int(source_event_at) if source_event_at is not None else None,
        received_at=int(received_at) if received_at is not None else None,
        observed_at=int(consumed_at),
        max_event_age_seconds=int(max_event_age_seconds),
        max_transport_age_seconds=int(max_transport_age_seconds),
        max_clock_skew_seconds=clock_skew_tolerance,
    ).to_dict()
    sequence = sequence_integrity(observation)

    reasons = list(integrity_errors)
    if stored_quality.get("status") != VALID or stored_quality.get("action_permitted") is not True:
        reasons.append("stored_quality_not_valid")
    if current_quality.get("status") != VALID or current_quality.get("action_permitted") is not True:
        reasons.append("observation_not_fresh_at_consumption")
    if sequence.get("required") and sequence.get("state") != QUALIFIED:
        reasons.append("sequence_integrity_not_qualified")

    return {
        "state": QUALIFIED if not reasons else BLOCKED,
        "observation_id": observation.get("observation_id"),
        "provider": raw.get("provider"),
        "instrument": observation.get("normalized", {}).get("instrument"),
        "channel": raw.get("channel"),
        "consumed_at": int(consumed_at),
        "stored_quality": stored_quality,
        "consumption_quality": current_quality,
        "integrity": {
            "state": QUALIFIED if not integrity_errors else BLOCKED,
            "errors": list(integrity_errors),
            "content_hash": observation.get("integrity", {}).get("content_hash"),
        },
        "sequence_integrity": sequence,
        "reasons": reasons,
    }


def _bond_content(decision: Mapping[str, Any], bindings: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    return {
        "decision_id": str(decision.get("id", "")),
        "decision_observed_at": int(decision.get("observed_at", 0) or 0),
        "actionable_at": int(decision.get("actionable_at", 0) or 0),
        "market_evidence": list(bindings),
    }


def bind_shadow_decision(
    decision: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
    *,
    max_event_age_seconds: int = 30,
    max_transport_age_seconds: int = 30,
) -> Mapping[str, Any]:
    """Create a deterministic prospective evidence bond for a shadow decision.

    The returned decision is a copy. Callers remain responsible for persisting it
    atomically with their own prospective shadow state.
    """
    decision_id = str(decision.get("id", ""))
    product = str(decision.get("product", ""))
    observed_at = decision.get("observed_at")
    if not decision_id or not product or observed_at is None:
        raise ValueError("shadow decision requires id, product, and observed_at")
    if int(decision.get("actionable_at", 0) or 0) <= int(observed_at):
        raise ValueError("shadow decision must remain prospective")
    if not observations:
        raise ValueError("shadow decision requires at least one market observation")

    bindings = []
    for observation in observations:
        instrument = str(observation.get("normalized", {}).get("instrument", ""))
        if instrument != product:
            raise ValueError(f"market observation instrument {instrument!r} does not match decision product {product!r}")
        qualification = qualify_observation(
            observation,
            consumed_at=int(observed_at),
            max_event_age_seconds=max_event_age_seconds,
            max_transport_age_seconds=max_transport_age_seconds,
        )
        if qualification["state"] != QUALIFIED:
            raise ValueError(
                f"market observation {observation.get('observation_id')} is not qualified: "
                + ", ".join(qualification["reasons"])
            )

        normalized = observation.get("normalized", {})
        signal_timestamp = decision.get("signal_candle_timestamp")
        if normalized.get("type") == "candle" and signal_timestamp is not None:
            if int(normalized.get("start_at", -1)) != int(signal_timestamp):
                raise ValueError("candle evidence does not match the decision signal candle")

        bindings.append(
            {
                "observation_id": observation.get("observation_id"),
                "provider": observation.get("raw", {}).get("provider"),
                "instrument": instrument,
                "channel": observation.get("raw", {}).get("channel"),
                "content_hash": observation.get("integrity", {}).get("content_hash"),
                "source_event_at": observation.get("raw", {}).get("source_event_at"),
                "received_at": observation.get("raw", {}).get("received_at"),
                "consumed_at": int(observed_at),
                "quality_state": qualification["consumption_quality"].get("status"),
                "sequence_state": qualification["sequence_integrity"].get("state"),
                "qualification": qualification,
            }
        )

    result = dict(decision)
    result["market_evidence"] = bindings
    result["market_evidence_bond"] = {
        "schema_version": 1,
        "algorithm": "sha256",
        "content_hash": canonical_hash(_bond_content(result, bindings)),
    }
    return result


def _load_observation(root: Path, observation_id: str) -> Mapping[str, Any] | None:
    path = root / "artifacts/market_data/observations" / f"{observation_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _bindings(decision: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = decision.get("market_evidence")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _verify_evidence_bond(decision: Mapping[str, Any]) -> list[str]:
    bindings = _bindings(decision)
    if not bindings:
        return ["market_evidence_missing"]
    bond = decision.get("market_evidence_bond", {})
    if bond.get("schema_version") != 1 or bond.get("algorithm") != "sha256":
        return ["market_evidence_bond_metadata_invalid"]
    expected = canonical_hash(_bond_content(decision, bindings))
    if bond.get("content_hash") != expected:
        return ["market_evidence_bond_hash_mismatch"]
    return []


def qualification_snapshot(
    root: Path,
    shadow: Mapping[str, Any] | None = None,
    *,
    max_event_age_seconds: int = 30,
    max_transport_age_seconds: int = 30,
) -> Mapping[str, Any]:
    """Build a deterministic read-only audit of the market observation plane."""
    root = Path(root).resolve()
    if shadow is None:
        shadow_path = root / "state/market_shadow.json"
        shadow = json.loads(shadow_path.read_text(encoding="utf-8")) if shadow_path.is_file() else {"decisions": []}

    store_errors = validate_market_data_store(root)
    stream_errors = validate_stream_bundles(root)

    index_path = root / "state/market_data.json"
    market_index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {"items": []}
    sequence_counts = {QUALIFIED: 0, BLOCKED: 0, NOT_APPLICABLE: 0}
    quality_counts: dict[str, int] = {}
    observation_audits = []
    for item in market_index.get("items", []):
        observation = _load_observation(root, str(item.get("observation_id", "")))
        if observation is None:
            continue
        sequence = sequence_integrity(observation)
        sequence_counts[sequence["state"]] = sequence_counts.get(sequence["state"], 0) + 1
        quality_status = str(observation.get("quality", {}).get("status", "UNKNOWN"))
        quality_counts[quality_status] = quality_counts.get(quality_status, 0) + 1
        observation_audits.append(
            {
                "observation_id": observation.get("observation_id"),
                "provider": observation.get("raw", {}).get("provider"),
                "instrument": observation.get("normalized", {}).get("instrument"),
                "channel": observation.get("raw", {}).get("channel"),
                "quality_status": quality_status,
                "sequence_integrity": sequence,
                "content_hash": observation.get("integrity", {}).get("content_hash"),
            }
        )

    decision_audits = []
    joined = qualified_joined = blocked_joined = legacy = 0
    for decision in shadow.get("decisions", []):
        bindings = _bindings(decision)
        if not bindings:
            legacy += 1
            decision_audits.append(
                {
                    "decision_id": decision.get("id"),
                    "state": LEGACY_UNJOINED,
                    "reason": "decision predates prospective market-evidence binding; no retroactive upgrade permitted",
                    "observation_ids": [],
                }
            )
            continue

        joined += 1
        binding_results = []
        reasons = _verify_evidence_bond(decision)
        observation_ids = []
        for binding in bindings:
            observation_id = str(binding.get("observation_id", ""))
            observation_ids.append(observation_id)
            observation = _load_observation(root, observation_id)
            if observation is None:
                reasons.append(f"missing_market_observation:{observation_id}")
                continue
            if binding.get("content_hash") != observation.get("integrity", {}).get("content_hash"):
                reasons.append(f"bound_observation_hash_mismatch:{observation_id}")
            if binding.get("provider") != observation.get("raw", {}).get("provider"):
                reasons.append(f"bound_provider_mismatch:{observation_id}")
            if binding.get("instrument") != observation.get("normalized", {}).get("instrument"):
                reasons.append(f"bound_instrument_mismatch:{observation_id}")
            if binding.get("channel") != observation.get("raw", {}).get("channel"):
                reasons.append(f"bound_channel_mismatch:{observation_id}")
            if int(binding.get("consumed_at", -1)) != int(decision.get("observed_at", -2)):
                reasons.append(f"bound_consumption_time_mismatch:{observation_id}")

            qualification = qualify_observation(
                observation,
                consumed_at=int(decision["observed_at"]),
                max_event_age_seconds=max_event_age_seconds,
                max_transport_age_seconds=max_transport_age_seconds,
            )
            binding_results.append(qualification)
            if qualification["state"] != QUALIFIED:
                reasons.extend(qualification["reasons"])
            if binding.get("quality_state") != qualification["consumption_quality"].get("status"):
                reasons.append(f"bound_quality_state_mismatch:{observation_id}")
            if binding.get("sequence_state") != qualification["sequence_integrity"].get("state"):
                reasons.append(f"bound_sequence_state_mismatch:{observation_id}")

        state = QUALIFIED if not reasons and len(binding_results) == len(bindings) else BLOCKED
        if state == QUALIFIED:
            qualified_joined += 1
        else:
            blocked_joined += 1
        decision_audits.append(
            {
                "decision_id": decision.get("id"),
                "state": state,
                "observation_ids": observation_ids,
                "bindings": binding_results,
                "reasons": reasons,
            }
        )

    prospective_join_state = NOT_APPLICABLE
    if joined:
        prospective_join_state = QUALIFIED if blocked_joined == 0 else BLOCKED

    return {
        "schema_version": 1,
        "policy": {
            "venue_neutral": True,
            "fail_closed": True,
            "retroactive_evidence_binding_permitted": False,
            "max_event_age_seconds": int(max_event_age_seconds),
            "max_transport_age_seconds": int(max_transport_age_seconds),
        },
        "market_plane": {
            "observation_count": len(observation_audits),
            "quality_counts": quality_counts,
            "sequence_counts": sequence_counts,
            "store_integrity_state": QUALIFIED if not store_errors else BLOCKED,
            "store_integrity_errors": store_errors,
            "stream_bundle_integrity_state": QUALIFIED if not stream_errors else BLOCKED,
            "stream_bundle_integrity_errors": stream_errors,
            "observations": observation_audits,
        },
        "shadow_evidence": {
            "decision_count": len(shadow.get("decisions", [])),
            "legacy_unjoined_count": legacy,
            "prospectively_joined_count": joined,
            "qualified_joined_count": qualified_joined,
            "blocked_joined_count": blocked_joined,
            "prospective_join_state": prospective_join_state,
            "certification_state": (
                QUALIFIED
                if joined > 0 and blocked_joined == 0 and not store_errors and not stream_errors
                else NOT_EARNED if joined == 0 else BLOCKED
            ),
            "decisions": decision_audits,
        },
    }
