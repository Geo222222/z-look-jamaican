from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autonomous_kernel.evaluation.relationship_resolver import (
    RELATIONSHIP_RESOLVER_IMPLEMENTATION_REF,
    QuestionOutcomePendingError,
    QuestionResolverError,
    resolve_relationship_question,
)
from autonomous_kernel.experience.contracts import ExperienceTimescale
from autonomous_kernel.experience.relationship_recovery import recover_economic_relationship_state
from autonomous_kernel.experience.relationships import EconomicRelationshipState, RelationshipStateError
from autonomous_kernel.prediction import (
    PredictionArtifactRef,
    QuestionPredictionJournal,
    build_question_bound_prediction,
)
from autonomous_kernel.questions.catalog import question_catalog_v1
from autonomous_kernel.questions.contracts import QuestionRegistryEntry, build_question_registry_snapshot
from autonomous_kernel.questions.readiness import build_resolver_ready_registry


T = 1_788_400_000_000_000_000
SECOND = 1_000_000_000
MINUTE = 60 * SECOND
RELATIONSHIP = "REL-BTC-SPOT-PERP"
ROOT = "ASSET.BTC"


def _question(question_id):
    return next(item for item in question_catalog_v1() if item.question_id == question_id)


def _state(
    state_id,
    basis_bps,
    *,
    cutoff,
    known=None,
    relationship_id=RELATIONSHIP,
    graph_hash=None,
    spot_quote="USD",
    derivative_quote="USD",
    source_node="NODE-SPOT",
    target_node="NODE-PERP",
):
    known_at = cutoff if known is None else known
    graph = graph_hash or ("1" * 64)
    comparable = spot_quote == derivative_quote
    qualified_basis = comparable and basis_bps is not None
    state = {
        "basis": {
            "status": "QUALIFIED" if qualified_basis else "UNAVAILABLE",
            "reason": None if qualified_basis else "QUOTE_UNIT_MISMATCH_REQUIRES_NORMALIZATION_PROOF",
            "spot_quote_unit": spot_quote,
            "derivative_quote_unit": derivative_quote,
            "basis_bps": None if not qualified_basis else str(basis_bps),
            "annualized_basis_bps": None,
            "annualized_status": "UNAVAILABLE",
        },
        "latest_returns": {
            "spot_return_bps": "1",
            "derivative_return_bps": "1",
            "confirmation": "DIRECTIONALLY_CONFIRMED",
        },
        "lagged_association": {
            "status": "UNAVAILABLE",
            "truth_class": "CAUSAL_CUTOFF_LAGGED_ASSOCIATION_NOT_CAUSALITY",
            "association": "UNAVAILABLE",
        },
        "relative_liquidity": {
            "spread_comparison_status": "UNAVAILABLE",
            "depth_comparison_status": "UNAVAILABLE",
            "depth_comparison_reason": "SPOT_DERIVATIVE_AMOUNT_NORMALIZATION_NOT_QUALIFIED",
        },
        "derivative_structure": {
            "funding": {"status": "UNAVAILABLE"},
            "open_interest": {
                "status": "QUALIFIED",
                "value": "100",
                "unit_semantics": "PROVIDER_NATIVE_UNSPECIFIED",
                "cross_venue_comparable": False,
            },
            "open_interest_change": {"status": "UNAVAILABLE"},
            "mark_index": {"status": "UNAVAILABLE"},
            "liquidations": {
                "status": "QUALIFIED",
                "reported_buy_size": "100",
                "unit_semantics": "PROVIDER_NATIVE_UNSPECIFIED",
                "cross_venue_comparable": False,
            },
        },
        "unit_air_gap": {
            "price_basis_directly_comparable": comparable,
            "spot_derivative_amounts_directly_comparable": False,
            "cross_venue_open_interest_directly_comparable": False,
            "cross_venue_liquidation_size_directly_comparable": False,
            "rule": "NUMERIC_EQUALITY_NEVER_IMPLIES_ECONOMIC_UNIT_COMPATIBILITY",
        },
        "truth_boundaries": {
            "lagged_association_is_causality": False,
            "open_interest_cross_venue_comparable": False,
            "liquidation_size_cross_venue_comparable": False,
            "structural_graph_is_empirical_leadership_claim": False,
        },
    }
    return EconomicRelationshipState(
        relationship_state_id=state_id,
        relationship_id=relationship_id,
        relationship_type="SPOT_DERIVATIVE",
        economic_root_id=ROOT,
        cutoff_at_ns=cutoff,
        known_at_ns=known_at,
        status="QUALIFIED",
        graph_id="GRAPH-BTC",
        graph_version="1",
        graph_hash=graph,
        source_node_id=source_node,
        target_node_id=target_node,
        source_frame_ids=("REP-SPOT-%s" % state_id, "REP-DERIV-%s" % state_id),
        source_frame_hashes=("a" * 64, "b" * 64),
        state=state,
    )


