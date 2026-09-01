"""Restartable canonical shadow economic lifecycle using the execution contracts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any, Mapping, Protocol

from .market_data import validate_observation
from .operations import ExecutionRequest, ReceiptStore, authorize_execution, canonical_hash


RECONCILIATION_STATES = {"NOT_APPLICABLE", "NO_EXTERNAL_TRUTH", "MATCHED", "DIVERGED", "ERROR"}


def _atomic(path: Path, document: Mapping[str, Any]) -> None:
    # Use the market-data atomic writer's semantics without sharing a state path.
    from .market_data import _atomic_json
    _atomic_json(path, document)


def _d(value: Any) -> Decimal:
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError("decimal value must be finite")
    return parsed


def _s(value: Decimal) -> str:
    return format(value, "f")


@dataclass(frozen=True)
class TypedDecision:
    decision_id: str
    capability_id: str
    market_observation_id: str
    decided_at: str
    action: str
    quantity: str
    order_type: str
    limit_price: str | None
    rationale_code: str
    schema_version: int = 1

    def validate(self) -> None:
        if self.action not in {"BUY", "SELL", "HOLD"}:
            raise ValueError("invalid decision action")
        if self.action == "HOLD":
            raise ValueError("economic lifecycle qualification requires BUY or SELL")
        if _d(self.quantity) <= 0:
            raise ValueError("decision quantity must be positive")
        for value in (self.decision_id, self.capability_id, self.market_observation_id, self.decided_at, self.rationale_code):
            if not str(value).strip():
                raise ValueError("decision lineage fields are required")


@dataclass(frozen=True)
class ExecutionAssumptions:
    assumption_id: str
    fee_bps: str
    half_spread_bps: str
    slippage_bps: str
    latency_ms: int
    latency_price_penalty_bps: str
    fill_ratio: str
    quantity_step: str
    price_step: str
    minimum_notional: str
    available_capacity: str
    schema_version: int = 1

    def validate(self) -> None:
        for field in ("fee_bps", "half_spread_bps", "slippage_bps", "latency_price_penalty_bps", "minimum_notional", "available_capacity"):
            if _d(getattr(self, field)) < 0:
                raise ValueError(f"{field} must be non-negative")
        for field in ("quantity_step", "price_step"):
            if _d(getattr(self, field)) <= 0:
                raise ValueError(f"{field} must be positive")
        if not Decimal("0") <= _d(self.fill_ratio) <= Decimal("1"):
            raise ValueError("fill_ratio must be between zero and one")
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")


class ExecutionAdapter(Protocol):
    name: str

    def execute(self, request: ExecutionRequest, observation: Mapping[str, Any], assumptions: ExecutionAssumptions, processed_at: str) -> Mapping[str, Any]: ...


class DeterministicShadowAdapter:
    """Explicit configured assumptions over observed reference facts; never random."""

    name = "deterministic_shadow_v1"

    def execute(self, request: ExecutionRequest, observation: Mapping[str, Any], assumptions: ExecutionAssumptions, processed_at: str) -> Mapping[str, Any]:
        request.validate()
        assumptions.validate()
        if validate_observation(observation):
            raise ValueError("invalid market observation")
        if observation.get("quality", {}).get("status") != "VALID":
            return self._rejected(request, processed_at, "market_data_not_valid")
        reference = _d(observation["normalized"]["close"])
        requested = _d(request.quantity)
        quantity_step = _d(assumptions.quantity_step)
        capacity = _d(assumptions.available_capacity)
        fill_ratio = _d(assumptions.fill_ratio)
        fill_qty = min(requested * fill_ratio, capacity)
        fill_qty = (fill_qty / quantity_step).to_integral_value(rounding=ROUND_DOWN) * quantity_step
        direction = Decimal("1") if request.side == "BUY" else Decimal("-1")
        configured_penalty = _d(assumptions.half_spread_bps) + _d(assumptions.slippage_bps) + _d(assumptions.latency_price_penalty_bps)
        modeled_price = reference * (Decimal("1") + direction * configured_penalty / Decimal("10000"))
        price_step = _d(assumptions.price_step)
        modeled_price = (modeled_price / price_step).to_integral_value(rounding=ROUND_DOWN) * price_step
        if request.order_type == "LIMIT" and request.limit_price is not None:
            limit = _d(request.limit_price)
            if request.side == "BUY" and modeled_price > limit:
                return self._rejected(request, processed_at, "limit_not_marketable")
            if request.side == "SELL" and modeled_price < limit:
                return self._rejected(request, processed_at, "limit_not_marketable")
        notional = fill_qty * modeled_price
        if fill_qty <= 0:
            return self._rejected(request, processed_at, "quantity_below_precision_or_capacity")
        if notional < _d(assumptions.minimum_notional):
            return self._rejected(request, processed_at, "minimum_notional_not_met")
        fee = notional * _d(assumptions.fee_bps) / Decimal("10000")
        fill = {
            "fill_id": f"FILL-{request.request_id}-1", "request_id": request.request_id,
            "instrument": request.instrument, "side": request.side, "quantity": _s(fill_qty),
            "price": _s(modeled_price), "notional": _s(notional), "fee_usd": _s(fee),
            "filled_at": processed_at, "truth_class": "MODELED", "source": self.name,
        }
        return {
            "schema_version": 1, "result_id": f"RESULT-{request.request_id}",
            "request_id": request.request_id, "processed_at": processed_at,
            "status": "FILLED" if fill_qty == requested else "PARTIALLY_FILLED",
            "adapter": self.name, "fills": [fill], "capital_moved": False,
            "observed_venue_truth": False, "modeled_execution": True,
            "facts": {"truth_class": "OBSERVED", "reference_price": _s(reference), "market_observation_id": observation["observation_id"]},
            "configured_assumptions": {**asdict(assumptions), "truth_class": "CONFIGURED"},
            "modeled": {"truth_class": "MODELED", "configured_price_penalty_bps": _s(configured_penalty), "latency_ms": assumptions.latency_ms},
        }

    def _rejected(self, request: ExecutionRequest, processed_at: str, reason: str) -> Mapping[str, Any]:
        return {"schema_version": 1, "result_id": f"RESULT-{request.request_id}", "request_id": request.request_id, "processed_at": processed_at, "status": "REJECTED", "adapter": self.name, "fills": [], "capital_moved": False, "observed_venue_truth": False, "modeled_execution": True, "reason": reason}


def build_accounting(request: ExecutionRequest, result: Mapping[str, Any], mark_price: str, accounted_at: str) -> Mapping[str, Any]:
    events = [{"event_id": f"EVT-{request.request_id}-INTENT", "type": "EXECUTION_INTENT", "at": request.created_at, "request_id": request.request_id}]
    fills = result.get("fills", [])
    signed_qty = Decimal("0")
    total_cost = Decimal("0")
    total_fee = Decimal("0")
    for index, fill in enumerate(fills, start=1):
        quantity = _d(fill["quantity"])
        price = _d(fill["price"])
        fee = _d(fill["fee_usd"])
        direction = Decimal("1") if fill["side"] == "BUY" else Decimal("-1")
        signed_qty += direction * quantity
        total_cost += direction * quantity * price
        total_fee += fee
        events.append({"event_id": f"EVT-{request.request_id}-FILL-{index}", "type": "MODELED_FILL", "at": fill["filled_at"], "fill": fill})
        events.append({"event_id": f"EVT-{request.request_id}-FEE-{index}", "type": "MODELED_FEE", "at": fill["filled_at"], "amount_usd": _s(fee), "fill_id": fill["fill_id"]})
    average_entry = abs(total_cost / signed_qty) if signed_qty else Decimal("0")
    mark = _d(mark_price)
    unrealized = signed_qty * (mark - average_entry) - total_fee if signed_qty else -total_fee
    position = {"instrument": request.instrument, "quantity": _s(signed_qty), "average_entry_price": _s(average_entry), "mark_price": _s(mark), "unrealized_pnl_usd": _s(unrealized), "realized_pnl_usd": "0.00", "truth_class": "MODELED"}
    events.append({"event_id": f"EVT-{request.request_id}-POSITION", "type": "POSITION_PROJECTION", "at": accounted_at, "position": position})
    event_fill_qty = sum((_d(item["fill"]["quantity"]) for item in events if item["type"] == "MODELED_FILL"), Decimal("0"))
    result_fill_qty = sum((_d(item["quantity"]) for item in fills), Decimal("0"))
    event_fee = sum((_d(item["amount_usd"]) for item in events if item["type"] == "MODELED_FEE"), Decimal("0"))
    result_fee = sum((_d(item["fee_usd"]) for item in fills), Decimal("0"))
    matched = event_fill_qty == result_fill_qty and event_fee == result_fee
    return {
        "schema_version": 1, "accounting_receipt_id": f"ACCT-{request.request_id}",
        "request_id": request.request_id, "events": events, "position_projection": position,
        "fees_realized_usd": "0.00", "pnl_realized_usd": "0.00", "financial_exposure_usd": "0.00",
        "shadow_modeled_fee_usd": _s(total_fee), "shadow_modeled_unrealized_pnl_usd": _s(unrealized),
        "comparison_performed": True, "truth_source": "DECLARED_SHADOW_FILL_EVENTS",
        "reconciliation_state": "MATCHED" if matched else "DIVERGED",
        "discrepancy_count": 0 if matched else 1,
        "external_truth_available": False,
    }


class ShadowLifecycle:
    STAGES = ("PREPARED", "AUTHORIZED", "EXECUTED", "ACCOUNTED", "FINALIZED")

    def __init__(self, root: Path, adapter: ExecutionAdapter | None = None):
        self.root = root.resolve()
        self.adapter = adapter or DeterministicShadowAdapter()
        self.journal_dir = self.root / "runtime/shadow_operations"

    def run(self, *, decision: TypedDecision, observation: Mapping[str, Any], capability: Mapping[str, Any], governor: Mapping[str, Any], assumptions: ExecutionAssumptions, processed_at: str, fail_after_stage: str | None = None) -> Mapping[str, Any]:
        decision.validate()
        if decision.market_observation_id != observation.get("observation_id"):
            raise ValueError("decision/observation lineage mismatch")
        if observation.get("quality", {}).get("status") != "VALID":
            raise ValueError("stale, degraded, or unavailable observation is not actionable")
        request = ExecutionRequest(
            request_id=f"REQ-{decision.decision_id}", idempotency_key=f"IDEMP-{decision.decision_id}",
            capability_id=decision.capability_id, decision_id=decision.decision_id,
            market_observation_id=decision.market_observation_id, mode="SHADOW",
            instrument=observation["normalized"]["instrument"], side=decision.action,
            order_type=decision.order_type, quantity=decision.quantity, limit_price=decision.limit_price,
            created_at=decision.decided_at, capital_effect="NONE",
        )
        journal_path = self.journal_dir / f"{request.request_id}.json"
        if journal_path.exists():
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            if journal.get("input_hash") != canonical_hash({"decision": asdict(decision), "observation_hash": observation["integrity"]["content_hash"], "assumptions": asdict(assumptions)}):
                raise RuntimeError("operation idempotency conflict")
        else:
            journal = {"schema_version": 1, "operation_id": request.request_id, "stage": "PREPARED", "input_hash": canonical_hash({"decision": asdict(decision), "observation_hash": observation["integrity"]["content_hash"], "assumptions": asdict(assumptions)}), "decision": asdict(decision), "request": request.to_dict(), "observation_path": None, "assumptions": asdict(assumptions)}
            _atomic(journal_path, journal)
        self._fault(journal["stage"], fail_after_stage)
        if journal["stage"] == "PREPARED":
            journal["authorization"] = authorize_execution(request, capability, governor, processed_at)
            journal["stage"] = "AUTHORIZED"
            _atomic(journal_path, journal)
            self._fault("AUTHORIZED", fail_after_stage)
        if not journal["authorization"]["allowed"]:
            raise PermissionError("execution denied: " + ",".join(journal["authorization"]["reasons"]))
        if journal["stage"] == "AUTHORIZED":
            journal["result"] = self.adapter.execute(request, observation, assumptions, processed_at)
            journal["stage"] = "EXECUTED"
            _atomic(journal_path, journal)
            self._fault("EXECUTED", fail_after_stage)
        if journal["stage"] == "EXECUTED":
            journal["accounting"] = build_accounting(request, journal["result"], observation["normalized"]["close"], processed_at)
            journal["stage"] = "ACCOUNTED"
            _atomic(journal_path, journal)
            self._fault("ACCOUNTED", fail_after_stage)
        if journal["stage"] == "ACCOUNTED":
            content = {"request": request.to_dict(), "authorization": journal["authorization"], "result": journal["result"], "accounting": journal["accounting"]}
            receipt = {"schema_version": 1, "receipt_id": f"RECEIPT-{request.request_id}", "request_hash": canonical_hash(request.to_dict()), "decision": asdict(decision), "market_observation": {"observation_id": observation["observation_id"], "content_hash": observation["integrity"]["content_hash"], "quality": observation["quality"]}, "request": request.to_dict(), "risk_authorization": journal["authorization"], "execution_result": journal["result"], "accounting": journal["accounting"], "integrity": {"algorithm": "sha256", "content_hash": canonical_hash(content)}}
            ReceiptStore(self.root).persist(receipt)
            journal["receipt_path"] = f"receipts/execution/{request.request_id}.json"
            journal["stage"] = "FINALIZED"
            _atomic(journal_path, journal)
            self._fault("FINALIZED", fail_after_stage)
        return json.loads((self.root / journal["receipt_path"]).read_text(encoding="utf-8"))

    @staticmethod
    def _fault(stage: str, requested: str | None) -> None:
        if requested == stage:
            raise RuntimeError(f"injected failure after {stage}")


def validate_shadow_runtime(root: Path) -> list[str]:
    errors: list[str] = []
    directory = root / "runtime/shadow_operations"
    if not directory.exists():
        return errors
    for path in sorted(directory.glob("*.json")):
        relative = path.relative_to(root).as_posix()
        try:
            journal = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{relative}: corrupt shadow operation journal: {exc}")
            continue
        if journal.get("stage") not in ShadowLifecycle.STAGES:
            errors.append(f"{relative}: invalid operation stage")
        # Version-1 journals store only the aggregate input hash. Final receipt
        # integrity remains the authoritative content proof.
        if not journal.get("input_hash"):
            errors.append(f"{relative}: missing input hash")
        if journal.get("stage") == "FINALIZED":
            receipt_path = root / str(journal.get("receipt_path", ""))
            if not receipt_path.is_file():
                errors.append(f"{relative}: finalized operation receipt is missing")
    replay_dir = root / "runtime/replays"
    if replay_dir.exists():
        for path in sorted(replay_dir.glob("*.json")):
            relative = path.relative_to(root).as_posix()
            try:
                replay = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"{relative}: corrupt replay checkpoint: {exc}")
                continue
            ordered = replay.get("ordered_observation_ids", [])
            processed = replay.get("processed_observation_ids", [])
            if len(ordered) != len(set(ordered)) or not set(processed).issubset(set(ordered)):
                errors.append(f"{relative}: replay duplicate/order lineage invalid")
            if replay.get("status") == "COMPLETE" and processed != ordered:
                errors.append(f"{relative}: complete replay has unprocessed observations")
            for receipt in replay.get("receipt_paths", []):
                if not (root / str(receipt)).is_file():
                    errors.append(f"{relative}: replay receipt missing: {receipt}")
    return errors
