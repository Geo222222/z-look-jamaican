from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .store import validate_market_context_store


def market_context_status(root: Path) -> Mapping[str, Any]:
    root = root.resolve()
    errors = validate_market_context_store(root)
    index_path = root / "state/market_context.json"
    if not index_path.is_file():
        return {"status": "INVALID", "errors": errors, "context_count": 0, "latest": None}
    index = json.loads(index_path.read_text(encoding="utf-8"))
    items = list(index.get("items", []))
    ordered = sorted(items, key=lambda item: (int(item.get("cutoff_at_ns", -1)), int(item.get("known_at_ns", -1)), str(item.get("context_id", ""))))
    counts = {"QUALIFIED": 0, "DEGRADED": 0, "UNAVAILABLE": 0}
    for item in items:
        key = str(item.get("status", "UNAVAILABLE"))
        counts[key] = counts.get(key, 0) + 1
    return {"status": "OK" if not errors else "INVALID", "errors": errors, "context_count": len(items), "status_counts": counts, "latest": ordered[-1] if ordered else None, "authority": "Z9 market perception only; no Benjamin, Watchman, or Hand authority"}
