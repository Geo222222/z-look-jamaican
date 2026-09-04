from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from ..book_bridge import canonical_json
from ..prediction.factory import PredictionFactoryError, representation_target_price
from ..representation.contracts import RepresentationFrame
from ..representation.store import validate_representation_store
from .contracts import ExperienceTimescale, MarketExperienceFrame
from .store import ExperienceJournalCommitment


ROOT_PATH_SCHEMA_VERSION = "1.0"
ROOT_PATH_BUILDER_VERSION = "economic-root-path-v1"
ROOT_PATH_STATUSES = {"QUALIFIED", "DEGRADED", "UNAVAILABLE"}


class RootPathExperienceError(ValueError):
    pass


class RootPathStoreError(RuntimeError):
    pass


def _digest(value: str, field: str) -> str:
    text = str(value).lower()
    if len(text) != 64:
        raise RootPathExperienceError("%s must be SHA-256 hex" % field)
    try:
        int(text, 16)
    except ValueError as exc:
        raise RootPathExperienceError("%s must be hexadecimal" % field) from exc
    return text


@dataclass(frozen=True)
class RootPathPoint:
    target_at_ns: int
    frame_id: str
    frame_content_hash: str
    frame_cutoff_at_ns: int
    frame_known_at_ns: int
    midpoint: str
    midpoint_source: str

    def __post_init__(self) -> None:
        if self.target_at_ns < 0 or self.frame_cutoff_at_ns < self.target_at_ns or self.frame_known_at_ns < 0:
            raise RootPathExperienceError("root-path point timing is invalid")
        if self.frame_known_at_ns > self.frame_cutoff_at_ns:
            raise RootPathExperienceError("root-path point cannot be known after its frame cutoff")
        if not self.frame_id or not self.midpoint_source:
            raise RootPathExperienceError("root-path point identity/source is required")
        _digest(self.frame_content_hash, "root-path frame_content_hash")
        try:
            midpoint = float(self.midpoint)
        except (TypeError, ValueError) as exc:
            raise RootPathExperienceError("root-path midpoint must be numeric") from exc
        if midpoint <= 0:
            raise RootPathExperienceError("root-path midpoint must be positive")

    def to_wire(self) -> Dict[str, Any]:
        return {
            "target_at_ns": self.target_at_ns,
            "frame_id": self.frame_id,
            "frame_content_hash": self.frame_content_hash,
            "frame_cutoff_at_ns": self.frame_cutoff_at_ns,
            "frame_known_at_ns": self.frame_known_at_ns,
            "midpoint": self.midpoint,
            "midpoint_source": self.midpoint_source,
        }

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "RootPathPoint":
        return cls(
            target_at_ns=int(value.get("target_at_ns", -1)),
            frame_id=str(value.get("frame_id", "")),
            frame_content_hash=str(value.get("frame_content_hash", "")),
            frame_cutoff_at_ns=int(value.get("frame_cutoff_at_ns", -1)),
            frame_known_at_ns=int(value.get("frame_known_at_ns", -1)),
            midpoint=str(value.get("midpoint", "")),
            midpoint_source=str(value.get("midpoint_source", "")),
        )


