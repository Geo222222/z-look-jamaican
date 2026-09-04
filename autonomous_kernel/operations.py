"""Canonical pre-live execution contracts and deterministic zero-exposure plane."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping


CAPABILITY_STATES = (
    "DISCOVERED", "HYPOTHESIS", "PREREGISTERED", "BACKTEST_SUPPORTED",
    "PROSPECTIVE_SUPPORTED", "REPLAY_QUALIFIED", "SHADOW_QUALIFIED",
    "EXECUTION_PLANE_QUALIFIED", "CAPITAL_ELIGIBLE", "LIVE",
)
EXECUTION_MODES = {"SHADOW", "SIMULATION", "LIVE"}
SECRET_FIELDS = {"private_key", "seed_phrase", "mnemonic", "password", "api_key", "secret_value", "credential_value"}


def _decimal(value: str, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a decimal string") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return parsed


def canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ExecutionRequest:
    request_id: str
    idempotency_key: str
    capability_id: str
    decision_id: str
    market_observation_id: str
    mode: str
    instrument: str
    side: str
    order_type: str
    quantity: str
    limit_price: str | None
    created_at: str
    capital_effect: str = "NONE"
    schema_version: int = 1

    def validate(self) -> None:
        for name in ("request_id", "idempotency_key", "capability_id", "decision_id", "market_observation_id", "instrument", "created_at"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if self.mode not in EXECUTION_MODES:
            raise ValueError("mode must be SHADOW, SIMULATION, or LIVE")
        if self.side not in {"BUY", "SELL", "HOLD"}:
            raise ValueError("side must be BUY, SELL, or HOLD")
        if self.order_type not in {"MARKET", "LIMIT", "NONE"}:
            raise ValueError("unsupported order_type")
        _decimal(self.quantity, "quantity")
        if self.order_type == "LIMIT":
            if self.limit_price is None or _decimal(self.limit_price, "limit_price") <= 0:
                raise ValueError("positive limit_price is required for LIMIT")
        if self.capital_effect != "NONE" and self.mode != "LIVE":
            raise ValueError("pre-live requests must declare capital_effect NONE")

    def to_dict(self) -> Mapping[str, Any]:
        return asdict(self)


def validate_capability_registry(document: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if document.get("schema_version") != 1:
        errors.append("state/capabilities.json: schema_version must be 1")
    items = document.get("items")
    if not isinstance(items, list):
        return errors + ["state/capabilities.json: items must be a list"]
    ids: set[str] = set()
    for item in items:
        capability_id = str(item.get("id", ""))
        if not capability_id or capability_id in ids:
            errors.append("state/capabilities.json: IDs must be present and unique")
        ids.add(capability_id)
        if item.get("state") not in CAPABILITY_STATES:
            errors.append(f"state/capabilities.json: {capability_id} has invalid state")
        if not isinstance(item.get("evidence_ids"), list):
            errors.append(f"state/capabilities.json: {capability_id} evidence_ids must be a list")
        if item.get("live_enabled") is not False:
            errors.append(f"state/capabilities.json: {capability_id} live_enabled must remain false")
        if item.get("operational_status") not in {"ACTIVE", "SUSPENDED", "REJECTED", "REVOKED"}:
            errors.append(f"state/capabilities.json: {capability_id} has invalid operational_status")
    return errors


def promote_capability(item: Mapping[str, Any], new_state: str, evidence_ids: list[str]) -> Mapping[str, Any]:
    current = str(item.get("state"))
    if current not in CAPABILITY_STATES or new_state not in CAPABILITY_STATES:
        raise ValueError("unknown capability state")
    if CAPABILITY_STATES.index(new_state) != CAPABILITY_STATES.index(current) + 1:
        raise ValueError("capability promotions must advance exactly one state")
    if not evidence_ids:
        raise ValueError("promotion requires evidence")
    updated = dict(item)
    updated["state"] = new_state
    updated["evidence_ids"] = list(dict.fromkeys([*item.get("evidence_ids", []), *evidence_ids]))
    updated["live_enabled"] = False
    return updated


def evidence_bound_promotion(item: Mapping[str, Any], new_state: str, evidence: list[Mapping[str, Any]], rule_id: str, transition_at: str, root: Path) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if new_state in {"CAPITAL_ELIGIBLE", "LIVE"}:
        raise PermissionError("CAPITAL_ELIGIBLE and LIVE require a separate Governor authorization mechanism")
    if item.get("operational_status") != "ACTIVE":
        raise ValueError("only ACTIVE capabilities may be promoted")
    if not rule_id or not transition_at or not evidence:
        raise ValueError("promotion requires deterministic rule, timestamp, and evidence")
    verified = []
    for artifact in evidence:
        path = (root / str(artifact.get("path", ""))).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("promotion evidence path escapes repository") from exc
        if not path.is_file():
            raise ValueError(f"promotion evidence missing: {artifact.get('path')}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != artifact.get("sha256"):
            raise ValueError(f"promotion evidence hash mismatch: {artifact.get('path')}")
        if not artifact.get("qualifying_test"):
            raise ValueError("promotion evidence requires qualifying_test")
        verified.append(dict(artifact))
    previous = str(item.get("state"))
    updated = promote_capability(item, new_state, [str(artifact.get("evidence_id")) for artifact in evidence])
    transition = {
        "schema_version": 1, "transition_id": f"CAPTRANS-{item.get('id')}-{new_state}",
        "capability_id": item.get("id"), "transition_at": transition_at,
        "previous_state": previous, "new_state": new_state, "outcome": "PROMOTED",
        "deterministic_rule_id": rule_id, "evidence": verified,
        "approved_by": "deterministic_capability_rule", "model_direct_authority": False,
    }
    return updated, transition


def capability_non_success(item: Mapping[str, Any], outcome: str, reason: str, evidence_ids: list[str], transition_at: str) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if outcome not in {"SUSPENDED", "REJECTED", "REVOKED"}:
        raise ValueError("invalid non-success capability outcome")
    if not reason or not evidence_ids:
        raise ValueError("non-success outcome requires reason and evidence")
    updated = dict(item)
    updated["operational_status"] = outcome
    updated["live_enabled"] = False
    transition = {"schema_version": 1, "transition_id": f"CAPTRANS-{item.get('id')}-{outcome}-{transition_at}", "capability_id": item.get("id"), "transition_at": transition_at, "previous_state": item.get("state"), "new_state": item.get("state"), "outcome": outcome, "reason": reason, "evidence_ids": evidence_ids, "model_direct_authority": False}
    return updated, transition


def validate_capability_transitions(records: list[Mapping[str, Any]], root: Path) -> list[str]:
    errors: list[str] = []
    ids: set[str] = set()
    for record in records:
        transition_id = str(record.get("transition_id", ""))
        if not transition_id or transition_id in ids:
            errors.append("state/capability_transitions.jsonl: transition IDs must be present and unique")
        ids.add(transition_id)
        if record.get("outcome") == "PROMOTED":
            previous, new = record.get("previous_state"), record.get("new_state")
            if previous not in CAPABILITY_STATES or new not in CAPABILITY_STATES or CAPABILITY_STATES.index(new) != CAPABILITY_STATES.index(previous) + 1:
                errors.append(f"state/capability_transitions.jsonl: {transition_id} skipped lifecycle state")
            if new in {"CAPITAL_ELIGIBLE", "LIVE"}:
                errors.append(f"state/capability_transitions.jsonl: {transition_id} uses forbidden capital/live promotion")
            if not record.get("deterministic_rule_id") or not record.get("transition_at"):
                errors.append(f"state/capability_transitions.jsonl: {transition_id} missing rule/timestamp")
            for artifact in record.get("evidence", []):
                path = root / str(artifact.get("path", ""))
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact.get("sha256"):
                    errors.append(f"state/capability_transitions.jsonl: {transition_id} evidence integrity failed")
        elif record.get("outcome") not in {"SUSPENDED", "REJECTED", "REVOKED"}:
            errors.append(f"state/capability_transitions.jsonl: {transition_id} invalid outcome")
    return errors


def authorize_execution(request: ExecutionRequest, capability: Mapping[str, Any], governor: Mapping[str, Any], authorized_at: str) -> Mapping[str, Any]:
    request.validate()
    reasons: list[str] = []
    state = str(capability.get("state", ""))
    if request.capability_id != capability.get("id"):
        reasons.append("capability_mismatch")
    if state not in CAPABILITY_STATES or CAPABILITY_STATES.index(state) < CAPABILITY_STATES.index("PREREGISTERED"):
        reasons.append("capability_not_preregistered")
    if request.mode == "LIVE":
        reasons.append("live_execution_disabled")
    if governor.get("production_financial_trading") != "disabled":
        reasons.append("governor_snapshot_unexpected")
    for field in ("max_single_trade_usd", "max_concurrent_financial_exposure_usd", "max_daily_loss_usd"):
        if governor.get(field) != 0:
            reasons.append(f"governor_{field}_not_zero")
    if request.capital_effect != "NONE":
        reasons.append("nonzero_capital_effect_forbidden")
    allowed = not reasons and request.mode in {"SHADOW", "SIMULATION"}
    return {
        "schema_version": 1,
        "authorization_id": f"AUTH-{request.request_id}",
        "request_id": request.request_id,
        "authorized_at": authorized_at,
        "allowed": allowed,
        "authorized_mode": request.mode if allowed else None,
        "authorized_financial_exposure_usd": "0.00",
        "reasons": reasons,
        "governor_policy": "zero_exposure_v1",
    }


def build_execution_receipt(request: ExecutionRequest, capability: Mapping[str, Any], governor: Mapping[str, Any], processed_at: str) -> Mapping[str, Any]:
    authorization = authorize_execution(request, capability, governor, processed_at)
    allowed = bool(authorization["allowed"])
    result = {
        "schema_version": 1,
        "result_id": f"RESULT-{request.request_id}",
        "request_id": request.request_id,
        "processed_at": processed_at,
        "status": "SHADOW_RECORDED" if allowed and request.mode == "SHADOW" else "SIMULATION_ACCEPTED" if allowed else "DENIED",
        "venue_adapter": "none_pre_live",
        "venue_order_id": None,
        "fills": [],
        "observed_venue_truth": False,
        "capital_moved": False,
    }
    accounting = {
        "schema_version": 1,
        "accounting_receipt_id": f"ACCT-{request.request_id}",
        "request_id": request.request_id,
        "reconciliation_state": "NO_EXTERNAL_EFFECT",
        "orders_recorded": 0,
        "fills_recorded": 0,
        "fees_realized_usd": "0.00",
        "pnl_realized_usd": "0.00",
        "financial_exposure_usd": "0.00",
        "external_truth_available": False,
    }
    request_dict = request.to_dict()
    return {
        "schema_version": 1,
        "receipt_id": f"RECEIPT-{request.request_id}",
        "request_hash": canonical_hash(request_dict),
        "request": request_dict,
        "risk_authorization": authorization,
        "execution_result": result,
        "accounting": accounting,
        "integrity": {"algorithm": "sha256", "content_hash": canonical_hash({"request": request_dict, "authorization": authorization, "result": result, "accounting": accounting})},
    }


class ReceiptStore:
    """One immutable receipt per request; retry is idempotent and drift fails closed."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def persist(self, receipt: Mapping[str, Any]) -> Mapping[str, Any]:
        request_id = str(receipt.get("request", {}).get("request_id", ""))
        if not request_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in request_id):
            raise ValueError("unsafe request_id")
        directory = self.root / "receipts" / "execution"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{request_id}.json"
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if existing.get("request_hash") != receipt.get("request_hash"):
                raise RuntimeError("idempotency conflict: request_id already has different content")
            return existing
        descriptor, temporary = tempfile.mkstemp(prefix=f".{request_id}.", suffix=".tmp", dir=str(directory))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(dict(receipt), handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return receipt


def validate_execution_receipts(root: Path) -> list[str]:
    """Validate immutable receipts, hashes, zero-exposure invariants, and secret boundary."""
    errors: list[str] = []
    directory = root / "receipts/execution"
    if not directory.exists():
        return errors

    def inspect_secrets(value: Any, location: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in SECRET_FIELDS:
                    errors.append(f"{location}: forbidden secret-bearing field {key!r}")
                inspect_secrets(child, f"{location}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                inspect_secrets(child, f"{location}[{index}]")

    for path in sorted(directory.glob("*.json")):
        relative = path.relative_to(root).as_posix()
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{relative}: unreadable receipt: {exc}")
            continue
        inspect_secrets(receipt, relative)
        request = receipt.get("request", {})
        authorization = receipt.get("risk_authorization", {})
        result = receipt.get("execution_result", {})
        accounting = receipt.get("accounting", {})
        request_id = request.get("request_id")
        if path.stem != request_id:
            errors.append(f"{relative}: filename must equal request_id")
        if canonical_hash(request) != receipt.get("request_hash"):
            errors.append(f"{relative}: request hash mismatch")
        content = {"request": request, "authorization": authorization, "result": result, "accounting": accounting}
        if canonical_hash(content) != receipt.get("integrity", {}).get("content_hash"):
            errors.append(f"{relative}: content hash mismatch")
        if result.get("capital_moved") is not False:
            errors.append(f"{relative}: capital_moved must be false pre-live")
        if authorization.get("authorized_financial_exposure_usd") != "0.00":
            errors.append(f"{relative}: authorization exposure must be 0.00")
        if accounting.get("financial_exposure_usd") != "0.00":
            errors.append(f"{relative}: accounting exposure must be 0.00")
        if request.get("mode") == "LIVE" and authorization.get("allowed") is not False:
            errors.append(f"{relative}: LIVE authorization must be denied")
        if accounting.get("pnl_realized_usd") != "0.00" or accounting.get("fees_realized_usd") != "0.00":
            errors.append(f"{relative}: pre-live realized economics must be 0.00")
    return errors
