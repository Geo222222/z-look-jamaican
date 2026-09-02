"""Prospective zero-capital shadow decisions with mandatory market-evidence bonds.

This module is deliberately separate from EXP-MKT-002. It does not fetch market
information, choose a trading strategy, execute orders, move capital, or mutate
the frozen experiment state. A caller supplies an explicit decision proposal
and immutable observation IDs. The observations must already exist in the
canonical market-data store and must qualify at the proposal's observation time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .market_data import _atomic_json
from .market_observation_qualification import bind_shadow_decision
from .operations import canonical_hash


STATE_RELATIVE_PATH = "state/qualified_market_shadow.json"
PROGRAM_ID = "QUALIFIED-MARKET-SHADOW-V1"
MODE = "zero_capital_evidence_bound_shadow"


@dataclass(frozen=True)
class ShadowDecisionProposal:
    decision_id: str
    product: str
    observed_at: int
    actionable_at: int
    target_position: int
    strategy_id: str
    rationale_code: str
    signal_candle_timestamp: int | None = None
    schema_version: int = 1

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported shadow decision proposal schema")
        for name, value in (
            ("decision_id", self.decision_id),
            ("product", self.product),
            ("strategy_id", self.strategy_id),
            ("rationale_code", self.rationale_code),
        ):
            if not str(value).strip():
                raise ValueError(f"{name} is required")
        if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in self.decision_id):
            raise ValueError("unsafe decision_id")
        if self.observed_at < 0 or self.actionable_at <= self.observed_at:
            raise ValueError("shadow decision must be strictly prospective")
        if self.target_position not in (-1, 0, 1):
            raise ValueError("target_position must be -1, 0, or 1")
        if self.signal_candle_timestamp is not None and self.signal_candle_timestamp < 0:
            raise ValueError("signal_candle_timestamp must be non-negative")

    def to_decision(self) -> Mapping[str, Any]:
        self.validate()
        decision = {
            "schema_version": 1,
            "id": self.decision_id,
            "product": self.product,
            "observed_at": int(self.observed_at),
            "actionable_at": int(self.actionable_at),
            "target_position": int(self.target_position),
            "strategy_id": self.strategy_id,
            "rationale_code": self.rationale_code,
            "status": "pending",
            "capital_effect": "NONE",
            "execution_authority": False,
        }
        if self.signal_candle_timestamp is not None:
            decision["signal_candle_timestamp"] = int(self.signal_candle_timestamp)
        return decision


def _empty_state() -> Mapping[str, Any]:
    return {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "mode": MODE,
        "authority": "Prospective zero-capital shadow decisions only; no execution authority.",
        "legacy_experiment_state_mutable": False,
        "updated_at": None,
        "decisions": [],
        "summary": {
            "total": 0,
            "pending": 0,
            "resolved": 0,
            "nonzero_target": 0,
            "evidence_bound": 0,
        },
    }


def load_qualified_shadow_state(root: Path) -> Mapping[str, Any]:
    path = root.resolve() / STATE_RELATIVE_PATH
    if not path.is_file():
        return _empty_state()
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema_version") != 1 or state.get("program_id") != PROGRAM_ID:
        raise ValueError("qualified shadow state identity/schema mismatch")
    if state.get("mode") != MODE or not isinstance(state.get("decisions"), list):
        raise ValueError("qualified shadow state mode/decisions invalid")
    if state.get("legacy_experiment_state_mutable") is not False:
        raise ValueError("qualified shadow state may not claim authority over legacy experiment state")
    return state


def _load_observation(root: Path, observation_id: str) -> Mapping[str, Any]:
    if not observation_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in observation_id):
        raise ValueError("unsafe observation_id")
    path = root / "artifacts/market_data/observations" / f"{observation_id}.json"
    if not path.is_file():
        raise ValueError(f"market observation not found: {observation_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _summarize(decisions: Sequence[Mapping[str, Any]]) -> Mapping[str, int]:
    return {
        "total": len(decisions),
        "pending": sum(item.get("status") == "pending" for item in decisions),
        "resolved": sum(item.get("status") == "resolved" for item in decisions),
        "nonzero_target": sum(int(item.get("target_position", 0)) != 0 for item in decisions),
        "evidence_bound": sum(bool(item.get("market_evidence_bond")) for item in decisions),
    }


def record_qualified_shadow_decision(
    root: Path,
    proposal: ShadowDecisionProposal,
    observation_ids: Sequence[str],
    *,
    max_event_age_seconds: int,
    max_transport_age_seconds: int,
) -> Mapping[str, Any]:
    """Qualify, bind, and atomically persist one prospective shadow decision."""
    root = root.resolve()
    proposal.validate()
    if max_event_age_seconds <= 0 or max_transport_age_seconds <= 0:
        raise ValueError("freshness limits must be positive explicit parameters")
    if not observation_ids:
        raise ValueError("at least one observation_id is required")
    if len(set(observation_ids)) != len(observation_ids):
        raise ValueError("duplicate observation IDs are not permitted in one evidence bond")

    observations = [_load_observation(root, observation_id) for observation_id in observation_ids]
    decision = dict(proposal.to_decision())
    decision["freshness_policy"] = {
        "max_event_age_seconds": int(max_event_age_seconds),
        "max_transport_age_seconds": int(max_transport_age_seconds),
    }
    bound = dict(
        bind_shadow_decision(
            decision,
            observations,
            max_event_age_seconds=max_event_age_seconds,
            max_transport_age_seconds=max_transport_age_seconds,
        )
    )
    bound["decision_content_hash"] = canonical_hash(
        {key: value for key, value in bound.items() if key != "decision_content_hash"}
    )

    # Imported lazily so the durable-state control plane can validate this module
    # without creating an import cycle during module initialization.
    from .store import writer_lock

    with writer_lock(root):
        legacy_path = root / "state/market_shadow.json"
        legacy_before = legacy_path.read_bytes() if legacy_path.is_file() else None

        state = dict(load_qualified_shadow_state(root))
        decisions = [dict(item) for item in state.get("decisions", [])]
        existing = next((item for item in decisions if item.get("id") == proposal.decision_id), None)
        if existing is not None:
            if existing.get("decision_content_hash") != bound["decision_content_hash"]:
                raise RuntimeError("qualified shadow decision ID conflict")
            return existing

        decisions.append(bound)
        decisions.sort(key=lambda item: (int(item.get("observed_at", 0)), str(item.get("id", ""))))
        state["decisions"] = decisions
        state["updated_at"] = int(proposal.observed_at)
        state["summary"] = _summarize(decisions)
        _atomic_json(root / STATE_RELATIVE_PATH, state)

        if legacy_before is not None and legacy_path.read_bytes() != legacy_before:
            raise RuntimeError("legacy EXP-MKT-002 state changed during successor shadow persistence")
    return bound


def validate_qualified_shadow_state(root: Path) -> list[str]:
    """Validate successor-state structure and immutable decision hashes."""
    path = root.resolve() / STATE_RELATIVE_PATH
    if not path.is_file():
        return []
    errors: list[str] = []
    try:
        state = load_qualified_shadow_state(root)
    except (ValueError, json.JSONDecodeError) as exc:
        return [f"{STATE_RELATIVE_PATH}: {exc}"]
    seen: set[str] = set()
    for item in state.get("decisions", []):
        decision_id = str(item.get("id", ""))
        if not decision_id:
            errors.append("qualified shadow decision missing id")
            continue
        if decision_id in seen:
            errors.append(f"duplicate qualified shadow decision id: {decision_id}")
        seen.add(decision_id)
        expected = canonical_hash({key: value for key, value in item.items() if key != "decision_content_hash"})
        if item.get("decision_content_hash") != expected:
            errors.append(f"qualified shadow decision hash mismatch: {decision_id}")
        if item.get("capital_effect") != "NONE" or item.get("execution_authority") is not False:
            errors.append(f"qualified shadow decision exceeds zero-capital authority: {decision_id}")
        if not item.get("market_evidence") or not item.get("market_evidence_bond"):
            errors.append(f"qualified shadow decision lacks market evidence bond: {decision_id}")
    if state.get("summary") != _summarize(state.get("decisions", [])):
        errors.append("qualified shadow summary does not match decisions")
    return errors
