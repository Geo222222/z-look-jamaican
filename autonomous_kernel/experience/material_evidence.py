from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Optional, Sequence, Tuple

from ..book_bridge import ZLJBookSigner, canonical_json
from .economic_graph import EconomicInstrumentGraph


class MaterialEvidenceError(ValueError):
    pass


def _dt_from_ns(value: int) -> datetime:
    if value < 0:
        raise MaterialEvidenceError("timestamp must be non-negative")
    return datetime.fromtimestamp(value / 1_000_000_000, tz=timezone.utc)


@dataclass(frozen=True)
class MaterialEvidenceIntent:
    """Minimum-necessary Book evidence for a material ZLJ artifact.

    High-volume raw observations and experience records remain in ZLJ. The Book
    receives material artifact proofs or journal commitments rather than an
    automatic copy of every market event.
    """

    event_type: str
    evidence_class: str
    subject_id: str
    occurred_at_ns: int
    known_at_ns: int
    payload: bytes
    payload_ref: Optional[str] = None
    correlation_id: Optional[str] = None
    causation_receipt_id: Optional[str] = None
    evidence_receipt_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.event_type.startswith("ZLJ."):
            raise MaterialEvidenceError("material evidence event_type must be ZLJ.*")
        if self.evidence_class not in {"CONSTITUTIONAL", "ECONOMIC", "ANALYTICAL"}:
            raise MaterialEvidenceError("invalid evidence_class")
        if not self.subject_id:
            raise MaterialEvidenceError("subject_id is required")
        if self.occurred_at_ns < 0 or self.known_at_ns < 0:
            raise MaterialEvidenceError("timestamps must be non-negative")
        if len(set(self.evidence_receipt_ids)) != len(self.evidence_receipt_ids):
            raise MaterialEvidenceError("evidence receipt ids must be unique")
        if any(not item for item in self.evidence_receipt_ids):
            raise MaterialEvidenceError("evidence receipt ids must be non-empty")

    @property
    def payload_digest(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()

    def sign(
        self,
        *,
        signer: ZLJBookSigner,
        receipt_id: str,
        produced_at: datetime,
        privacy_class: str = "CONFIDENTIAL_EVIDENCE",
        visibility_scope: Sequence[str] = ("INSTITUTION",),
    ) -> Mapping[str, object]:
        return signer.sign_v2_envelope(
            receipt_id=receipt_id,
            event_type=self.event_type,
            evidence_class=self.evidence_class,
            subject_id=self.subject_id,
            occurred_at=_dt_from_ns(self.occurred_at_ns),
            known_at=_dt_from_ns(self.known_at_ns),
            produced_at=produced_at,
            payload_digest=self.payload_digest,
            privacy_class=privacy_class,
            visibility_scope=visibility_scope,
            payload_ref=self.payload_ref,
            correlation_id=self.correlation_id,
            causation_receipt_id=self.causation_receipt_id,
            evidence_receipt_ids=self.evidence_receipt_ids,
            source_event_at=_dt_from_ns(self.occurred_at_ns),
        )


def material_graph_evidence(
    graph: EconomicInstrumentGraph,
    *,
    payload_ref: Optional[str] = None,
    correlation_id: Optional[str] = None,
    causation_receipt_id: Optional[str] = None,
    evidence_receipt_ids: Sequence[str] = (),
) -> MaterialEvidenceIntent:
    """Create the Book-bound proof intent for one graph version.

    Graph activation/version changes are material because all later experience
    joins depend on the structural economic identities encoded here.
    """

    return MaterialEvidenceIntent(
        event_type="ZLJ.ECONOMIC_INSTRUMENT_GRAPH",
        evidence_class="ANALYTICAL",
        subject_id=f"{graph.graph_id}@{graph.graph_version}",
        occurred_at_ns=graph.effective_at_ns,
        known_at_ns=graph.known_at_ns,
        payload=canonical_json(graph.to_wire()),
        payload_ref=payload_ref,
        correlation_id=correlation_id,
        causation_receipt_id=causation_receipt_id,
        evidence_receipt_ids=tuple(evidence_receipt_ids),
    )
