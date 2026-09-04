from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Iterable, List, Mapping

from ..operations import canonical_hash
from .adapters import COINBASE_PROVIDER, ProviderRecord, adapt_coinbase_advanced_trade
from .contracts import CanonicalObservation
from .store import CanonicalBatchStore


class CanonicalMaterializationError(RuntimeError):
    pass


def materialize_coinbase_stream(
    root: Path,
    stream_id: str,
    *,
    default_symbol: str,
) -> Mapping[str, object]:
    """Rebuild a canonical batch from one immutable Coinbase stream bundle."""
    root = root.resolve()
    manifest_path = root / "artifacts/market_data/streams" / (stream_id + ".manifest.json")
    if not manifest_path.is_file():
        raise CanonicalMaterializationError("stream manifest is missing: %s" % stream_id)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("stream_id") != stream_id:
        raise CanonicalMaterializationError("stream manifest identity mismatch")
    data_path = (root / str(manifest.get("compressed_path", ""))).resolve()
    try:
        data_path.relative_to(root)
    except ValueError as exc:
        raise CanonicalMaterializationError("stream path escapes repository") from exc
    if not data_path.is_file():
        raise CanonicalMaterializationError("stream bundle is missing")
    compressed = data_path.read_bytes()
    if hashlib.sha256(compressed).hexdigest() != manifest.get("compressed_sha256"):
        raise CanonicalMaterializationError("compressed stream hash mismatch")
    raw = gzip.decompress(compressed)
    if hashlib.sha256(raw).hexdigest() != manifest.get("journal_sha256"):
        raise CanonicalMaterializationError("stream journal hash mismatch")

    observations: List[CanonicalObservation] = []
    raw_ref = data_path.relative_to(root).as_posix()
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CanonicalMaterializationError("stream record %d is invalid JSON" % line_number) from exc
        message = record.get("message")
        if not isinstance(message, dict):
            raise CanonicalMaterializationError("stream record %d lacks message" % line_number)
        message_hash = str(record.get("message_hash", ""))
        if canonical_hash(message) != message_hash:
            raise CanonicalMaterializationError("stream record %d message hash mismatch" % line_number)
        provider_record = ProviderRecord(
            provider=COINBASE_PROVIDER,
            stream_id=stream_id,
            received_at_ns=int(record.get("received_at_ns", -1)),
            message=message,
            message_hash=message_hash,
            raw_ref="%s#line=%d" % (raw_ref, line_number),
        )
        observations.extend(
            adapt_coinbase_advanced_trade(
                provider_record,
                default_symbol=default_symbol,
            )
        )
    if not observations:
        raise CanonicalMaterializationError("stream contains no canonical market observations")

    return CanonicalBatchStore(root).persist_batch(
        batch_id="CAN-%s" % stream_id,
        observations=tuple(observations),
        source_ref=raw_ref,
        source_sha256=str(manifest["journal_sha256"]),
    )


def main(argv: Iterable[str] = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize ZLJ canonical observations from immutable stream evidence")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--stream-id", required=True)
    parser.add_argument("--instrument", required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    manifest = materialize_coinbase_stream(
        args.root,
        args.stream_id,
        default_symbol=args.instrument,
    )
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
