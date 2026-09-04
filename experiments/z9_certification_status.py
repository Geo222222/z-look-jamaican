from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Tuple

from autonomous_kernel.assembly.contextual_journal import validate_contextual_assembly_journal
from autonomous_kernel.assembly.contextual_lineage import validate_contextual_assembly_lineage
from autonomous_kernel.context.store import validate_market_context_store


POLICY_REF = "artifacts/evidence/market/z9-certification-policy-v1.json"
Z8_INVENTORY_REF = "artifacts/evidence/market/z8-certification-inventory-20260903.json"
REQUIRED_CODE = ("autonomous_kernel/context/contracts.py", "autonomous_kernel/context/builder.py", "autonomous_kernel/context/store.py", "autonomous_kernel/assembly/contextual.py", "autonomous_kernel/assembly/contextual_journal.py", "autonomous_kernel/assembly/contextual_lineage.py", "autonomous_kernel/assembly/contextual_service.py")


class Z9CertificationStatusError(RuntimeError):
    pass


def _load(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise Z9CertificationStatusError("required Z9 artifact missing: %s" % path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise Z9CertificationStatusError("expected JSON object: %s" % path)
    return value


def _bucket(ns: int) -> Tuple[str, str]:
    moment = datetime.fromtimestamp(int(ns) / 1_000_000_000, timezone.utc)
    start = (moment.hour // 6) * 6
    return moment.date().isoformat(), "%02d:00-%02d:59" % (start, start + 5)


def z9_certification_status(root: Path) -> Mapping[str, Any]:
    root = root.resolve(); policy = _load(root / POLICY_REF); z8 = _load(root / Z8_INVENTORY_REF)
    if policy.get("policy_id") != "Z9-CERTIFICATION-POLICY-V1" or policy.get("status") != "FROZEN_BEFORE_EMPIRICAL_Z9_RESULT":
        raise Z9CertificationStatusError("Z9 certification policy identity/status drifted")
    missing = [path for path in REQUIRED_CODE if not (root / path).is_file()]
    context_errors = validate_market_context_store(root); journal_errors = validate_contextual_assembly_journal(root); lineage_errors = validate_contextual_assembly_lineage(root)
    construction_errors = missing + context_errors + journal_errors + lineage_errors
    index = _load(root / "state/market_context.json"); items = list(index.get("items", [])); qualified = [item for item in items if item.get("status") == "QUALIFIED"]
    dates = set(); buckets = set(); qualified_market = []; derivative_relationship_contexts = []
    for item in qualified:
        date, bucket = _bucket(int(item.get("cutoff_at_ns", 0))); dates.add(date); buckets.add(bucket)
        path = root / str(item.get("path", ""))
        if not path.is_file():
            continue
        document = _load(path); context = document.get("context", {}); state = context.get("state", {}) if isinstance(context, Mapping) else {}; market = state.get("market", {}) if isinstance(state, Mapping) else {}; derivatives = state.get("derivatives", {}) if isinstance(state, Mapping) else {}
        if int(market.get("qualified_spot_count", 0)) >= int(policy["market_wide_historical"]["minimum_qualified_spot_instruments_per_context"]):
            qualified_market.append(item)
        if int(derivatives.get("relationship_count", 0)) > 0:
            derivative_relationship_contexts.append(item)
    market_policy = policy["market_wide_historical"]
    market_ready = len(qualified_market) >= int(market_policy["minimum_qualified_contexts"]) and len(dates) >= int(market_policy["minimum_distinct_utc_dates"]) and len(buckets) >= int(market_policy["minimum_distinct_utc_six_hour_buckets"])
    derivative_policy = policy["spot_derivatives"]
    derivative_dates = {_bucket(int(item.get("cutoff_at_ns", 0)))[0] for item in derivative_relationship_contexts}; derivative_buckets = {_bucket(int(item.get("cutoff_at_ns", 0)))[1] for item in derivative_relationship_contexts}
    derivative_ready = len(derivative_relationship_contexts) >= int(derivative_policy["minimum_qualified_relationship_contexts"]) and len(derivative_dates) >= int(derivative_policy["minimum_distinct_utc_dates"]) and len(derivative_buckets) >= int(derivative_policy["minimum_distinct_utc_six_hour_buckets"])
    z8_progress = z8.get("broad_historical_progress", {}); z8_broad_met = z8_progress.get("status") != "DATA_BLOCKED" and bool(z8_progress.get("performance_scoring_allowed_yet")); contextual_ready = bool(z8_broad_met and market_ready)
    return {
        "schema_version": 1, "policy_id": policy["policy_id"],
        "construction": {"status": "CONSTRUCTED" if not construction_errors else "INVALID", "errors": construction_errors, "base_z8_rewritten": False},
        "market_wide_historical": {"status": "READY_FOR_PREREGISTERED_SCORING" if market_ready else "DATA_BLOCKED", "qualified_contexts": len(qualified_market), "required_qualified_contexts": int(market_policy["minimum_qualified_contexts"]), "distinct_utc_dates": len(dates), "required_distinct_utc_dates": int(market_policy["minimum_distinct_utc_dates"]), "distinct_utc_six_hour_buckets": len(buckets), "required_distinct_utc_six_hour_buckets": int(market_policy["minimum_distinct_utc_six_hour_buckets"])},
        "spot_derivatives": {"status": "READY_FOR_PREREGISTERED_SCORING" if derivative_ready else "DATA_BLOCKED", "qualified_relationship_contexts": len(derivative_relationship_contexts), "required_qualified_relationship_contexts": int(derivative_policy["minimum_qualified_relationship_contexts"]), "distinct_utc_dates": len(derivative_dates), "distinct_utc_six_hour_buckets": len(derivative_buckets)},
        "contextual_assembly": {"status": "READY_FOR_PREREGISTERED_WALK_FORWARD" if contextual_ready else "DATA_BLOCKED", "z8_broad_prerequisite_met": z8_broad_met, "z9_market_wide_prerequisite_met": market_ready, "required_resolved_predictions": int(policy["contextual_assembly"]["minimum_resolved_contextual_predictions"]), "required_chronological_folds": int(policy["contextual_assembly"]["minimum_chronological_folds"])},
        "capital_effect": "NONE", "live_execution": False
    }


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Z9 construction and empirical certification readiness"); parser.add_argument("--root", type=Path, default=Path.cwd()); parser.add_argument("--output", type=Path); args = parser.parse_args(list(argv) if argv is not None else None)
    result = z9_certification_status(args.root); text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(text, encoding="utf-8")
    print(text, end=""); return 0


if __name__ == "__main__":
    raise SystemExit(main())
