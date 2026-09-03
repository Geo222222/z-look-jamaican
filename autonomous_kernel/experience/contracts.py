from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Mapping, Sequence, Tuple


EXPERIENCE_SCHEMA_VERSION = "1.0"
EXPERIENCE_STATUSES = {"QUALIFIED", "DEGRADED", "UNAVAILABLE"}
FEATURE_FAMILY_STATUSES = {"QUALIFIED", "DEGRADED", "UNAVAILABLE"}


class MarketExperienceError(ValueError):
    pass


class ExperienceTimescale(str, Enum):
    MICRO = "MICRO"
    SHORT = "SHORT"
    SESSION = "SESSION"
    MACRO_STRUCTURAL = "MACRO_STRUCTURAL"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _digest(value: str, field: str) -> str:
    text = str(value).lower()
    if len(text) != 64:
        raise MarketExperienceError(f"{field} must be SHA-256 hex")
    try:
        int(text, 16)
    except ValueError as exc:
        raise MarketExperienceError(f"{field} must be hexadecimal") from exc
    return text


def _unique_strings(values: Sequence[str], field: str) -> Tuple[str, ...]:
    result = tuple(str(value) for value in values)
    if any(not value for value in result) or len(set(result)) != len(result):
        raise MarketExperienceError(f"{field} must contain unique non-empty values")
    return result


@dataclass(frozen=True)
class ExperienceSourceFrame:
    frame_id: str
    frame_hash: str
    instrument_id: str
    market_type: str
    window_start_ns: int
    cutoff_at_ns: int
    known_at_ns: int
    status: str

    def __post_init__(self) -> None:
        if not self.frame_id or not self.instrument_id or not self.market_type:
            raise MarketExperienceError("experience source-frame identity is required")
        _digest(self.frame_hash, "frame_hash")
        if self.status not in EXPERIENCE_STATUSES:
            raise MarketExperienceError("source frame status is invalid")
        if self.window_start_ns < 0 or self.cutoff_at_ns < self.window_start_ns:
            raise MarketExperienceError("source frame window is invalid")
        if self.known_at_ns < self.window_start_ns or self.known_at_ns > self.cutoff_at_ns:
            raise MarketExperienceError("source frame known_at is invalid")

    def body(self) -> Dict[str, object]:
        return {
            "frame_id": self.frame_id,
            "frame_hash": self.frame_hash,
            "instrument_id": self.instrument_id,
            "market_type": self.market_type,
            "window_start_ns": self.window_start_ns,
            "cutoff_at_ns": self.cutoff_at_ns,
            "known_at_ns": self.known_at_ns,
            "status": self.status,
        }


@dataclass(frozen=True)
class ExperienceView:
    timescale: ExperienceTimescale
    lookback_ns: int
    window_start_ns: int
    cutoff_at_ns: int
    status: str
    source_frames: Tuple[ExperienceSourceFrame, ...]
    feature_family_status: Mapping[str, str]

    def __post_init__(self) -> None:
        if self.lookback_ns <= 0:
            raise MarketExperienceError("experience lookback_ns must be positive")
        if self.window_start_ns < 0 or self.cutoff_at_ns < self.window_start_ns:
            raise MarketExperienceError("experience view window is invalid")
        if self.cutoff_at_ns - self.window_start_ns != self.lookback_ns:
            raise MarketExperienceError("experience view lookback must equal cutoff minus start")
        if self.status not in EXPERIENCE_STATUSES:
            raise MarketExperienceError("experience view status is invalid")
        ids = [item.frame_id for item in self.source_frames]
        if len(ids) != len(set(ids)):
            raise MarketExperienceError("experience view source frame ids must be unique")
        if not isinstance(self.feature_family_status, Mapping) or not self.feature_family_status:
            raise MarketExperienceError("experience view requires feature-family status")
        for family, status in self.feature_family_status.items():
            if not str(family).strip() or str(status) not in FEATURE_FAMILY_STATUSES:
                raise MarketExperienceError("experience feature-family status is invalid")
        if self.status == "QUALIFIED" and not self.source_frames:
            raise MarketExperienceError("qualified experience view requires source frames")

    def body(self) -> Dict[str, object]:
        return {
            "timescale": self.timescale.value,
            "lookback_ns": self.lookback_ns,
            "window_start_ns": self.window_start_ns,
            "cutoff_at_ns": self.cutoff_at_ns,
            "status": self.status,
            "source_frames": [item.body() for item in sorted(self.source_frames, key=lambda frame: frame.frame_id)],
            "feature_family_status": dict(sorted((str(k), str(v)) for k, v in self.feature_family_status.items())),
        }