@dataclass(frozen=True)
class EconomicRootPathExperience:
    root_path_id: str
    economic_root_id: str
    instrument_id: str
    timescale: ExperienceTimescale
    window_start_ns: int
    cutoff_at_ns: int
    known_at_ns: int
    grid_interval_ns: int
    max_point_lag_ns: int
    status: str
    baseline_experience_id: str
    baseline_experience_hash: str
    points: Tuple[RootPathPoint, ...]
    missing_target_ns: Tuple[int, ...]
    builder_version: str = ROOT_PATH_BUILDER_VERSION
    schema_version: str = ROOT_PATH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ROOT_PATH_SCHEMA_VERSION:
            raise RootPathExperienceError("unsupported root-path schema")
        for field in ("root_path_id", "economic_root_id", "instrument_id", "baseline_experience_id", "builder_version"):
            if not str(getattr(self, field)).strip():
                raise RootPathExperienceError("root-path %s is required" % field)
        _digest(self.baseline_experience_hash, "baseline_experience_hash")
        if self.window_start_ns < 0 or self.cutoff_at_ns < self.window_start_ns:
            raise RootPathExperienceError("root-path window is invalid")
        if self.known_at_ns < 0 or self.known_at_ns > self.cutoff_at_ns:
            raise RootPathExperienceError("root-path known_at is invalid")
        if self.grid_interval_ns <= 0 or self.max_point_lag_ns < 0 or self.max_point_lag_ns >= self.grid_interval_ns:
            raise RootPathExperienceError("root-path grid/lag contract is invalid")
        if (self.cutoff_at_ns - self.window_start_ns) % self.grid_interval_ns != 0:
            raise RootPathExperienceError("root-path window must divide exactly by grid interval")
        if self.status not in ROOT_PATH_STATUSES:
            raise RootPathExperienceError("root-path status is invalid")

        expected_targets = tuple(range(self.window_start_ns, self.cutoff_at_ns + 1, self.grid_interval_ns))
        point_targets = tuple(point.target_at_ns for point in self.points)
        if point_targets != tuple(sorted(point_targets)) or len(set(point_targets)) != len(point_targets):
            raise RootPathExperienceError("root-path point targets must be unique and ordered")
        if any(target not in expected_targets for target in point_targets):
            raise RootPathExperienceError("root-path point lies outside preregistered grid")
        missing = tuple(int(item) for item in self.missing_target_ns)
        if missing != tuple(sorted(missing)) or len(set(missing)) != len(missing):
            raise RootPathExperienceError("root-path missing targets must be unique and ordered")
        if any(target not in expected_targets for target in missing):
            raise RootPathExperienceError("root-path missing target lies outside preregistered grid")
        if set(point_targets).intersection(missing) or set(point_targets).union(missing) != set(expected_targets):
            raise RootPathExperienceError("root-path points and missing targets must partition the grid")
        frame_ids = [point.frame_id for point in self.points]
        if len(frame_ids) != len(set(frame_ids)):
            raise RootPathExperienceError("one representation frame cannot satisfy multiple root-path grid points")
        for point in self.points:
            if point.frame_cutoff_at_ns > point.target_at_ns + self.max_point_lag_ns:
                raise RootPathExperienceError("root-path point exceeds declared grid lag")
            if point.frame_cutoff_at_ns > self.cutoff_at_ns or point.frame_known_at_ns > self.cutoff_at_ns:
                raise RootPathExperienceError("root-path point exceeds causal cutoff")
        max_known = max((point.frame_known_at_ns for point in self.points), default=0)
        if self.known_at_ns < max_known:
            raise RootPathExperienceError("root-path known_at precedes source knowledge")
        if self.status == "QUALIFIED" and (missing or len(self.points) != len(expected_targets)):
            raise RootPathExperienceError("qualified root path requires every grid point")
        if self.status == "UNAVAILABLE" and self.points:
            raise RootPathExperienceError("unavailable root path cannot carry qualified points")
        if self.status == "DEGRADED" and (not self.points or not missing):
            raise RootPathExperienceError("degraded root path requires partial grid evidence")

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "root_path_id": self.root_path_id,
            "economic_root_id": self.economic_root_id,
            "instrument_id": self.instrument_id,
            "timescale": self.timescale.value,
            "window_start_ns": self.window_start_ns,
            "cutoff_at_ns": self.cutoff_at_ns,
            "known_at_ns": self.known_at_ns,
            "grid_interval_ns": self.grid_interval_ns,
            "max_point_lag_ns": self.max_point_lag_ns,
            "status": self.status,
            "builder_version": self.builder_version,
            "baseline_experience": {
                "experience_id": self.baseline_experience_id,
                "content_hash": self.baseline_experience_hash,
            },
            "points": [point.to_wire() for point in self.points],
            "missing_target_ns": list(self.missing_target_ns),
            "truth_boundary": {
                "point_selection": "FIRST_QUALIFIED_SAME_INSTRUMENT_FRAME_BY_CUTOFF_WITHIN_NONOVERLAPPING_GRID_LAG",
                "interpolation": False,
                "future_known_inputs": False,
                "capital_decision": False,
                "risk_authorization": False,
                "external_execution": False,
            },
        }

    def content_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.body())).hexdigest()

    def to_wire(self) -> Dict[str, Any]:
        value = self.body()
        value["integrity"] = {"algorithm": "sha256", "content_hash": self.content_hash()}
        return value

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "EconomicRootPathExperience":
        baseline = value.get("baseline_experience")
        points = value.get("points")
        if not isinstance(baseline, Mapping):
            raise RootPathExperienceError("root-path baseline experience is malformed")
        if not isinstance(points, Sequence) or isinstance(points, (str, bytes)):
            raise RootPathExperienceError("root-path points must be an array")
        item = cls(
            schema_version=str(value.get("schema_version", "")),
            root_path_id=str(value.get("root_path_id", "")),
            economic_root_id=str(value.get("economic_root_id", "")),
            instrument_id=str(value.get("instrument_id", "")),
            timescale=ExperienceTimescale(str(value.get("timescale", ""))),
            window_start_ns=int(value.get("window_start_ns", -1)),
            cutoff_at_ns=int(value.get("cutoff_at_ns", -1)),
            known_at_ns=int(value.get("known_at_ns", -1)),
            grid_interval_ns=int(value.get("grid_interval_ns", -1)),
            max_point_lag_ns=int(value.get("max_point_lag_ns", -1)),
            status=str(value.get("status", "")),
            builder_version=str(value.get("builder_version", "")),
            baseline_experience_id=str(baseline.get("experience_id", "")),
            baseline_experience_hash=str(baseline.get("content_hash", "")),
            points=tuple(RootPathPoint.from_wire(raw) for raw in points if isinstance(raw, Mapping)),
            missing_target_ns=tuple(int(raw) for raw in value.get("missing_target_ns", [])),
        )
        if len(item.points) != len(points):
            raise RootPathExperienceError("root-path point is malformed")
        truth = value.get("truth_boundary")
        if not isinstance(truth, Mapping):
            raise RootPathExperienceError("root-path truth boundary is malformed")
        if truth.get("interpolation") is not False or truth.get("future_known_inputs") is not False:
            raise RootPathExperienceError("root-path truth boundary is invalid")
        if any(truth.get(key) is not False for key in ("capital_decision", "risk_authorization", "external_execution")):
            raise RootPathExperienceError("root-path authority boundary is invalid")
        integrity = value.get("integrity")
        if not isinstance(integrity, Mapping) or integrity.get("algorithm") != "sha256" or integrity.get("content_hash") != item.content_hash():
            raise RootPathExperienceError("root-path content hash mismatch")
        return item


