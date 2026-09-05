from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from ..operations import canonical_hash
from ..store import writer_lock
from ..experts.contracts import validate_expert_claim
from ..experts.school import build_competence_memory
from .publication import validate_intelligence_publication
from .gate import (
    assess_benjamin_publication_qualification,
    build_benjamin_handoff,
    validate_benjamin_handoff,
    validate_benjamin_publication_qualification,
)


RUNTIME_SCHEMA_VERSION = 1
GENESIS = "GENESIS"
ALLOWED_EVENT_TYPES = {
    "EXPERT_CLAIM_RECORDED",
    "EXPERT_SCORE_RECORDED",
    "COMPETENCE_REBUILT",
    "EXPERT_ASSEMBLY_RECORDED",
    "INTELLIGENCE_PUBLISHED",
    "BENJAMIN_PUBLICATION_QUALIFIED",
    "BENJAMIN_PUBLICATION_BLOCKED",
    "BENJAMIN_HANDOFF_PUBLISHED",
    "MARKET_SYNTHESIS_PUBLISHED",
}


class IntelligenceRuntimeError(RuntimeError):
    pass


def _content_hash(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        return ""
    integrity = value.get("integrity")
    if not isinstance(integrity, Mapping):
        return ""
    return str(integrity.get("content_hash") or "")


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(value), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_jsonl(path: Path) -> Tuple[Mapping[str, Any], ...]:
    if not path.is_file():
        return ()
    output: List[Mapping[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IntelligenceRuntimeError("intelligence journal line %d is invalid JSON" % line_number) from exc
        if not isinstance(value, Mapping):
            raise IntelligenceRuntimeError("intelligence journal line must be an object")
        output.append(value)
    return tuple(output)


def _event(sequence: int, event_type: str, occurred_at_ns: int, payload: Mapping[str, Any], previous_hash: str) -> Mapping[str, Any]:
    if event_type not in ALLOWED_EVENT_TYPES:
        raise IntelligenceRuntimeError("unsupported intelligence event type")
    body = {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "sequence": int(sequence),
        "event_type": event_type,
        "occurred_at_ns": int(occurred_at_ns),
        "payload": dict(payload),
        "previous_hash": previous_hash,
    }
    wire = dict(body)
    wire["event_hash"] = canonical_hash(body)
    return wire


def validate_event_chain(events: Sequence[Mapping[str, Any]]) -> Tuple[str, ...]:
    errors: List[str] = []
    previous = GENESIS
    for index, event in enumerate(events):
        if event.get("schema_version") != RUNTIME_SCHEMA_VERSION or event.get("sequence") != index:
            errors.append("sequence %d schema/sequence mismatch" % index)
        if event.get("event_type") not in ALLOWED_EVENT_TYPES:
            errors.append("sequence %d unknown event type" % index)
        if event.get("previous_hash") != previous:
            errors.append("sequence %d previous hash mismatch" % index)
        body = {key: value for key, value in event.items() if key != "event_hash"}
        expected = canonical_hash(body)
        if event.get("event_hash") != expected:
            errors.append("sequence %d event hash mismatch" % index)
        previous = expected
    return tuple(errors)


def project_runtime(events: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    state: Dict[str, Any] = {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "claims": {},
        "scores": [],
        "competence": None,
        "assemblies": [],
        "syntheses": [],
        "publications": [],
        "qualifications": [],
        "handoffs": [],
        "event_count": 0,
        "last_event_hash": None,
    }
    for event in events:
        payload = event["payload"]
        if event["event_type"] == "EXPERT_CLAIM_RECORDED":
            claim = payload["claim"]
            state["claims"][claim["integrity"]["content_hash"]] = claim
        elif event["event_type"] == "EXPERT_SCORE_RECORDED":
            state["scores"].append(payload["score"])
        elif event["event_type"] == "COMPETENCE_REBUILT":
            state["competence"] = payload["competence"]
        elif event["event_type"] == "EXPERT_ASSEMBLY_RECORDED":
            state["assemblies"].append(payload["assembly"])
        elif event["event_type"] == "MARKET_SYNTHESIS_PUBLISHED":
            state["syntheses"].append(payload["synthesis"])
        elif event["event_type"] == "INTELLIGENCE_PUBLISHED":
            state["publications"].append(payload["publication"])
        elif event["event_type"] == "BENJAMIN_PUBLICATION_QUALIFIED":
            state["qualifications"].append(payload["qualification"])
        elif event["event_type"] == "BENJAMIN_PUBLICATION_BLOCKED":
            state["qualifications"].append(payload["qualification"])
        elif event["event_type"] == "BENJAMIN_HANDOFF_PUBLISHED":
            state["handoffs"].append(payload["handoff"])
    state["event_count"] = len(events)
    state["last_event_hash"] = None if not events else events[-1]["event_hash"]
    return state


class IntelligenceRuntime:
    """Append-only runtime for ZLJ expert intelligence. No capital authority."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.events_path = self.root / "memory/expert_intelligence.jsonl"
        self.state_path = self.root / "state/expert_intelligence.json"

    def events(self) -> Tuple[Mapping[str, Any], ...]:
        return _read_jsonl(self.events_path)

    def state(self) -> Mapping[str, Any]:
        if not self.state_path.is_file():
            return project_runtime(self.events())
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise IntelligenceRuntimeError("expert intelligence state must be an object")
        return value

    def _append(self, event_type: str, occurred_at_ns: int, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if int(occurred_at_ns) < 0:
            raise IntelligenceRuntimeError("occurred_at_ns must be non-negative")
        with writer_lock(self.root):
            events = list(self.events())
            errors = validate_event_chain(events)
            if errors:
                raise IntelligenceRuntimeError("expert intelligence journal invalid: " + "; ".join(errors))
            previous = GENESIS if not events else str(events[-1]["event_hash"])
            event = _event(len(events), event_type, int(occurred_at_ns), payload, previous)
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            events.append(event)
            _atomic_json(self.state_path, project_runtime(events))
            return event

    def record_claim(self, contract: Mapping[str, Any], claim: Mapping[str, Any], *, occurred_at_ns: int) -> Mapping[str, Any]:
        validate_expert_claim(contract, claim)
        return self._append("EXPERT_CLAIM_RECORDED", occurred_at_ns, {"claim": dict(claim)})

    def record_score(self, score: Mapping[str, Any], *, occurred_at_ns: int) -> Mapping[str, Any]:
        claim_hash = str(score.get("claim_hash", ""))
        state = project_runtime(self.events())
        if claim_hash not in state["claims"]:
            raise IntelligenceRuntimeError("score references an unknown expert claim")
        return self._append("EXPERT_SCORE_RECORDED", occurred_at_ns, {"score": dict(score)})

    def rebuild_competence(self, *, known_at_ns: int, recent_half_life_ns: int = 3_600_000_000_000) -> Mapping[str, Any]:
        state = project_runtime(self.events())
        competence = build_competence_memory(state["scores"], now_ns=known_at_ns, recent_half_life_ns=recent_half_life_ns)
        self._append("COMPETENCE_REBUILT", known_at_ns, {"competence": competence})
        return competence

    def record_assembly(self, assembly: Mapping[str, Any], *, occurred_at_ns: int) -> Mapping[str, Any]:
        if not assembly.get("integrity", {}).get("content_hash"):
            raise IntelligenceRuntimeError("assembly must be integrity-bound")
        return self._append("EXPERT_ASSEMBLY_RECORDED", occurred_at_ns, {"assembly": dict(assembly)})

    def record_synthesis(self, synthesis: Mapping[str, Any], *, occurred_at_ns: int) -> Mapping[str, Any]:
        if not synthesis.get("integrity", {}).get("content_hash"):
            raise IntelligenceRuntimeError("synthesis must be integrity-bound")
        if synthesis.get("artifact_class") != "MARKET_SYNTHESIS":
            raise IntelligenceRuntimeError("synthesis artifact_class must be MARKET_SYNTHESIS")
        state = project_runtime(self.events())
        content_hash = str(synthesis["integrity"]["content_hash"])
        synthesis_id = str(synthesis.get("synthesis_id") or "")
        for existing in state.get("syntheses") or []:
            if not isinstance(existing, Mapping):
                continue
            existing_hash = str((existing.get("integrity") or {}).get("content_hash") or "")
            if existing_hash == content_hash:
                return existing
            if synthesis_id and existing.get("synthesis_id") == synthesis_id and existing_hash != content_hash:
                raise IntelligenceRuntimeError("synthesis identity conflict")
        return self._append("MARKET_SYNTHESIS_PUBLISHED", occurred_at_ns, {"synthesis": dict(synthesis)})

    def publish(self, publication: Mapping[str, Any], *, occurred_at_ns: int) -> Mapping[str, Any]:
        validate_intelligence_publication(publication)
        return self._append("INTELLIGENCE_PUBLISHED", occurred_at_ns, {"publication": dict(publication)})

    def qualify_for_benjamin(
        self,
        publication: Mapping[str, Any],
        assembly: Mapping[str, Any],
        competence_memory: Mapping[str, Any],
        market_context,
        *,
        qualification_cutoff_ns: int,
        claims: Sequence[Mapping[str, Any]],
        data_quality: Mapping[str, Any],
        policy: Mapping[str, Any] = None,
    ) -> Mapping[str, Any]:
        cutoff = int(qualification_cutoff_ns)
        self._require_recorded_qualification_inputs(
            publication,
            assembly,
            competence_memory,
            claims,
            qualification_cutoff_ns=cutoff,
        )
        qualification = assess_benjamin_publication_qualification(
            publication,
            assembly,
            competence_memory,
            market_context,
            qualification_cutoff_ns=cutoff,
            claims=claims,
            data_quality=data_quality,
            policy=policy,
        )
        validate_benjamin_publication_qualification(qualification)
        state = project_runtime(self.events())
        existing = next((item for item in state["qualifications"] if item.get("qualification_id") == qualification["qualification_id"]), None)
        if existing is not None:
            if existing.get("integrity", {}).get("content_hash") != qualification["integrity"]["content_hash"]:
                raise IntelligenceRuntimeError("conflicting qualification identity reuse")
            handoff = None
            if existing.get("status") == "ELIGIBLE":
                handoff = self._ensure_recorded_handoff(
                    publication,
                    existing,
                    assembly,
                    competence_memory,
                    market_context,
                    claims,
                    occurred_at_ns=cutoff,
                )
            return {"qualification": existing, "handoff": handoff, "idempotent": True}
        event_type = "BENJAMIN_PUBLICATION_QUALIFIED" if qualification["status"] == "ELIGIBLE" else "BENJAMIN_PUBLICATION_BLOCKED"
        self._append(event_type, cutoff, {"qualification": dict(qualification)})
        handoff = None
        if qualification["status"] == "ELIGIBLE":
            handoff = self._ensure_recorded_handoff(
                publication,
                qualification,
                assembly,
                competence_memory,
                market_context,
                claims,
                occurred_at_ns=cutoff,
            )
        return {"qualification": qualification, "handoff": handoff, "idempotent": False}

    def record_handoff(self, handoff: Mapping[str, Any], *, occurred_at_ns: int) -> Mapping[str, Any]:
        validate_benjamin_handoff(handoff)
        recorded = self._require_recorded_eligible_qualification_for_handoff(handoff, occurred_at_ns=int(occurred_at_ns))
        state = project_runtime(self.events())
        existing = next((item for item in state["handoffs"] if item.get("handoff_id") == handoff.get("handoff_id")), None)
        if existing is not None:
            if existing.get("integrity", {}).get("content_hash") != handoff.get("integrity", {}).get("content_hash"):
                raise IntelligenceRuntimeError("conflicting handoff identity reuse")
            return existing
        if recorded.get("status") != "ELIGIBLE":
            raise IntelligenceRuntimeError("handoff cannot be published from a blocked qualification")
        return self._append("BENJAMIN_HANDOFF_PUBLISHED", int(occurred_at_ns), {"handoff": dict(handoff)})

    def _events_known_at(self, cutoff_ns: int) -> Tuple[Mapping[str, Any], ...]:
        return tuple(event for event in self.events() if int(event.get("occurred_at_ns", -1)) <= int(cutoff_ns))

    def _require_recorded_qualification_inputs(
        self,
        publication: Mapping[str, Any],
        assembly: Mapping[str, Any],
        competence_memory: Mapping[str, Any],
        claims: Sequence[Mapping[str, Any]],
        *,
        qualification_cutoff_ns: int,
    ) -> None:
        events = self._events_known_at(qualification_cutoff_ns)
        publication_hash = _content_hash(publication)
        assembly_hash = _content_hash(assembly)
        competence_hash = _content_hash(competence_memory)
        recorded_claims = {
            _content_hash(event["payload"]["claim"]): event["payload"]["claim"]
            for event in events
            if event["event_type"] == "EXPERT_CLAIM_RECORDED"
        }
        recorded_score_claim_hashes = {
            str(event["payload"]["score"].get("claim_hash") or "")
            for event in events
            if event["event_type"] == "EXPERT_SCORE_RECORDED"
        }
        recorded_competence_hashes = {
            _content_hash(event["payload"]["competence"])
            for event in events
            if event["event_type"] == "COMPETENCE_REBUILT"
        }
        recorded_assembly_hashes = {
            _content_hash(event["payload"]["assembly"])
            for event in events
            if event["event_type"] == "EXPERT_ASSEMBLY_RECORDED"
        }
        recorded_publication_hashes = {
            _content_hash(event["payload"]["publication"])
            for event in events
            if event["event_type"] == "INTELLIGENCE_PUBLISHED"
        }
        contributions = assembly.get("expert_contributions") if isinstance(assembly, Mapping) else ()
        required_claim_hashes: List[str] = []
        for contribution in contributions or ():
            if isinstance(contribution, Mapping) and contribution.get("claim_hash"):
                required_claim_hashes.append(str(contribution["claim_hash"]))
        for claim in claims:
            digest = _content_hash(claim)
            if digest:
                required_claim_hashes.append(digest)
        if not required_claim_hashes:
            raise IntelligenceRuntimeError("qualification requires recorded expert claims")
        for digest in required_claim_hashes:
            if digest not in recorded_claims:
                raise IntelligenceRuntimeError("qualification requires a recorded expert claim")
            if digest not in recorded_score_claim_hashes:
                raise IntelligenceRuntimeError("qualification requires a recorded expert score")
        if competence_hash not in recorded_competence_hashes:
            raise IntelligenceRuntimeError("qualification requires recorded competence memory")
        if assembly_hash not in recorded_assembly_hashes:
            raise IntelligenceRuntimeError("qualification requires a recorded assembly")
        if publication_hash not in recorded_publication_hashes:
            raise IntelligenceRuntimeError("qualification requires a recorded internal publication")
        provenance = publication.get("provenance") if isinstance(publication, Mapping) else None
        if not isinstance(provenance, Mapping):
            raise IntelligenceRuntimeError("publication provenance does not reference the recorded assembly")
        if provenance.get("assembly_hash") != assembly_hash:
            raise IntelligenceRuntimeError("publication provenance does not reference the recorded assembly")
        if provenance.get("competence_memory_hash") != competence_hash:
            raise IntelligenceRuntimeError("publication provenance does not reference the recorded competence memory")

    def _recorded_qualification(
        self,
        qualification_id: str,
        *,
        occurred_at_ns: int,
    ) -> Mapping[str, Any]:
        found = None
        for event in self._events_known_at(occurred_at_ns):
            if event["event_type"] not in {"BENJAMIN_PUBLICATION_QUALIFIED", "BENJAMIN_PUBLICATION_BLOCKED"}:
                continue
            qualification = event["payload"]["qualification"]
            if qualification.get("qualification_id") == qualification_id:
                found = qualification
        if found is None:
            raise IntelligenceRuntimeError("handoff requires a recorded eligible qualification")
        return found

    def _require_recorded_eligible_qualification_for_handoff(
        self,
        handoff: Mapping[str, Any],
        *,
        occurred_at_ns: int,
    ) -> Mapping[str, Any]:
        qualification = self._recorded_qualification(
            str(handoff.get("qualification_result_id") or ""),
            occurred_at_ns=occurred_at_ns,
        )
        if qualification.get("status") != "ELIGIBLE":
            raise IntelligenceRuntimeError("handoff cannot be published from a blocked qualification")
        if _content_hash(qualification) != str(handoff.get("qualification_result_hash") or ""):
            raise IntelligenceRuntimeError("handoff qualification hash mismatch")
        provenance = qualification.get("provenance") if isinstance(qualification.get("provenance"), Mapping) else {}
        if provenance.get("publication_hash") != handoff.get("internal_publication_hash"):
            raise IntelligenceRuntimeError("handoff publication lineage mismatch")
        if provenance.get("assembly_hash") != handoff.get("assembly_hash"):
            raise IntelligenceRuntimeError("handoff assembly lineage mismatch")
        policy = qualification.get("policy") if isinstance(qualification.get("policy"), Mapping) else {}
        if policy.get("policy_hash") != handoff.get("qualification_policy_hash"):
            raise IntelligenceRuntimeError("handoff policy lineage mismatch")
        return qualification

    def _ensure_recorded_handoff(
        self,
        publication: Mapping[str, Any],
        qualification: Mapping[str, Any],
        assembly: Mapping[str, Any],
        competence_memory: Mapping[str, Any],
        market_context,
        claims: Sequence[Mapping[str, Any]],
        *,
        occurred_at_ns: int,
    ) -> Mapping[str, Any]:
        if qualification.get("status") != "ELIGIBLE":
            raise IntelligenceRuntimeError("handoff cannot be published from a blocked qualification")
        recorded = self._recorded_qualification(str(qualification.get("qualification_id") or ""), occurred_at_ns=occurred_at_ns)
        if recorded.get("status") != "ELIGIBLE":
            raise IntelligenceRuntimeError("handoff cannot be published from a blocked qualification")
        if _content_hash(recorded) != _content_hash(qualification):
            raise IntelligenceRuntimeError("handoff qualification hash mismatch")
        handoff = build_benjamin_handoff(
            publication,
            recorded,
            assembly,
            competence_memory,
            market_context,
            claims,
            created_at_ns=int(occurred_at_ns),
        )
        validate_benjamin_handoff(handoff)
        recorded_event = self.record_handoff(handoff, occurred_at_ns=int(occurred_at_ns))
        if recorded_event.get("event_type") == "BENJAMIN_HANDOFF_PUBLISHED":
            return recorded_event["payload"]["handoff"]
        return recorded_event

    def rebuild_state(self) -> Mapping[str, Any]:
        with writer_lock(self.root):
            events = self.events()
            errors = validate_event_chain(events)
            if errors:
                raise IntelligenceRuntimeError("expert intelligence journal invalid: " + "; ".join(errors))
            state = project_runtime(events)
            _atomic_json(self.state_path, state)
            return state
