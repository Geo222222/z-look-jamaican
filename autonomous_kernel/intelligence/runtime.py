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


RUNTIME_SCHEMA_VERSION = 1
GENESIS = "GENESIS"
ALLOWED_EVENT_TYPES = {
    "EXPERT_CLAIM_RECORDED",
    "EXPERT_SCORE_RECORDED",
    "COMPETENCE_REBUILT",
    "EXPERT_ASSEMBLY_RECORDED",
    "INTELLIGENCE_PUBLISHED",
}


class IntelligenceRuntimeError(RuntimeError):
    pass


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
        "publications": [],
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
        elif event["event_type"] == "INTELLIGENCE_PUBLISHED":
            state["publications"].append(payload["publication"])
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

    def publish(self, publication: Mapping[str, Any], *, occurred_at_ns: int) -> Mapping[str, Any]:
        validate_intelligence_publication(publication)
        return self._append("INTELLIGENCE_PUBLISHED", occurred_at_ns, {"publication": dict(publication)})

    def rebuild_state(self) -> Mapping[str, Any]:
        with writer_lock(self.root):
            events = self.events()
            errors = validate_event_chain(events)
            if errors:
                raise IntelligenceRuntimeError("expert intelligence journal invalid: " + "; ".join(errors))
            state = project_runtime(events)
            _atomic_json(self.state_path, state)
            return state