def _registry(question):
    return build_question_registry_snapshot(
        registry_id="ZLJ-MARKET-QUESTIONS",
        version="relationship-resolver-test",
        entries=(
            QuestionRegistryEntry(
                definition=question,
                lifecycle_state="RESOLVER_READY",
                registered_at_ns=T - 2 * SECOND,
                effective_at_ns=T - SECOND,
                resolver_implementation_ref=RELATIONSHIP_RESOLVER_IMPLEMENTATION_REF,
            ),
        ),
        known_at_ns=T - 2 * SECOND,
        effective_at_ns=T - SECOND,
    )


def _prediction(root, question, baseline):
    refs = [
        PredictionArtifactRef(
            artifact_type="ECONOMIC_RELATIONSHIP_STATE",
            artifact_id=baseline.relationship_state_id,
            content_hash=baseline.content_hash(),
            known_at_ns=baseline.known_at_ns,
            status="QUALIFIED",
            timescales=(),
            feature_families=("ECONOMIC_RELATIONSHIP_STATE",),
            subject_ids=(RELATIONSHIP, ROOT),
        )
    ]
    if question.question_id == "SPOT_DERIVATIVE_BASIS_CHANGE_5M":
        refs.append(
            PredictionArtifactRef(
                artifact_type="MARKET_EXPERIENCE",
                artifact_id="EXP-BTC-REL",
                content_hash="3" * 64,
                known_at_ns=T - 1,
                status="QUALIFIED",
                timescales=(ExperienceTimescale.SHORT, ExperienceTimescale.SESSION),
                feature_families=(),
                subject_ids=(RELATIONSHIP, ROOT),
            )
        )
    else:
        refs.append(
            PredictionArtifactRef(
                artifact_type="MARKET_WIDE_EXPERIENCE",
                artifact_id="MW-BTC-REL",
                content_hash="4" * 64,
                known_at_ns=T - 1,
                status="QUALIFIED",
                timescales=(ExperienceTimescale.SHORT, ExperienceTimescale.SESSION),
                feature_families=("MARKET_WIDE_CONTEXT",),
                subject_ids=(RELATIONSHIP, ROOT),
            )
        )
    prediction = build_question_bound_prediction(
        registry=_registry(question),
        question=question,
        subject_id=RELATIONSHIP,
        mode="PROSPECTIVE_SHADOW",
        evidence_class="FORWARD_EVALUABLE",
        cutoff_at_ns=T,
        created_at_ns=T,
        answer={"value": "0"},
        model_refs=("MODEL-RELATIONSHIP-TEST",),
        artifact_refs=tuple(refs),
    )
    QuestionPredictionJournal(root).append(prediction, journaled_at_ns=T + 1)
    return prediction


