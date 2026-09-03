from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Tuple

from ..observation.contracts import CanonicalObservation, ObservationContractError
from ..operations import canonical_hash
from .builder import RepresentationError, build_instrument_state
from .store import RepresentationStore


class RepresentationMaterializationError(RuntimeError):
    pass


def _safe_id(value: str, field: str) -> str:
    if not value or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in value):
        raise RepresentationMaterializationError("%s must be non-empty and file-safe" % field)
    return value


def load_canonical_batch(root: Path, batch_id: str) -> Tuple[Mapping[str, object], Tuple[CanonicalObservation, ...]]:
    root = root.resolve()
    batch_id = _safe_id(batch_id, "batch_id")
    manifest_path = root / "artifacts/market_data/canonical" / (batch_id + ".manifest.json")
    if not manifest_path.is_file():
        raise RepresentationMaterializationError("canonical batch manifest is missing: %s" % batch_id)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    body = {key: value for key, value in manifest.items() if key != "integrity"}
    if manifest.get("batch_id") != batch_id:
        raise RepresentationMaterializationError("canonical batch identity mismatch")
    if canonical_hash(body) != manifest.get("integrity", {}).get("content_hash"):
        raise RepresentationMaterializationError("canonical batch manifest integrity mismatch")
    data_path = (root / str(manifest.get("path", ""))).resolve()
    try:
        data_path.relative_to(root)
    except ValueError as exc:
        raise RepresentationMaterializationError("canonical batch path escapes repository") from exc
    if not data_path.is_file():
        raise RepresentationMaterializationError("canonical batch data is missing")
    compressed = data_path.read_bytes()
    if hashlib.sha256(compressed).hexdigest() != manifest.get("compressed_sha256"):
        raise RepresentationMaterializationError("canonical batch compressed hash mismatch")
    try:
        raw = gzip.decompress(compressed)
    except gzip.BadGzipFile as exc:
        raise RepresentationMaterializationError("canonical batch is not valid gzip") from exc
    if hashlib.sha256(raw).hexdigest() != manifest.get("canonical_jsonl_sha256"):
        raise RepresentationMaterializationError("canonical batch JSONL hash mismatch")

    observations: List[CanonicalObservation] = []
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            observations.append(CanonicalObservation.from_wire(json.loads(line)))
        except (json.JSONDecodeError, ObservationContractError, ValueError, TypeError) as exc:
            raise RepresentationMaterializationError(
                "canonical observation %d is invalid: %s" % (line_number, exc)
            ) from exc
    if len(observations) != int(manifest.get("record_count", -1)):
        raise RepresentationMaterializationError("canonical batch record count mismatch")
    return manifest, tuple(observations)


def materialize_instrument_state(
    root: Path,
    *,
    batch_ids: Iterable[str],
    instrument_id: str,
    cutoff_at_ns: Optional[int] = None,
    depth_bands_bps: Tuple[int, ...] = (1, 5, 10),
) -> Mapping[str, object]:
    root = root.resolve()
    selected: List[CanonicalObservation] = []
    source_batches = []
    for batch_id in batch_ids:
        manifest, observations = load_canonical_batch(root, str(batch_id))
        matching = [item for item in observations if item.instrument.canonical_id == instrument_id]
        if cutoff_at_ns is not None:
            matching = [item for item in matching if item.known_at_ns <= int(cutoff_at_ns)]
        if matching:
            selected.extend(matching)
            source_batches.append(
                {
                    "batch_id": str(manifest["batch_id"]),
                    "manifest_ref": "artifacts/market_data/canonical/%s.manifest.json" % manifest["batch_id"],
                    "manifest_content_hash": str(manifest["integrity"]["content_hash"]),
                }
            )
    if not selected:
        raise RepresentationMaterializationError("no canonical observations match the requested instrument/cutoff")
    try:
        frame = build_instrument_state(
            tuple(selected),
            cutoff_at_ns=cutoff_at_ns,
            depth_bands_bps=depth_bands_bps,
        )
    except RepresentationError as exc:
        raise RepresentationMaterializationError(str(exc)) from exc
    return RepresentationStore(root).persist(frame, source_batches=tuple(source_batches))


def main(argv: Iterable[str] = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize ZLJ Z2 point-in-time instrument state")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--batch-id", action="append", required=True)
    parser.add_argument("--instrument-id", required=True)
    parser.add_argument("--cutoff-at-ns", type=int)
    parser.add_argument("--depth-band-bps", action="append", type=int)
    args = parser.parse_args(list(argv) if argv is not None else None)
    bands = tuple(args.depth_band_bps) if args.depth_band_bps else (1, 5, 10)
    artifact = materialize_instrument_state(
        args.root,
        batch_ids=tuple(args.batch_id),
        instrument_id=args.instrument_id,
        cutoff_at_ns=args.cutoff_at_ns,
        depth_bands_bps=bands,
    )
    print(json.dumps(artifact, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