def _durable_representations(root: Path) -> Tuple[RepresentationFrame, ...]:
    errors = validate_representation_store(root)
    if errors:
        raise RootPathExperienceError("representation store is invalid: " + "; ".join(errors))
    index_path = root / "state/representations.json"
    if not index_path.is_file():
        raise RootPathExperienceError("representation discovery index is missing")
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RootPathExperienceError("representation discovery index is unreadable") from exc
    frames = []
    for raw in index.get("items", []):
        if not isinstance(raw, Mapping):
            raise RootPathExperienceError("representation discovery item is malformed")
        path = (root / str(raw.get("path", ""))).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise RootPathExperienceError("representation path escapes repository") from exc
        document = json.loads(path.read_text(encoding="utf-8"))
        frames.append(RepresentationFrame.from_wire(document.get("frame", {})))
    return tuple(frames)


def _short_spot_instrument(experience: MarketExperienceFrame) -> Tuple[str, int]:
    views = [view for view in experience.views if view.timescale is ExperienceTimescale.SHORT]
    if len(views) != 1:
        raise RootPathExperienceError("root path requires exactly one SHORT Market Experience view")
    view = views[0]
    if experience.status != "QUALIFIED" or view.status != "QUALIFIED":
        raise RootPathExperienceError("root path requires qualified Market Experience and SHORT view")
    spot_ids = sorted({
        ref.instrument_id
        for ref in view.source_frames
        if ref.representation_type == "INSTRUMENT_STATE"
        and ref.market_type == "SPOT"
        and ref.status == "QUALIFIED"
    })
    if len(spot_ids) != 1:
        raise RootPathExperienceError("root path requires one unambiguous qualified spot instrument")
    return spot_ids[0], view.lookback_ns


