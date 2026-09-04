from __future__ import annotations

import unittest

from autonomous_kernel.context.contracts import MarketContextFrame
from autonomous_kernel.observation.instruments import CanonicalInstrument
from autonomous_kernel.representation.contracts import RepresentationFrame
from autonomous_kernel.research import (
    ResearchContractError,
    assess_promotion_evidence,
    build_experiment_contract,
    build_model_artifact_lineage,
    build_point_in_time_dataset_manifest,
    build_training_row,
    build_walk_forward_plan,
    extract_context_features,
    extract_instrument_features,
    validate_dataset_manifest,
    validate_walk_forward_plan,
)


SECOND = 1_000_000_000
QUESTION = "ECONOMIC_ROOT_DIRECTION_10S@1.0.0"
QUESTION_HASH = "a" * 64


def _instrument():
    return CanonicalInstrument(
        canonical_id="ASSET.BTC",
        asset_class="CRYPTO",
        market_type="SPOT",
        base_asset="BTC",
        quote_asset="USD",
        settlement_asset=None,
        expiry=None,
    )


def _frame(cutoff):
    state = {
        "venue_states": {
            "coinbase": {
                "book": {
                    "status": "QUALIFIED",
                    "spread_bps": "1.5",
                    "depth_bands_bps": {
                        "10": {
                            "quote_notional_imbalance": "0.25",
                            "bid_quote_notional": "1000",
                            "ask_quote_notional": "800",
                        }
                    },
                }
            }
        },
        "aggregate": {
            "cross_venue_spread_bps": "1.5",
            "venue_midpoint_dispersion_bps": "0",
            "qualified_book_venue_count": 1,
            "venue_count": 1,
            "trade_flow": {
                "reported_buy_quote_notional": "600",
                "reported_sell_quote_notional": "400",
                "trade_count": 5,
            },
        },
    }
    return RepresentationFrame(
        frame_id="REP-%d" % cutoff,
        representation_type="INSTRUMENT_STATE",
        instrument=_instrument(),
        window_start_ns=cutoff - SECOND,
        cutoff_at_ns=cutoff,
        known_at_ns=cutoff - 1,
        latest_source_event_at_ns=cutoff - 2,
        status="QUALIFIED",
        builder_version="instrument-state-v1",
        parameters={},
        state=state,
        source_observation_ids=("OBS-%d" % cutoff,),
        source_content_hashes=("b" * 64,),
        source_providers=("coinbase",),
        source_venues=("coinbase",),
    )


def _context(cutoff, frame):
    return MarketContextFrame(
        context_id="CTX-%d" % cutoff,
        context_type="MARKET_CONTEXT",
        cutoff_at_ns=cutoff,
        known_at_ns=cutoff - 1,
        status="QUALIFIED",
        builder_version="context-v1",
        parameters={},
        state={
            "members": {
                frame.instrument.canonical_id: {
                    "frame_id": frame.frame_id,
                    "frame_content_hash": frame.content_hash(),
                }
            },
            "regime": "TREND",
            "volatility_state": "HIGH",
            "liquidity_state": "DEEP",
            "summary": {"breadth_ratio": 0.7},
        },
        source_frame_ids=(frame.frame_id,),
        source_frame_hashes=(frame.content_hash(),),
        source_instrument_ids=(frame.instrument.canonical_id,),
    )


def _row(index):
    cutoff = (10 + index * 20) * SECOND
    frame = _frame(cutoff)
    context = _context(cutoff, frame)
    return build_training_row(
        row_id="ROW-%02d" % index,
        question_ref=QUESTION,
        question_definition_hash=QUESTION_HASH,
        instrument_features=extract_instrument_features(frame),
        context_features=extract_context_features(context),
        label=1 if index % 2 else 0,
        label_artifact_id="QOUT-%02d" % index,
        label_content_hash=("%x" % ((index % 15) + 1)) * 64,
        label_known_at_ns=cutoff + 10 * SECOND,
    )


