"""Temporal transitions between comparable state objects."""

from datetime import datetime
from typing import Any, Mapping, Sequence

from ..core import MarketObjectRef, build_object, object_id_from_ref


def state_transition(
    *, object_id: str, previous_state: Mapping[str, Any], current_state: Mapping[str, Any],
    created_at: str, intermediate_state_refs: Sequence[Mapping[str, Any]] = (),
) -> Mapping[str, Any]:
    if previous_state.get("layer") != "STATE" or current_state.get("layer") != "STATE":
        raise ValueError("state transition requires state objects")
    if previous_state["object_type"] != current_state["object_type"] or previous_state["subject"] != current_state["subject"]:
        raise ValueError("state transition inputs must share type and subject")
    previous_label = previous_state["payload"]["state"]
    current_label = current_state["payload"]["state"]
    started = datetime.fromisoformat(previous_state["effective_at"].replace("Z", "+00:00"))
    confirmed = datetime.fromisoformat(current_state["effective_at"].replace("Z", "+00:00"))
    duration_seconds = max(0.0, (confirmed - started).total_seconds())
    refs = [MarketObjectRef.to(previous_state["object_id"], "FROM_STATE", expected_object_type=previous_state["object_type"]), MarketObjectRef.to(current_state["object_id"], "TO_STATE", expected_object_type=current_state["object_type"])]
    for item in intermediate_state_refs:
        refs.append(MarketObjectRef.to(item["object_id"], "INTERMEDIATE_STATE", expected_object_type=item["object_type"]))
    for reference in current_state["payload"].get("evidence_refs", []):
        refs.append(MarketObjectRef.to(object_id_from_ref(reference), "SUPPORTS_TRANSITION"))
    strength = abs(float(current_state["payload"]["strength"]) - float(previous_state["payload"]["strength"]))
    if previous_label != current_label:
        strength = max(strength, 0.5)
    return build_object(
        object_id=object_id, object_type="STATE_TRANSITION", truth_class="DETERMINISTIC_CLASSIFICATION",
        subject=current_state["subject"], effective_at=current_state["effective_at"], created_at=created_at,
        source_time_range={"start": previous_state["effective_at"], "end": current_state["effective_at"]}, input_refs=refs,
        method={"name": "STATE_SEQUENCE_COMPARATOR", "version": "1.0.0", "deterministic": True},
        quality={"status": "VALID", "state_changed": previous_label != current_label},
        payload={"dimension": current_state["payload"]["dimension"], "from": previous_label, "to": current_label, "started_at": previous_state["effective_at"], "confirmed_at": current_state["effective_at"], "duration_before_transition_seconds": duration_seconds, "transition_strength": round(min(1.0, strength), 4), "from_state_ref": f"market://{previous_state['object_id']}", "to_state_ref": f"market://{current_state['object_id']}", "intermediate_state_refs": [f"market://{item['object_id']}" for item in intermediate_state_refs], "supporting_evidence_refs": list(current_state["payload"]["evidence_refs"])},
    )