@dataclass(frozen=True)
class MarketExperienceFrame:
    experience_id: str
    economic_root_id: str
    cutoff_at_ns: int
    known_at_ns: int
    status: str
    builder_version: str
    graph_id: str
    graph_version: str
    graph_hash: str
    context_id: str
    context_hash: str
    context_status: str
    views: Tuple[ExperienceView, ...]
    schema_version: str = EXPERIENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EXPERIENCE_SCHEMA_VERSION:
            raise MarketExperienceError("unsupported Market Experience schema")
        if not self.experience_id or not self.economic_root_id or not self.builder_version:
            raise MarketExperienceError("experience identity fields are required")
        if self.cutoff_at_ns < 0 or self.known_at_ns < 0 or self.known_at_ns > self.cutoff_at_ns:
            raise MarketExperienceError("experience timing is invalid")
        if self.status not in EXPERIENCE_STATUSES or self.context_status not in EXPERIENCE_STATUSES:
            raise MarketExperienceError("experience/context status is invalid")
        if not self.graph_id or not self.graph_version or not self.context_id:
            raise MarketExperienceError("graph/context identity is required")
        _digest(self.graph_hash, "graph_hash")
        _digest(self.context_hash, "context_hash")
        if not self.views:
            raise MarketExperienceError("Market Experience requires at least one timescale view")
        timescales = [view.timescale for view in self.views]
        if len(timescales) != len(set(timescales)):
            raise MarketExperienceError("experience timescales must be unique")
        for view in self.views:
            if view.cutoff_at_ns != self.cutoff_at_ns:
                raise MarketExperienceError("all experience views must share the experience cutoff")
            for source in view.source_frames:
                if source.cutoff_at_ns > self.cutoff_at_ns or source.known_at_ns > self.cutoff_at_ns:
                    raise MarketExperienceError("lookahead source frame rejected")
        max_known = max(
            [source.known_at_ns for view in self.views for source in view.source_frames] or [0]
        )
        if self.known_at_ns < max_known:
            raise MarketExperienceError("experience known_at cannot precede source knowledge")

    def source_set_hash(self) -> str:
        sources = []
        for view in sorted(self.views, key=lambda item: item.timescale.value):
            for source in sorted(view.source_frames, key=lambda item: item.frame_id):
                sources.append(
                    {
                        "timescale": view.timescale.value,
                        "frame_id": source.frame_id,
                        "frame_hash": source.frame_hash,
                    }
                )
        return _sha256(sources)

    def body(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "experience_id": self.experience_id,
            "economic_root_id": self.economic_root_id,
            "cutoff_at_ns": self.cutoff_at_ns,
            "known_at_ns": self.known_at_ns,
            "status": self.status,
            "builder_version": self.builder_version,
            "economic_graph": {
                "graph_id": self.graph_id,
                "graph_version": self.graph_version,
                "content_hash": self.graph_hash,
            },
            "market_context": {
                "context_id": self.context_id,
                "content_hash": self.context_hash,
                "status": self.context_status,
            },
            "views": [view.body() for view in sorted(self.views, key=lambda item: item.timescale.value)],
            "lineage": {"source_set_hash": self.source_set_hash()},
            "authority": {
                "capital_decision": False,
                "risk_authorization": False,
                "external_execution": False,
            },
        }

    def content_hash(self) -> str:
        return _sha256(self.body())

    def to_wire(self) -> Dict[str, object]:
        value = self.body()
        value["integrity"] = {"algorithm": "sha256", "content_hash": self.content_hash()}
        return value

    @classmethod
    def from_wire(cls, value: Mapping[str, object]) -> "MarketExperienceFrame":
        graph = value.get("economic_graph")
        context = value.get("market_context")
        views_raw = value.get("views")
        if not isinstance(graph, Mapping) or not isinstance(context, Mapping):
            raise MarketExperienceError("experience graph/context envelope is malformed")
        if not isinstance(views_raw, Sequence) or isinstance(views_raw, (str, bytes)):
            raise MarketExperienceError("experience views must be an array")
        views = []
        for raw in views_raw:
            if not isinstance(raw, Mapping):
                raise MarketExperienceError("experience view is malformed")
            frames_raw = raw.get("source_frames", [])
            if not isinstance(frames_raw, Sequence) or isinstance(frames_raw, (str, bytes)):
                raise MarketExperienceError("experience source frames must be an array")
            frames = []
            for frame in frames_raw:
                if not isinstance(frame, Mapping):
                    raise MarketExperienceError("experience source frame is malformed")
                frames.append(
                    ExperienceSourceFrame(
                        frame_id=str(frame.get("frame_id", "")),
                        frame_hash=str(frame.get("frame_hash", "")),
                        instrument_id=str(frame.get("instrument_id", "")),
                        market_type=str(frame.get("market_type", "")),
                        window_start_ns=int(frame.get("window_start_ns", -1)),
                        cutoff_at_ns=int(frame.get("cutoff_at_ns", -1)),
                        known_at_ns=int(frame.get("known_at_ns", -1)),
                        status=str(frame.get("status", "")),
                    )
                )
            feature_status = raw.get("feature_family_status")
            views.append(
                ExperienceView(
                    timescale=ExperienceTimescale(str(raw.get("timescale", ""))),
                    lookback_ns=int(raw.get("lookback_ns", -1)),
                    window_start_ns=int(raw.get("window_start_ns", -1)),
                    cutoff_at_ns=int(raw.get("cutoff_at_ns", -1)),
                    status=str(raw.get("status", "")),
                    source_frames=tuple(frames),
                    feature_family_status=feature_status if isinstance(feature_status, Mapping) else {},
                )
            )
        item = cls(
            schema_version=str(value.get("schema_version", "")),
            experience_id=str(value.get("experience_id", "")),
            economic_root_id=str(value.get("economic_root_id", "")),
            cutoff_at_ns=int(value.get("cutoff_at_ns", -1)),
            known_at_ns=int(value.get("known_at_ns", -1)),
            status=str(value.get("status", "")),
            builder_version=str(value.get("builder_version", "")),
            graph_id=str(graph.get("graph_id", "")),
            graph_version=str(graph.get("graph_version", "")),
            graph_hash=str(graph.get("content_hash", "")),
            context_id=str(context.get("context_id", "")),
            context_hash=str(context.get("content_hash", "")),
            context_status=str(context.get("status", "")),
            views=tuple(views),
        )
        lineage = value.get("lineage")
        if not isinstance(lineage, Mapping) or lineage.get("source_set_hash") != item.source_set_hash():
            raise MarketExperienceError("experience source_set_hash mismatch")
        authority = value.get("authority")
        if not isinstance(authority, Mapping) or any(authority.get(key) is not False for key in ("capital_decision", "risk_authorization", "external_execution")):
            raise MarketExperienceError("experience authority boundary is invalid")
        integrity = value.get("integrity")
        if not isinstance(integrity, Mapping) or integrity.get("content_hash") != item.content_hash():
            raise MarketExperienceError("Market Experience content hash mismatch")
        return item
