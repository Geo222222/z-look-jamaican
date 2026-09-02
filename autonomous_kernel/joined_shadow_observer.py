"""Join a fresh public observer window into successor zero-capital shadow state.

This is an integration/acceptance handoff, not a trading strategy. It records a
neutral target (0) whose meaning is: the perception pipeline accepted this exact
qualified market observation prospectively. It has no execution authority and
makes no claim of economic edge.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from .market_observation_qualification import QUALIFIED, qualification_snapshot
from .qualified_shadow import (
    ShadowDecisionProposal,
    load_qualified_shadow_state,
    record_qualified_shadow_decision,
    validate_qualified_shadow_state,
)


DEFAULT_POLICY_PATH = "config/qualified_shadow.json"


def _epoch_now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


@dataclass(frozen=True)
class JoinedShadowPolicy:
    program_id: str
    handoff_mode: str
    target_position: int
    strategy_id: str
    rationale_code: str
    actionable_delay_seconds: int
    max_event_age_seconds: int
    max_transport_age_seconds: int
    capital_effect: str
    execution_authority: bool
    schema_version: int = 1

    @classmethod
    def load(cls, root: Path, path: Optional[Path] = None) -> "JoinedShadowPolicy":
        policy_path = path or (root / DEFAULT_POLICY_PATH)
        raw = json.loads(policy_path.read_text(encoding="utf-8"))
        policy = cls(
            schema_version=int(raw.get("schema_version", 0)),
            program_id=str(raw.get("program_id", "")),
            handoff_mode=str(raw.get("handoff_mode", "")),
            target_position=int(raw.get("target_position", 999)),
            strategy_id=str(raw.get("strategy_id", "")),
            rationale_code=str(raw.get("rationale_code", "")),
            actionable_delay_seconds=int(raw.get("actionable_delay_seconds", 0)),
            max_event_age_seconds=int(raw.get("max_event_age_seconds", 0)),
            max_transport_age_seconds=int(raw.get("max_transport_age_seconds", 0)),
            capital_effect=str(raw.get("capital_effect", "")),
            execution_authority=raw.get("execution_authority") is True,
        )
        policy.validate()
        return policy

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported joined-shadow policy schema")
        if self.program_id != "QUALIFIED-MARKET-SHADOW-V1":
            raise ValueError("joined-shadow policy program identity mismatch")
        if self.handoff_mode != "PERCEPTION_ACCEPTANCE_ONLY":
            raise ValueError("joined-shadow v1 handoff must remain PERCEPTION_ACCEPTANCE_ONLY")
        if self.target_position != 0:
            raise ValueError("perception-acceptance handoff must remain neutral target 0")
        if not self.strategy_id or not self.rationale_code:
            raise ValueError("joined-shadow strategy/rationale identifiers are required")
        if self.actionable_delay_seconds <= 0:
            raise ValueError("joined-shadow actionable delay must be positive")
        if self.max_event_age_seconds <= 0 or self.max_transport_age_seconds <= 0:
            raise ValueError("joined-shadow freshness limits must be positive")
        if self.capital_effect != "NONE" or self.execution_authority:
            raise ValueError("joined-shadow perception handoff cannot have financial or execution authority")


def _load_observation(root: Path, observation_id: str) -> Mapping[str, Any]:
    if not observation_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in observation_id):
        raise ValueError("unsafe observer observation_id")
    path = root / "artifacts/market_data/observations" / f"{observation_id}.json"
    if not path.is_file():
        raise ValueError(f"observer observation is missing: {observation_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _existing_join(root: Path, decision_id: str) -> Mapping[str, Any] | None:
    state = load_qualified_shadow_state(root)
    return next((item for item in state.get("decisions", []) if item.get("id") == decision_id), None)


def _joined_result(status: str, window_id: str, observation_id: str, decision: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "status": status,
        "window_id": window_id,
        "observation_id": observation_id,
        "decision_id": decision["id"],
        "target_position": decision["target_position"],
        "strategy_id": decision["strategy_id"],
        "rationale_code": decision["rationale_code"],
        "consumed_at": decision["observed_at"],
        "market_evidence_bond": decision["market_evidence_bond"],
        "capital_effect": decision["capital_effect"],
        "execution_authority": decision["execution_authority"],
    }


def _verify_existing_join(root: Path, decision_id: str) -> None:
    state_errors = validate_qualified_shadow_state(root)
    if state_errors:
        raise RuntimeError("existing joined-shadow state failed validation: " + "; ".join(state_errors))
    snapshot = qualification_snapshot(root, {"decisions": []})
    evidence = snapshot["shadow_evidence"]
    audit = next(
        (
            item
            for item in evidence.get("decisions", [])
            if item.get("decision_id") == decision_id
            and item.get("source_state") == "state/qualified_market_shadow.json"
        ),
        None,
    )
    if audit is None or audit.get("state") != QUALIFIED:
        raise RuntimeError("existing observer-window join no longer qualifies")
    if evidence.get("certification_state") != QUALIFIED:
        raise RuntimeError("joined-shadow certification is blocked; refusing idempotent success")


def join_observer_window(
    root: Path,
    window: Mapping[str, Any],
    *,
    consumed_at: Optional[int] = None,
    policy_path: Optional[Path] = None,
) -> Mapping[str, Any]:
    """Bind one successful observer window into neutral successor shadow state.

    A stale/otherwise unqualified observation is reported as a skipped handoff
    and is never persisted as a successor decision. Structural/configuration
    errors remain exceptions because they indicate an operator/code defect.
    """
    root = root.resolve()
    policy = JoinedShadowPolicy.load(root, policy_path)
    if str(window.get("quality", "")) != "VALID":
        return {
            "status": "SKIPPED_WINDOW_NOT_VALID",
            "window_id": window.get("window_id"),
            "observation_id": window.get("observation_id"),
        }
    window_id = str(window.get("window_id", ""))
    stream_id = str(window.get("stream_id", ""))
    observation_id = str(window.get("observation_id", ""))
    if not window_id or not stream_id or not observation_id:
        raise ValueError("observer window requires window_id, stream_id, and observation_id")

    observation = _load_observation(root, observation_id)
    if observation.get("observation_id") != observation_id:
        raise ValueError("observer window/observation identity mismatch")
    normalized = observation.get("normalized", {})
    raw = observation.get("raw", {})
    if normalized.get("type") != "microstructure_stream_summary" or raw.get("channel") != "microstructure_stream":
        raise ValueError("observer handoff requires a canonical microstructure stream summary observation")
    if str(normalized.get("stream_id", "")) != stream_id:
        raise ValueError("observer window stream_id does not match immutable observation")

    decision_id = f"JOIN-{window_id}"
    existing = _existing_join(root, decision_id)
    if existing is not None:
        bound_ids = [str(item.get("observation_id", "")) for item in existing.get("market_evidence", [])]
        if bound_ids != [observation_id]:
            raise RuntimeError("existing observer-window join points to different market evidence")
        _verify_existing_join(root, decision_id)
        return _joined_result("ALREADY_JOINED_NEUTRAL_PERCEPTION", window_id, observation_id, existing)

    if observation.get("quality", {}).get("status") != "VALID":
        return {
            "status": "SKIPPED_OBSERVATION_NOT_VALID",
            "window_id": window_id,
            "observation_id": observation_id,
        }

    now = _epoch_now() if consumed_at is None else int(consumed_at)
    proposal = ShadowDecisionProposal(
        decision_id=decision_id,
        product=str(normalized.get("instrument", "")),
        observed_at=now,
        actionable_at=now + policy.actionable_delay_seconds,
        target_position=policy.target_position,
        strategy_id=policy.strategy_id,
        rationale_code=policy.rationale_code,
        signal_candle_timestamp=None,
    )
    try:
        decision = record_qualified_shadow_decision(
            root,
            proposal,
            [observation_id],
            max_event_age_seconds=policy.max_event_age_seconds,
            max_transport_age_seconds=policy.max_transport_age_seconds,
        )
    except ValueError as exc:
        message = str(exc)
        if "is not qualified" in message or "fresh" in message.lower() or "stale" in message.lower():
            return {
                "status": "SKIPPED_NOT_FRESH_OR_QUALIFIED",
                "window_id": window_id,
                "observation_id": observation_id,
                "consumed_at": now,
                "reason": message,
            }
        raise

    return _joined_result("JOINED_NEUTRAL_PERCEPTION", window_id, observation_id, decision)


def validate_joined_shadow_policy(root: Path) -> list[str]:
    """Validate the canonical neutral handoff policy without performing a handoff."""
    path = root.resolve() / DEFAULT_POLICY_PATH
    if not path.is_file():
        return [f"missing required joined-shadow policy: {DEFAULT_POLICY_PATH}"]
    try:
        JoinedShadowPolicy.load(root.resolve(), path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return [f"{DEFAULT_POLICY_PATH}: {exc}"]
    return []
