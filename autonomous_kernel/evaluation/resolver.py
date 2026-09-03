from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Optional, Sequence, Tuple

from ..prediction.contracts import Prediction, PredictionContractError
from ..prediction.factory import PredictionFactoryError, representation_target_price
from ..prediction.journal import PredictionJournal, validate_prediction_journal
from ..representation.contracts import RepresentationFrame
from .contracts import PredictionOutcome, RESOLUTION_POLICY_ID


MAX_RESOLUTION_LAG_NS_V1 = 5_000_000_000


class OutcomeResolutionError(RuntimeError):
    pass


class OutcomePendingError(OutcomeResolutionError):
    pass


def _prediction_from_journal(root: Path, prediction_id: str) -> Tuple[Prediction, str, int]:
    errors = validate_prediction_journal(root)
    if errors:
        raise OutcomeResolutionError("prediction journal invalid: " + "; ".join(errors))
    journal = PredictionJournal(root)
    matches = [entry for entry in journal.entries() if entry.get("prediction", {}).get("prediction_id") == prediction_id]
    if not matches:
        raise OutcomeResolutionError("prediction is not durably journaled")
    if len(matches) != 1:
        raise OutcomeResolutionError("prediction journal contains duplicate prediction_id")
    entry = matches[0]
    try:
        prediction = Prediction.from_wire(entry.get("prediction", {}))
    except (PredictionContractError, ValueError, TypeError) as exc:
        raise OutcomeResolutionError("journaled prediction is invalid: %s" % exc) from exc
    entry_hash = str(entry.get("entry_hash", ""))
    journaled_at_ns = int(entry.get("journaled_at_ns", -1))
    return prediction, entry_hash, journaled_at_ns


def select_resolution_frame(prediction: Prediction, frames: Sequence[RepresentationFrame]) -> Optional[RepresentationFrame]:
    """Select the first independently knowable qualified frame in the fixed v1 window."""
    eligible = []
    upper = prediction.resolves_at_ns + MAX_RESOLUTION_LAG_NS_V1
    for frame in frames:
        if frame.instrument != prediction.instrument:
            continue
        if frame.representation_type != "INSTRUMENT_STATE" or frame.status != "QUALIFIED":
            continue
        if frame.known_at_ns < prediction.resolves_at_ns or frame.known_at_ns > upper:
            continue
        eligible.append(frame)
    if not eligible:
        return None
    eligible.sort(key=lambda item: (item.known_at_ns, item.cutoff_at_ns, item.frame_id, item.content_hash()))
    return eligible[0]


def resolve_prediction(
    root: Path,
    prediction_id: str,
    frames: Sequence[RepresentationFrame],
    *,
    now_at_ns: int,
) -> PredictionOutcome:
    """Resolve one journaled prediction without allowing endpoint cherry-picking.

    A resolution frame is selected solely by the fixed v1 policy. If no frame
    exists and the resolution window is still open, no final outcome is emitted.
    Once the window closes, missing qualified evidence becomes UNRESOLVABLE.

    `decided_at_ns` is the deterministic instant the outcome first became
    knowable, not the later process retry time. This makes retries byte-stable.
    """
    root = root.resolve()
    prediction, entry_hash, journaled_at_ns = _prediction_from_journal(root, prediction_id)
    now = int(now_at_ns)
    if now < 0:
        raise OutcomeResolutionError("now_at_ns must be non-negative")
    if prediction.mode == "PROSPECTIVE_SHADOW" and journaled_at_ns >= prediction.resolves_at_ns:
        raise OutcomeResolutionError("late-journaled prospective prediction cannot be resolved as forward evidence")

    selected = select_resolution_frame(prediction, frames)
    material = "%s|%s|%s" % (prediction.prediction_id, RESOLUTION_POLICY_ID, MAX_RESOLUTION_LAG_NS_V1)
    outcome_id = "OUT-%s" % hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]

    if selected is not None:
        if now < selected.known_at_ns:
            raise OutcomePendingError("selected resolution evidence is not knowable at now_at_ns")
        try:
            realized_price, realized_source = representation_target_price(selected)
        except PredictionFactoryError as exc:
            raise OutcomeResolutionError("selected resolution frame has no canonical target price: %s" % exc) from exc
        reference = Decimal(prediction.reference_price)
        realized = Decimal(realized_price)
        realized_return = (realized / reference - Decimal("1")) * Decimal("10000")
        forecast_error = realized_return - Decimal(prediction.expected_move_bps)
        actual_positive = 1 if realized_return > 0 else 0
        return PredictionOutcome(
            outcome_id=outcome_id,
            prediction_id=prediction.prediction_id,
            prediction_content_hash=prediction.content_hash(),
            prediction_journal_entry_hash=entry_hash,
            evidence_class=prediction.evidence_class,
            target_metric=prediction.target_metric,
            model_refs=prediction.model_refs,
            status="RESOLVED",
            target_resolves_at_ns=prediction.resolves_at_ns,
            max_resolution_lag_ns=MAX_RESOLUTION_LAG_NS_V1,
            resolution_policy_id=RESOLUTION_POLICY_ID,
            decided_at_ns=selected.known_at_ns,
            reference_price=prediction.reference_price,
            reference_price_source=prediction.reference_price_source,
            resolution_frame_id=selected.frame_id,
            resolution_frame_content_hash=selected.content_hash(),
            resolution_known_at_ns=selected.known_at_ns,
            realized_price=realized_price,
            realized_price_source=realized_source,
            realized_return_bps=format(realized_return, "f"),
            forecast_error_bps=format(forecast_error, "f"),
            actual_positive=actual_positive,
        )

    window_closes = prediction.resolves_at_ns + MAX_RESOLUTION_LAG_NS_V1
    if now <= window_closes:
        raise OutcomePendingError("resolution window remains open and no eligible qualified frame exists")

    return PredictionOutcome(
        outcome_id=outcome_id,
        prediction_id=prediction.prediction_id,
        prediction_content_hash=prediction.content_hash(),
        prediction_journal_entry_hash=entry_hash,
        evidence_class=prediction.evidence_class,
        target_metric=prediction.target_metric,
        model_refs=prediction.model_refs,
        status="UNRESOLVABLE",
        target_resolves_at_ns=prediction.resolves_at_ns,
        max_resolution_lag_ns=MAX_RESOLUTION_LAG_NS_V1,
        resolution_policy_id=RESOLUTION_POLICY_ID,
        decided_at_ns=window_closes + 1,
        reference_price=prediction.reference_price,
        reference_price_source=prediction.reference_price_source,
        resolution_frame_id=None,
        resolution_frame_content_hash=None,
        resolution_known_at_ns=None,
        realized_price=None,
        realized_price_source=None,
        realized_return_bps=None,
        forecast_error_bps=None,
        actual_positive=None,
    )
