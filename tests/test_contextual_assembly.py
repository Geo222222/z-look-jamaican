from __future__ import annotations

import hashlib
import unittest
from decimal import Decimal

from autonomous_kernel.assembly import AssemblyReceipt, ContextualAssemblyError, ContextualAssemblyReceipt, ModelContextProfile, contextualize_prediction
from autonomous_kernel.context import build_market_context
from autonomous_kernel.prediction import create_prediction
from tests.test_market_context import BTC, histories


HORIZON = 1_000


def component(source, model_ref: str, prediction_id: str, expected: object, probability: object):
    return create_prediction(source, mode="PROSPECTIVE_SHADOW", prediction_at_ns=source.known_at_ns, created_at_ns=source.known_at_ns, horizon_ns=HORIZON, expected_move_bps=expected, probability_positive=probability, interval_low_bps="-30", interval_high_bps="30", model_refs=(model_ref,), prediction_id=prediction_id)


def base_receipt(source, first, second):
    base_prediction = create_prediction(source, mode="PROSPECTIVE_SHADOW", prediction_at_ns=source.known_at_ns, created_at_ns=source.known_at_ns + 1, horizon_ns=HORIZON, expected_move_bps="0", probability_positive="0.5", interval_low_bps="-30", interval_high_bps="30", model_refs=(first.model_refs[0], second.model_refs[0]), prediction_id="PRED-Z8-BASE")
    contributors = []
    for prediction in (first, second): contributors.append({"model_ref": prediction.model_refs[0], "registry_state": "SHADOW", "registry_event_hash": "1" * 64, "model_definition_hash": "2" * 64, "model_artifact_hash": "3" * 64, "component_prediction_id": prediction.prediction_id, "component_prediction_hash": prediction.content_hash(), "competence_profile_hash": None, "competence_as_of_ns": None, "competence_status": "NO_PRIOR_MATCHED_COMPETENCE", "resolved_count": 0, "sample_strength": "0", "skill": None, "mae_bps": None, "raw_weight_score": "1", "normalized_weight": "0.5"})
    receipt = AssemblyReceipt(receipt_id="ASM-Z8-BASE", assembly_at_ns=source.known_at_ns + 1, mode="PROSPECTIVE_SHADOW", evidence_class="FORWARD_EVALUABLE", representation_frame_id=source.frame_id, representation_content_hash=source.content_hash(), prediction_at_ns=source.known_at_ns, horizon_ns=HORIZON, resolves_at_ns=source.known_at_ns + HORIZON, target_metric="ZLJ_AGGREGATE_MIDPOINT_RETURN_BPS_V1", assembled_prediction_id=base_prediction.prediction_id, assembled_prediction_content_hash=base_prediction.content_hash(), contributors=tuple(contributors))
    return base_prediction, receipt


class ContextualAssemblyTests(unittest.TestCase):
    def test_context_overlay_is_bounded_explicit_and_does_not_rewrite_z8(self):
        btc, eth = histories(); source = btc[-1]; context = build_market_context(tuple(btc + eth), minimum_history_points=3); first = component(source, "MODEL-A@1", "PRED-A", "20", "0.7"); second = component(source, "MODEL-B@1", "PRED-B", "-20", "0.3"); base_prediction, receipt = base_receipt(source, first, second); base_wire = receipt.to_wire()
        profiles = (ModelContextProfile("MODEL-A@1", ("CORE_MARKET",), {"direction": ("RISK_ON",)}, {}, "FLOW"), ModelContextProfile("MODEL-B@1", ("CORRELATION",), {}, {"direction": ("RISK_ON",)}, "BOOK"))
        final, contextual = contextualize_prediction(source, (second, first), base_prediction, receipt, context, profiles, assembly_at_ns=source.known_at_ns + 2)
        self.assertEqual(base_wire, receipt.to_wire()); weights = {item["model_ref"]: Decimal(item["final_weight"]) for item in contextual.contributors}; self.assertGreater(weights["MODEL-A@1"], weights["MODEL-B@1"]); self.assertGreater(Decimal(final.expected_move_bps), Decimal("0")); self.assertEqual(Decimal("1"), sum(weights.values()));
        for item in contextual.contributors: self.assertGreaterEqual(Decimal(item["context_multiplier"]), Decimal("0.75")); self.assertLessEqual(Decimal(item["context_multiplier"]), Decimal("1.25")); self.assertTrue(item["reason_codes"])
        self.assertEqual(contextual.to_wire(), ContextualAssemblyReceipt.from_wire(contextual.to_wire()).to_wire())

    def test_missing_context_profile_fails_closed(self):
        btc, eth = histories(); source = btc[-1]; context = build_market_context(tuple(btc + eth)); first = component(source, "MODEL-A@1", "PRED-A", "10", "0.6"); second = component(source, "MODEL-B@1", "PRED-B", "-10", "0.4"); base_prediction, receipt = base_receipt(source, first, second)
        with self.assertRaises(ContextualAssemblyError): contextualize_prediction(source, (first, second), base_prediction, receipt, context, (ModelContextProfile("MODEL-A@1", (), {}, {}, "A"),), assembly_at_ns=source.known_at_ns + 2)

    def test_future_context_is_rejected(self):
        btc, eth = histories(); source = btc[-1]; context = build_market_context(tuple(btc + eth)); first = component(source, "MODEL-A@1", "PRED-A", "10", "0.6"); second = component(source, "MODEL-B@1", "PRED-B", "-10", "0.4"); base_prediction, receipt = base_receipt(source, first, second); profiles = (ModelContextProfile("MODEL-A@1", (), {}, {}, "A"), ModelContextProfile("MODEL-B@1", (), {}, {}, "B"))
        with self.assertRaises(ContextualAssemblyError): contextualize_prediction(source, (first, second), base_prediction, receipt, context, profiles, assembly_at_ns=context.known_at_ns - 1)


if __name__ == "__main__": unittest.main()
