"""Portfolio constraint evidence; never inferred by the market story."""

from typing import Any, Mapping

from ..core import build_object


def portfolio_observation(
    *, object_id: str, instrument: str, exchange: str, asset: str, timestamp: str,
    current_exposure_usd: str, available_risk_budget_usd: str, strategy_exposure_allowed: bool,
    source_record_id: str, source_sha256: str, created_at: str,
) -> Mapping[str, Any]:
    return build_object(
        object_id=object_id, object_type="PORTFOLIO_OBSERVATION", truth_class="OBSERVED_EVIDENCE",
        subject={"instrument": instrument, "exchange": exchange, "asset": asset}, effective_at=timestamp,
        created_at=created_at, source_time_range={"start": timestamp, "end": timestamp}, input_refs=[],
        method={"name": "PORTFOLIO_STATE_PRESERVATION", "version": "1.0.0", "deterministic": True},
        quality={"status": "VALID", "source_record_id": source_record_id, "source_sha256": source_sha256},
        payload={"current_exposure_usd": str(current_exposure_usd), "available_risk_budget_usd": str(available_risk_budget_usd), "strategy_exposure_allowed": bool(strategy_exposure_allowed), "source_record_id": source_record_id},
    )