class ResearchQualificationPlaneTests(unittest.TestCase):
    def test_feature_rows_keep_feature_and_label_time_separate(self):
        row = _row(0)
        self.assertLessEqual(row["feature_known_at_ns"], row["cutoff_at_ns"])
        self.assertGreater(row["label_known_at_ns"], row["cutoff_at_ns"])
        self.assertIn("microstructure.mean_depth10_imbalance", row["features"])
        self.assertEqual(row["context"]["context.regime"], "TREND")
        self.assertEqual({ref["role"] for ref in row["source_refs"]}, {"FEATURE", "LABEL"})

    def test_dataset_rejects_post_cutoff_feature_leakage(self):
        row = dict(_row(0))
        row["feature_known_at_ns"] = row["cutoff_at_ns"] + 1
        with self.assertRaisesRegex(ResearchContractError, "known by cutoff"):
            build_point_in_time_dataset_manifest(
                dataset_id="DS-BAD",
                question_ref=QUESTION,
                question_definition_hash=QUESTION_HASH,
                rows=(row,),
                feature_schema_version="1.0",
                created_at_ns=100 * SECOND,
            )

    def test_walk_forward_is_strictly_ordered_and_embargoed(self):
        rows = [_row(i) for i in range(8)]
        dataset = build_point_in_time_dataset_manifest(
            dataset_id="DS-DIRECTION-001",
            question_ref=QUESTION,
            question_definition_hash=QUESTION_HASH,
            rows=rows,
            feature_schema_version="1.0",
            created_at_ns=1000 * SECOND,
        )
        validate_dataset_manifest(dataset)
        plan = build_walk_forward_plan(dataset, minimum_train_rows=3, validation_rows=2, step_rows=1, embargo_ns=SECOND)
        validate_walk_forward_plan(plan)
        self.assertGreaterEqual(plan["fold_count"], 1)
        for fold in plan["folds"]:
            self.assertLess(fold["train_last_label_known_at_ns"] + SECOND, fold["validation_first_cutoff_ns"])
            self.assertFalse(set(fold["train_row_ids"]) & set(fold["validation_row_ids"]))

    def test_experiment_and_artifact_lineage_are_preregistered_not_training(self):
        rows = [_row(i) for i in range(8)]
        dataset = build_point_in_time_dataset_manifest(
            dataset_id="DS-DIRECTION-002",
            question_ref=QUESTION,
            question_definition_hash=QUESTION_HASH,
            rows=rows,
            feature_schema_version="1.0",
            created_at_ns=1000 * SECOND,
        )
        plan = build_walk_forward_plan(dataset, minimum_train_rows=3, validation_rows=2, step_rows=1)
        experiment = build_experiment_contract(
            experiment_id="EXP-DIRECTION-LINEAR-001",
            question_ref=QUESTION,
            question_definition_hash=QUESTION_HASH,
            dataset_hash=dataset["integrity"]["content_hash"],
            walk_forward_hash=plan["integrity"]["content_hash"],
            species="LOGISTIC_DIRECTION",
            implementation_ref="future.training.logistic_v1",
            implementation_hash="c" * 64,
            hyperparameters={"regularization": 1.0},
            seed=7,
            metric_ids=("BRIER", "LOG_LOSS"),
            registered_at_ns=1001 * SECOND,
        )
        self.assertEqual(experiment["training_status"], "NOT_RUN")
        self.assertFalse(experiment["authority"]["trains_models"])
        lineage = build_model_artifact_lineage(
            experiment,
            model_ref="LOGISTIC-DIRECTION@1.0.0",
            artifact_hash="d" * 64,
            training_code_hash="e" * 64,
            fold_receipt_hashes=("f" * 64,),
            produced_at_ns=1002 * SECOND,
        )
        self.assertEqual(lineage["dataset_hash"], dataset["integrity"]["content_hash"])
        self.assertFalse(lineage["authority"]["promotes_models"])

    def test_promotion_assessment_never_mutates_model_lifecycle(self):
        insufficient = assess_promotion_evidence(
            model_ref="MODEL@1",
            question_ref=QUESTION,
            evaluation_receipt_hash="1" * 64,
            sample_count=40,
            metric_value=0.18,
            baseline_metric_value=0.25,
            higher_is_better=False,
            minimum_samples=100,
            required_improvement=0.02,
        )
        self.assertEqual(insufficient["decision"], "INSUFFICIENT_EVIDENCE")
        supported = assess_promotion_evidence(
            model_ref="MODEL@1",
            question_ref=QUESTION,
            evaluation_receipt_hash="2" * 64,
            sample_count=200,
            metric_value=0.18,
            baseline_metric_value=0.25,
            higher_is_better=False,
            minimum_samples=100,
            required_improvement=0.02,
        )
        self.assertEqual(supported["decision"], "CANDIDATE_PROMOTION_EVIDENCE_SUPPORTED")
        self.assertFalse(supported["mutates_model_lifecycle"])
        self.assertFalse(supported["authority"]["promotes_models"])


if __name__ == "__main__":
    unittest.main()
