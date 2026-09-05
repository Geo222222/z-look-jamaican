from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomous_kernel.learning.direction_loop import _observation_bounds, process_canonical_direction_batches
from autonomous_kernel.learning.liquidity_loop import process_canonical_liquidity_batches
from autonomous_kernel.learning.magnitude_loop import process_canonical_magnitude_batches
from autonomous_kernel.learning.volatility_loop import _cutoffs, process_canonical_volatility_batches
from autonomous_kernel.synthesis.service import market_synthesis_projection


REPO = Path(__file__).resolve().parents[1]
REAL_VOLATILITY_OPT_IN_ENV = "ZLOOK_RUN_REAL_VOLATILITY_TEST"
EVIDENCE_ROOT_ENV = "ZLOOK_REAL_EVIDENCE_ROOT"
CHECKOUT_GUARD_PATHS = (
    "runtime/prediction-competence-proof",
    "memory/question_predictions.jsonl",
    "memory/question_outcomes.jsonl",
    "memory/expert_intelligence.jsonl",
    "state/question_prediction_journal.json",
    "state/question_outcome_journal.json",
    "state/expert_intelligence.json",
)


def _opt_in_requested() -> bool:
    return os.environ.get(REAL_VOLATILITY_OPT_IN_ENV, "").strip() == "1"


def _evidence_root() -> Path:
    raw = os.environ.get(EVIDENCE_ROOT_ENV, "").strip()
    return Path(raw).resolve() if raw else REPO


def _local_coinbase_manifests(root: Path) -> list:
    directory = Path(root) / "artifacts/market_data/canonical"
    if not directory.is_dir():
        return []
    return sorted(directory.glob("CAN-COINBASE-BTC-USD-OBS-*.manifest.json"))


def require_opt_in_local_coinbase_captures(root: Path) -> list:
    if not _opt_in_requested():
        raise unittest.SkipTest(
            "real Coinbase Volatility proof is opt-in; set %s=1 with local CAN-COINBASE-BTC-USD-OBS-* captures"
            % REAL_VOLATILITY_OPT_IN_ENV
        )
    manifests = _local_coinbase_manifests(root)
    if not manifests:
        raise AssertionError(
            "%s=1 but no artifacts/market_data/canonical/CAN-COINBASE-BTC-USD-OBS-*.manifest.json captures are present"
            % REAL_VOLATILITY_OPT_IN_ENV
        )
    return manifests


def _copy_canonical(src_root: Path, dest_root: Path, batch_id: str) -> None:
    relative_dir = Path("artifacts/market_data/canonical")
    (dest_root / relative_dir).mkdir(parents=True, exist_ok=True)
    for suffix in (".jsonl.gz", ".manifest.json"):
        src = src_root / relative_dir / (batch_id + suffix)
        if not src.is_file():
            raise FileNotFoundError(src)
        shutil.copy2(src, dest_root / relative_dir / (batch_id + suffix))


def _checkout_guard(root: Path) -> tuple:
    records = []
    for relative in CHECKOUT_GUARD_PATHS:
        path = Path(root) / relative
        if not path.exists():
            records.append((relative, None))
            continue
        if path.is_file():
            stat = path.stat()
            records.append((relative, stat.st_size, stat.st_mtime_ns))
            continue
        for child in sorted(path.rglob("*")):
            if not child.is_file():
                continue
            stat = child.stat()
            records.append((str(child.relative_to(root)).replace("\\", "/"), stat.st_size, stat.st_mtime_ns))
    return tuple(records)


class RealCoinbaseVolatilityGateTests(unittest.TestCase):
    def test_skips_without_opt_in_and_does_not_mutate_checkout(self):
        before = _checkout_guard(REPO)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(REAL_VOLATILITY_OPT_IN_ENV, None)
            with self.assertRaises(unittest.SkipTest):
                require_opt_in_local_coinbase_captures(REPO)
        self.assertEqual(before, _checkout_guard(REPO))

    def test_opt_in_without_local_captures_fails_clearly(self):
        before = _checkout_guard(REPO)
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory)
            with patch.dict(os.environ, {REAL_VOLATILITY_OPT_IN_ENV: "1"}):
                with self.assertRaisesRegex(AssertionError, r"%s=1 but no .*captures are present" % REAL_VOLATILITY_OPT_IN_ENV):
                    require_opt_in_local_coinbase_captures(empty)
        self.assertEqual(before, _checkout_guard(REPO))


class RealCoinbaseVolatilityLoopTests(unittest.TestCase):
    def test_public_coinbase_batches_complete_volatility_school_loop(self):
        before = _checkout_guard(REPO)
        evidence = _evidence_root()
        manifests = require_opt_in_local_coinbase_captures(evidence)
        batch_ids = [path.name[: -len(".manifest.json")] for path in manifests]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for batch_id in batch_ids:
                _copy_canonical(evidence, root, batch_id)
            usable = []
            for batch_id in batch_ids:
                try:
                    start_ns, end_ns = _observation_bounds(root, batch_id)
                except Exception:
                    continue
                if _cutoffs(start_ns, end_ns):
                    usable.append(batch_id)
            if not usable:
                raise AssertionError("%s=1 but copied captures are too short for a frozen 60s Volatility cutoff" % REAL_VOLATILITY_OPT_IN_ENV)
            direction = process_canonical_direction_batches(root, tuple(usable), sync=True)
            liquidity = process_canonical_liquidity_batches(root, tuple(usable), sync=True)
            magnitude = process_canonical_magnitude_batches(root, tuple(usable), sync=True)
            result = process_canonical_volatility_batches(root, tuple(usable), sync=True)
            self.assertGreaterEqual(result["counts"]["predicted"], 3)
            self.assertGreater(result["counts"]["resolved"] + result["counts"]["unresolvable"], 0)
            self.assertEqual(result["question_ref"], "ECONOMIC_ROOT_VOLATILITY_60S@1.0.0")
            self.assertEqual(result["horizon_ns"], 60_000_000_000)
            self.assertFalse(result["authority"]["capital_allocation"])
            assembly = (result.get("sync") or {}).get("volatility_assembly") or {}
            self.assertIn(assembly.get("status"), {"RESEARCH_ONLY", "BLOCKED"})
            synthesis = market_synthesis_projection(root)
            latest = synthesis.get("latest") if isinstance(synthesis.get("latest"), dict) else {}
            self.assertFalse(latest.get("internal_intelligence_publication") == "PUBLISHED")
            self.assertNotEqual(latest.get("benjamin_publication"), "ELIGIBLE")
            self.assertGreaterEqual(direction["counts"]["predicted"], 0)
            self.assertGreaterEqual(liquidity["counts"]["predicted"], 0)
            self.assertGreaterEqual(magnitude["counts"]["predicted"], 0)
            self.assertFalse((root / "runtime/prediction-competence-proof/result.json").is_file())
        self.assertEqual(
            before,
            _checkout_guard(REPO),
            "real Coinbase Volatility test must not write runtime/, memory/, or state/ in the repository checkout",
        )
