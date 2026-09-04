from __future__ import annotations

import copy

import pytest

from autonomous_kernel.experts import (
    ExpertSchoolError,
    assemble_expert_claims,
    build_baseline_expert_school,
    build_competence_memory,
    build_expert_claim,
    contextual_competence,
    score_expert_claim,
)
from autonomous_kernel.intelligence import (
    IntelligencePublicationError,
    build_intelligence_publication,
    validate_intelligence_publication,
)


def _direction_experts():
    school = build_baseline_expert_school()
    contracts = [
        item for item in school["experts"]
        if item["expert_id"].startswith("ECONOMIC_ROOT_DIRECTION_10S_")
    ]
    assert len(contracts) >= 5
    return school, contracts


def _claim(contract, probability, evidence_ref):
    question_ref = contract["question_refs"][0]
    return build_expert_claim(
        contract,
        question_ref=question_ref,
        cutoff_ns=1_000_000_000,
        answer=probability,
        evidence_refs=(evidence_ref,),
        experience_refs=("experience:market:1",),
        input_snapshot_hash="1" * 64,
    )


def test_baseline_expert_school_covers_active_questions_without_claiming_authority():
    school = build_baseline_expert_school()
    assert school["lifecycle_state"] == "CANDIDATE_POPULATION"
    assert school["expert_count"] >= 35
    assert school["claims_competence"] is False
    assert school["sets_adaptive_weights"] is False
    assert school["authority"]["capital_decision"] is False
    assert school["authority"]["risk_authorization"] is False
    assert school["authority"]["external_execution"] is False
    assert all(item["lifecycle_state"] == "CANDIDATE" for item in school["experts"])
    assert all(len(item["question_refs"]) == 1 for item in school["experts"])


def test_claims_are_graded_into_competence_then_contextually_assembled():
    _, contracts = _direction_experts()
    claim_a = _claim(contracts[0], 0.80, "evidence:independent:a")
    claim_b = _claim(contracts[1], 0.60, "evidence:independent:b")

    record_a = score_expert_claim(
        contracts[0], claim_a, True,
        resolved_at_ns=12_000_000_000,
        context={"regime": "TREND", "liquidity": "DEEP"},
    )
    record_b = score_expert_claim(
        contracts[1], claim_b, True,
        resolved_at_ns=12_000_000_000,
        context={"regime": "TREND", "liquidity": "DEEP"},
    )
    memory = build_competence_memory((record_a, record_b), now_ns=13_000_000_000)
    assert memory["entry_count"] == 2
    entry_a = next(item for item in memory["entries"] if item["expert_ref"] == claim_a["expert_ref"])
    contextual = contextual_competence(entry_a, {"regime": "TREND", "liquidity": "DEEP"})
    assert 0.0 <= contextual["contextual_score"] <= 1.0
    assert set(contextual["matched_context_dimensions"]) == {"regime", "liquidity"}

    assembly = assemble_expert_claims(
        (claim_a, claim_b), memory, {"regime": "TREND", "liquidity": "DEEP"}
    )
    assert assembly["question_ref"] == claim_a["question_ref"]
    assert 0.60 <= assembly["assembled_estimate"] <= 0.80
    assert abs(sum(item["weight"] for item in assembly["expert_contributions"]) - 1.0) < 1e-12
    assert assembly["authority"]["sets_adaptive_weights"] is True
    assert assembly["authority"]["capital_decision"] is False


def test_shared_evidence_is_discounted_relative_to_independent_testimony():
    _, contracts = _direction_experts()
    claim_a = _claim(contracts[0], 0.80, "evidence:shared")
    claim_b = _claim(contracts[1], 0.70, "evidence:shared")
    claim_c = _claim(contracts[2], 0.65, "evidence:independent")
    records = [
        score_expert_claim(contract, claim, True, resolved_at_ns=12_000_000_000, context={"regime": "TREND"})
        for contract, claim in zip(contracts[:3], (claim_a, claim_b, claim_c))
    ]
    memory = build_competence_memory(records, now_ns=13_000_000_000)
    assembly = assemble_expert_claims((claim_a, claim_b, claim_c), memory, {"regime": "TREND"})
    weights = {item["expert_ref"]: item["weight"] for item in assembly["expert_contributions"]}
    assert weights[claim_c["expert_ref"]] > 0.0
    assert weights[claim_a["expert_ref"]] < 0.5


def test_scoring_fails_closed_before_horizon():
    _, contracts = _direction_experts()
    claim = _claim(contracts[0], 0.8, "evidence:a")
    with pytest.raises(ExpertSchoolError):
        score_expert_claim(contracts[0], claim, True, resolved_at_ns=2_000_000_000)


def test_intelligence_publication_is_benjamin_consumable_but_never_a_trade_instruction():
    _, contracts = _direction_experts()
    claim_a = _claim(contracts[0], 0.80, "evidence:a")
    claim_b = _claim(contracts[1], 0.60, "evidence:b")
    records = [
        score_expert_claim(contract, claim, True, resolved_at_ns=12_000_000_000, context={"regime": "TREND"})
        for contract, claim in zip(contracts[:2], (claim_a, claim_b))
    ]
    memory = build_competence_memory(records, now_ns=13_000_000_000)
    assembly = assemble_expert_claims((claim_a, claim_b), memory, {"regime": "TREND"})
    publication = build_intelligence_publication(
        assembly,
        published_at_ns=13_000_000_001,
        evidence_refs=("evidence:a", "evidence:b"),
        competence_memory_hash=memory["integrity"]["content_hash"],
        market_context_hash="2" * 64,
    )
    validate_intelligence_publication(publication)
    assert publication["consumer_boundary"]["may_be_consumed_by"] == ["BENJAMIN"]
    assert publication["authority"]["economic_decision"] is False
    assert publication["authority"]["risk_authorization"] is False
    assert publication["authority"]["external_execution"] is False

    tampered = copy.deepcopy(publication)
    tampered["capital_action"] = "BUY"
    with pytest.raises(IntelligencePublicationError):
        validate_intelligence_publication(tampered)
