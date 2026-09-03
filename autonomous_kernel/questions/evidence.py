from __future__ import annotations

from typing import Optional, Sequence

from ..book_bridge import canonical_json
from ..experience.material_evidence import MaterialEvidenceIntent
from .contracts import QuestionRegistrySnapshot


def material_question_registry_evidence(
    registry: QuestionRegistrySnapshot,
    *,
    payload_ref: Optional[str] = None,
    correlation_id: Optional[str] = None,
    causation_receipt_id: Optional[str] = None,
    evidence_receipt_ids: Sequence[str] = (),
) -> MaterialEvidenceIntent:
    """Create minimum-necessary Book evidence for one registry activation.

    A registry version is material because it fixes the questions/outcomes ZLJ
    claims to evaluate prospectively. The Book commitment prevents redefining a
    target after results are observed without creating a new registry identity.
    """

    return MaterialEvidenceIntent(
        event_type="ZLJ.QUESTION_REGISTRY",
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
