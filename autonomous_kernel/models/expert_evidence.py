from __future__ import annotations

from typing import Optional, Sequence

from ..book_bridge import canonical_json
from ..experience.material_evidence import MaterialEvidenceIntent
from .question_experts import QuestionExpertRegistrySnapshot


def material_question_expert_registry_evidence(
    registry: QuestionExpertRegistrySnapshot,
    *,
    payload_ref: Optional[str] = None,
    correlation_id: Optional[str] = None,
    causation_receipt_id: Optional[str] = None,
    evidence_receipt_ids: Sequence[str] = (),
) -> MaterialEvidenceIntent:
    """Create minimum-necessary Book evidence for one expert-registry activation.

    This freezes which exact expert definitions existed before prospective
    evaluation. It is NOT competence evidence and does not promote any expert
    beyond the lifecycle state already carried by the registry snapshot.
    """
    return MaterialEvidenceIntent(
        event_type="ZLJ.QUESTION_EXPERT_REGISTRY",
        evidence_class="ANALYTICAL",
        subject_id="%s@%s" % (registry.registry_id, registry.version),
        occurred_at_ns=registry.effective_at_ns,
        known_at_ns=registry.known_at_ns,
        payload=canonical_json(registry.to_wire()),
        payload_ref=payload_ref,
        correlation_id=correlation_id,
        causation_receipt_id=causation_receipt_id,
        evidence_receipt_ids=tuple(evidence_receipt_ids),
    )