class RelationshipResolverTests(unittest.TestCase):
    def test_relationship_state_recovery_round_trip_preserves_exact_hash(self):
        original = _state("RELSTATE-BASE", "100", cutoff=T, known=T - 1)
        restored = recover_economic_relationship_state(original.to_wire())
        self.assertEqual(original.to_wire(), restored.to_wire())
        self.assertEqual(original.content_hash(), restored.content_hash())

    def test_recovery_rejects_semantic_unit_air_gap_weakening_even_with_valid_hash(self):
        original = _state("RELSTATE-BASE", "100", cutoff=T, known=T - 1)
        changed_state = dict(original.state)
        changed_air_gap = dict(changed_state["unit_air_gap"])
        changed_air_gap["spot_derivative_amounts_directly_comparable"] = True
        changed_state["unit_air_gap"] = changed_air_gap
        invalid = EconomicRelationshipState(
            relationship_state_id=original.relationship_state_id,
            relationship_id=original.relationship_id,
            relationship_type=original.relationship_type,
            economic_root_id=original.economic_root_id,
            cutoff_at_ns=original.cutoff_at_ns,
            known_at_ns=original.known_at_ns,
            status=original.status,
            graph_id=original.graph_id,
            graph_version=original.graph_version,
            graph_hash=original.graph_hash,
            source_node_id=original.source_node_id,
            target_node_id=original.target_node_id,
            source_frame_ids=original.source_frame_ids,
            source_frame_hashes=original.source_frame_hashes,
            state=changed_state,
            builder_version=original.builder_version,
        )
        with self.assertRaisesRegex(RelationshipStateError, "air-gap cannot be weakened"):
            recover_economic_relationship_state(invalid.to_wire())

    def test_basis_change_uses_exact_relationship_state_difference(self):
        baseline = _state("RELSTATE-BASE", "100", cutoff=T, known=T - 1)
        forward = _state("RELSTATE-FWD", "80", cutoff=T + 5 * MINUTE)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = _question("SPOT_DERIVATIVE_BASIS_CHANGE_5M")
            prediction = _prediction(root, question, baseline)
            outcome = resolve_relationship_question(root, prediction.prediction_id, baseline_state=baseline, forward_states=(forward,), now_at_ns=T + 5 * MINUTE + SECOND)
            self.assertEqual({"value": "-20"}, outcome.realized_answer)
            self.assertEqual("RELSTATE-FWD", outcome.resolution_evidence[1].artifact_id)

    def test_relative_value_positive_means_absolute_basis_convergence(self):
        baseline = _state("RELSTATE-BASE", "-100", cutoff=T, known=T - 1)
        forward = _state("RELSTATE-FWD", "-60", cutoff=T + 5 * MINUTE)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = _question("SPOT_DERIVATIVE_RELATIVE_VALUE_CONVERGENCE_5M")
            prediction = _prediction(root, question, baseline)
            outcome = resolve_relationship_question(root, prediction.prediction_id, baseline_state=baseline, forward_states=(forward,), now_at_ns=T + 5 * MINUTE + SECOND)
            self.assertEqual({"value": "40"}, outcome.realized_answer)

    def test_changed_graph_or_wrong_relationship_cannot_substitute_for_frozen_relationship(self):
        baseline = _state("RELSTATE-BASE", "100", cutoff=T, known=T - 1)
        wrong_relationship = _state("RELSTATE-WRONG", "10", cutoff=T + 5 * MINUTE, relationship_id="REL-OTHER")
        changed_graph = _state("RELSTATE-NEW-GRAPH", "20", cutoff=T + 5 * MINUTE + SECOND, graph_hash="9" * 64)
        exact = _state("RELSTATE-EXACT", "70", cutoff=T + 5 * MINUTE + 2 * SECOND)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, _question("SPOT_DERIVATIVE_BASIS_CHANGE_5M"), baseline)
            outcome = resolve_relationship_question(
                root,
                prediction.prediction_id,
                baseline_state=baseline,
                forward_states=(wrong_relationship, changed_graph, exact),
                now_at_ns=T + 5 * MINUTE + 3 * SECOND,
            )
            self.assertEqual({"value": "-30"}, outcome.realized_answer)
            self.assertEqual("RELSTATE-EXACT", outcome.resolution_evidence[1].artifact_id)

    def test_quote_unit_mismatch_cannot_become_direct_basis_outcome(self):
        baseline = _state(
            "RELSTATE-BASE",
            None,
            cutoff=T,
            known=T - 1,
            spot_quote="USD",
            derivative_quote="USDT",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, _question("SPOT_DERIVATIVE_BASIS_CHANGE_5M"), baseline)
            with self.assertRaisesRegex(QuestionResolverError, "basis is not qualified"):
                resolve_relationship_question(root, prediction.prediction_id, baseline_state=baseline, forward_states=(), now_at_ns=T + 6 * MINUTE)

    def test_missing_compatible_forward_state_is_pending_then_unresolvable(self):
        baseline = _state("RELSTATE-BASE", "100", cutoff=T, known=T - 1)
        changed_graph = _state("RELSTATE-NEW-GRAPH", "80", cutoff=T + 5 * MINUTE, graph_hash="9" * 64)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction = _prediction(root, _question("SPOT_DERIVATIVE_BASIS_CHANGE_5M"), baseline)
            with self.assertRaisesRegex(QuestionOutcomePendingError, "window remains open"):
                resolve_relationship_question(
                    root,
                    prediction.prediction_id,
                    baseline_state=baseline,
                    forward_states=(changed_graph,),
                    now_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns,
                )
            outcome = resolve_relationship_question(
                root,
                prediction.prediction_id,
                baseline_state=baseline,
                forward_states=(changed_graph,),
                now_at_ns=prediction.resolves_at_ns + prediction.max_resolution_lag_ns + 1,
            )
            self.assertEqual("UNRESOLVABLE", outcome.status)

    def test_registry_promotes_only_implemented_relationship_questions(self):
        basis = _question("SPOT_DERIVATIVE_BASIS_CHANGE_5M")
        relative = _question("SPOT_DERIVATIVE_RELATIVE_VALUE_CONVERGENCE_5M")
        base = build_question_registry_snapshot(
            registry_id="ZLJ-MARKET-QUESTIONS",
            version="before-relationship",
            entries=(
                QuestionRegistryEntry(basis, "DEFINED", T - 3 * SECOND, T - 2 * SECOND),
                QuestionRegistryEntry(relative, "DEFINED", T - 3 * SECOND, T - 2 * SECOND),
            ),
            known_at_ns=T - 3 * SECOND,
            effective_at_ns=T - 2 * SECOND,
        )
        promoted = build_resolver_ready_registry(
            base,
            version="after-relationship",
            known_at_ns=T - SECOND,
            effective_at_ns=T,
            resolver_implementations={
                basis.question_ref: RELATIONSHIP_RESOLVER_IMPLEMENTATION_REF,
                relative.question_ref: RELATIONSHIP_RESOLVER_IMPLEMENTATION_REF,
            },
        )
        self.assertEqual({"RESOLVER_READY"}, {entry.lifecycle_state for entry in promoted.entries})


if __name__ == "__main__":
    unittest.main()