def build_economic_root_path(
    root: Path,
    baseline_experience: MarketExperienceFrame,
    *,
    grid_interval_ns: int,
    max_point_lag_ns: int,
    builder_version: str = ROOT_PATH_BUILDER_VERSION,
) -> EconomicRootPathExperience:
    """Build causal per-root price-path memory from durable Z2 history only."""
    root = root.resolve()
    restored_experience = MarketExperienceFrame.from_wire(baseline_experience.to_wire())
    instrument_id, lookback_ns = _short_spot_instrument(restored_experience)
    if grid_interval_ns <= 0 or max_point_lag_ns < 0 or max_point_lag_ns >= grid_interval_ns:
        raise RootPathExperienceError("root-path grid/lag contract is invalid")
    if lookback_ns % grid_interval_ns != 0:
        raise RootPathExperienceError("SHORT lookback must divide exactly by root-path grid interval")
    window_start = restored_experience.cutoff_at_ns - lookback_ns
    cutoff = restored_experience.cutoff_at_ns
    frames = [
        frame
        for frame in _durable_representations(root)
        if frame.instrument.canonical_id == instrument_id
        and frame.representation_type == "INSTRUMENT_STATE"
        and frame.status == "QUALIFIED"
        and frame.cutoff_at_ns <= cutoff
        and frame.known_at_ns <= cutoff
    ]
    frames.sort(key=lambda item: (item.cutoff_at_ns, item.known_at_ns, item.frame_id, item.content_hash()))

    used = set()
    points = []
    missing = []
    for target in range(window_start, cutoff + 1, grid_interval_ns):
        eligible = [
            frame
            for frame in frames
            if frame.frame_id not in used
            and frame.cutoff_at_ns >= target
            and frame.cutoff_at_ns <= target + max_point_lag_ns
        ]
        if not eligible:
            missing.append(target)
            continue
        selected = eligible[0]
        try:
            midpoint, source = representation_target_price(selected)
        except PredictionFactoryError:
            missing.append(target)
            continue
        used.add(selected.frame_id)
        points.append(
            RootPathPoint(
                target_at_ns=target,
                frame_id=selected.frame_id,
                frame_content_hash=selected.content_hash(),
                frame_cutoff_at_ns=selected.cutoff_at_ns,
                frame_known_at_ns=selected.known_at_ns,
                midpoint=midpoint,
                midpoint_source=source,
            )
        )

    expected_count = lookback_ns // grid_interval_ns + 1
    if len(points) == expected_count and not missing:
        status = "QUALIFIED"
    elif points:
        status = "DEGRADED"
    else:
        status = "UNAVAILABLE"
    known_at = max([restored_experience.known_at_ns] + [point.frame_known_at_ns for point in points])
    material = {
        "economic_root_id": restored_experience.economic_root_id,
        "instrument_id": instrument_id,
        "timescale": ExperienceTimescale.SHORT.value,
        "window_start_ns": window_start,
        "cutoff_at_ns": cutoff,
        "grid_interval_ns": grid_interval_ns,
        "max_point_lag_ns": max_point_lag_ns,
        "baseline_experience_hash": restored_experience.content_hash(),
        "points": [point.to_wire() for point in points],
        "missing_target_ns": missing,
        "builder_version": builder_version,
    }
    return EconomicRootPathExperience(
        root_path_id="ROOTPATH-%s" % hashlib.sha256(canonical_json(material)).hexdigest()[:32],
        economic_root_id=restored_experience.economic_root_id,
        instrument_id=instrument_id,
        timescale=ExperienceTimescale.SHORT,
        window_start_ns=window_start,
        cutoff_at_ns=cutoff,
        known_at_ns=known_at,
        grid_interval_ns=int(grid_interval_ns),
        max_point_lag_ns=int(max_point_lag_ns),
        status=status,
        baseline_experience_id=restored_experience.experience_id,
        baseline_experience_hash=restored_experience.content_hash(),
        points=tuple(points),
        missing_target_ns=tuple(missing),
        builder_version=builder_version,
    )


