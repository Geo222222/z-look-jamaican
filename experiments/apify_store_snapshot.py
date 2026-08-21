"""Capture a reproducible, read-only Apify Store demand snapshot.

The Store endpoint is public and unauthenticated. External fields are treated as
untrusted data: this experiment stores only identifiers, titles, pricing model,
selected numeric telemetry, and source URLs. It performs HTTP GET requests only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


DEFAULT_TERMS = (
    "business entity search",
    "EU TED tenders",
    "SEC filings",
    "CISA KEV",
    "government tenders",
)

API_TEMPLATE = (
    "https://api.apify.com/v2/store?limit={limit}"
    "&includeUnrunnableActors=true&search={term}"
)
COLLECTOR_REVISION = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return float(value)
    return 0


def normalize_item(item: Mapping[str, Any]) -> Dict[str, Any]:
    stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
    pricing = item.get("currentPricingInfo") if isinstance(item.get("currentPricingInfo"), dict) else {}
    run_stats = stats.get("publicActorRunStats30Days")
    if not isinstance(run_stats, dict):
        run_stats = {}
    succeeded = _number(run_stats.get("SUCCEEDED"))
    failed = _number(run_stats.get("FAILED")) + _number(run_stats.get("ABORTED")) + _number(run_stats.get("TIMED-OUT"))
    known_outcomes = succeeded + failed
    runs_30_days = _number(run_stats.get("TOTAL")) or known_outcomes
    success_rate = round(succeeded / known_outcomes, 6) if known_outcomes else None
    return {
        "id": str(item.get("id") or ""),
        "title": str(item.get("title") or item.get("name") or "")[:300],
        "url": str(item.get("url") or "")[:1000],
        "pricing_model": pricing.get("pricingModel"),
        "price_per_unit_usd": pricing.get("pricePerUnitUsd"),
        "users_30_days": int(_number(stats.get("totalUsers30Days"))),
        "runs_30_days": int(runs_30_days),
        "total_runs": int(_number(stats.get("totalRuns"))),
        "review_count": int(_number(stats.get("actorReviewCount"))),
        "rating": stats.get("actorReviewRating"),
        "success_rate_30_days": success_rate,
    }


def summarize(items: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    rows = list(items)
    users = [int(_number(item.get("users_30_days"))) for item in rows]
    paid = [
        item
        for item in rows
        if str(item.get("pricing_model") or "").upper() not in {"", "FREE"}
    ]
    paid_with_five_users = [item for item in paid if int(_number(item.get("users_30_days"))) >= 5]
    return {
        "results_returned": len(rows),
        "aggregate_users_30_days": sum(users),
        "max_users_30_days": max(users) if users else 0,
        "paid_results": len(paid),
        "paid_results_with_at_least_five_users": len(paid_with_five_users),
    }


def fetch_term(term: str, limit: int, timeout_seconds: float) -> Dict[str, Any]:
    url = API_TEMPLATE.format(limit=limit, term=quote_plus(term))
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Z-Look-Opportunity-Experiment/1.0 (read-only)",
        },
        method="GET",
    )
    retrieved_at = utc_now()
    with urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read()
        status = response.status
    payload = json.loads(raw.decode("utf-8"))
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError(f"unexpected Apify Store response shape for term {term!r}")
    items = [normalize_item(item) for item in data["items"] if isinstance(item, dict)]
    return {
        "term": term,
        "source_url": url,
        "retrieved_at": retrieved_at,
        "http_status": status,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "external_content_trust": "untrusted_data_only",
        "summary": summarize(items),
        "items": items,
    }


def capture(
    terms: Sequence[str],
    limit: int,
    timeout_seconds: float,
    output: Path,
) -> Dict[str, Any]:
    if not terms:
        raise ValueError("at least one search term is required")
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    started_at = utc_now()
    queries = [fetch_term(term, limit, timeout_seconds) for term in terms]
    document = {
        "schema_version": 1,
        "collector_revision": COLLECTOR_REVISION,
        "experiment_id": "EXP-OPP-001",
        "captured_at": utc_now(),
        "started_at": started_at,
        "source": "Apify public Store API",
        "method": "unauthenticated HTTP GET only",
        "terms": list(terms),
        "limit_per_term": limit,
        "queries": queries,
        "interpretation_warning": "Store search is semantic and noisy. Users and runs are demand proxies, not proof of paid use, revenue, retention, or profit. Manual competitor curation is required before promotion.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture read-only Apify Store telemetry")
    parser.add_argument("--term", action="append", dest="terms", help="search term; repeat for multiple terms")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or Path("artifacts/evidence/apify_store") / f"snapshot-{timestamp}.json"
    try:
        document = capture(args.terms or DEFAULT_TERMS, args.limit, args.timeout_seconds, output)
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1
    result = {
        "status": "success",
        "experiment_id": document["experiment_id"],
        "captured_at": document["captured_at"],
        "output": str(output.resolve()),
        "queries": [
            {"term": query["term"], **query["summary"]}
            for query in document["queries"]
        ],
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