class RootPathExperienceStore:
    """Immutable temporal root paths with compact Book-commitment support."""

    JOURNAL_NAME = "ZLJ.ECONOMIC_ROOT_PATH.v1"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.snapshot_dir = self.root / "artifacts/market_experience/root_paths"
        self.journal_path = self.root / "memory/root_path_experiences.jsonl"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)

    def persist(self, path_state: EconomicRootPathExperience) -> Mapping[str, Any]:
        snapshot_path = self.snapshot_dir / (path_state.root_path_id + ".json")
        snapshot = canonical_json(path_state.to_wire())
        if snapshot_path.exists():
            existing = snapshot_path.read_bytes().rstrip(b"\n")
            if existing != snapshot:
                raise RootPathStoreError("existing root-path snapshot differs from immutable content")
        else:
            self._atomic_create(snapshot_path, snapshot + b"\n")

        existing_event = next((item for item in self._events() if item.get("root_path_id") == path_state.root_path_id), None)
        if existing_event is not None:
            if existing_event.get("content_hash") != path_state.content_hash():
                raise RootPathStoreError("root-path journal identity conflicts with content")
            return existing_event
        events = tuple(self._events())
        body = {
            "schema_version": "ZLJ.ECONOMIC_ROOT_PATH.EVENT.v1",
            "journal_name": self.JOURNAL_NAME,
            "sequence": len(events) + 1,
            "root_path_id": path_state.root_path_id,
            "content_hash": path_state.content_hash(),
            "economic_root_id": path_state.economic_root_id,
            "instrument_id": path_state.instrument_id,
            "cutoff_at_ns": path_state.cutoff_at_ns,
            "known_at_ns": path_state.known_at_ns,
            "status": path_state.status,
            "baseline_experience_hash": path_state.baseline_experience_hash,
            "previous_event_hash": "GENESIS" if not events else str(events[-1]["event_hash"]),
        }
        event = dict(body)
        event["event_hash"] = hashlib.sha256(canonical_json(body)).hexdigest()
        with self.journal_path.open("ab") as handle:
            handle.write(canonical_json(event) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event

    def load(self, root_path_id: str) -> EconomicRootPathExperience:
        path = self.snapshot_dir / (str(root_path_id) + ".json")
        if not path.is_file():
            raise KeyError(root_path_id)
        return EconomicRootPathExperience.from_wire(json.loads(path.read_text(encoding="utf-8")))

    def verify(self) -> Tuple[bool, Tuple[str, ...]]:
        errors = []
        previous = "GENESIS"
        for expected_sequence, event in enumerate(self._events(), 1):
            if event.get("sequence") != expected_sequence:
                errors.append("sequence %d mismatch" % expected_sequence)
            if event.get("previous_event_hash") != previous:
                errors.append("sequence %d previous hash mismatch" % expected_sequence)
            body = dict(event)
            claimed = str(body.pop("event_hash", ""))
            computed = hashlib.sha256(canonical_json(body)).hexdigest()
            if claimed != computed:
                errors.append("sequence %d event hash mismatch" % expected_sequence)
            root_path_id = str(event.get("root_path_id", ""))
            try:
                state = self.load(root_path_id)
                if state.content_hash() != event.get("content_hash"):
                    errors.append("sequence %d snapshot hash mismatch" % expected_sequence)
            except Exception:
                errors.append("sequence %d root-path snapshot invalid" % expected_sequence)
            previous = computed
        return not errors, tuple(errors)

    def commitment(self, *, start_sequence: int = 1, end_sequence: Optional[int] = None) -> ExperienceJournalCommitment:
        events = tuple(self._events())
        if not events:
            raise RootPathStoreError("cannot commit empty root-path journal")
        start = int(start_sequence)
        end = len(events) if end_sequence is None else int(end_sequence)
        if start <= 0 or end < start or end > len(events):
            raise RootPathStoreError("invalid root-path commitment range")
        selected = events[start - 1:end]
        hashes = [str(item["event_hash"]) for item in selected]
        last = selected[-1]
        return ExperienceJournalCommitment(
            journal_name=self.JOURNAL_NAME,
            start_sequence=start,
            end_sequence=end,
            event_count=len(selected),
            first_event_hash=hashes[0],
            last_event_hash=hashes[-1],
            range_digest=hashlib.sha256(canonical_json(hashes)).hexdigest(),
            last_experience_id=str(last["root_path_id"]),
            last_cutoff_at_ns=int(last["cutoff_at_ns"]),
            known_at_ns=max(int(item["known_at_ns"]) for item in selected),
        )

    def _events(self) -> Iterable[Dict[str, Any]]:
        if not self.journal_path.is_file():
            return ()
        output = []
        for line in self.journal_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise RootPathStoreError("root-path journal entry must be an object")
                output.append(value)
        return tuple(output)

    @staticmethod
    def _atomic_create(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists():
                raise FileExistsError(path)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
