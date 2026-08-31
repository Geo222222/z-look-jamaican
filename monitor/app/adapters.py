from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

SENSITIVE_PARTS = {
    '.env', 'secret', 'secrets', 'private', 'private_key', 'seed', 'mnemonic',
    'credential', 'credentials', 'keystore', 'keyfile', 'signer-secret'
}
ALLOWED_SUFFIXES = {'.json', '.jsonl', '.ndjson', '.log', '.txt', '.yaml', '.yml'}
DEFAULT_SCAN_DIRS = ('state', 'runtime', 'evidence', 'experiments', 'memory', 'logs', 'artifacts', 'receipts', 'metrics')
MAX_FILE_BYTES = int(os.getenv('ZLOOK_MONITOR_MAX_FILE_BYTES', str(8 * 1024 * 1024)))
MAX_RECORDS_PER_FILE = int(os.getenv('ZLOOK_MONITOR_MAX_RECORDS_PER_FILE', '5000'))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') if dt else None


def parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def first(obj: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in obj and obj[key] not in (None, ''):
            return obj[key]
    return None


def safe_text(value: Any, limit: int = 600) -> str:
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = str(value).replace('\x00', '').strip()
    return text[:limit]


def has_secret_shape(obj: dict[str, Any]) -> bool:
    for key in obj:
        low = key.lower().replace('-', '_')
        if any(token in low for token in ('private_key', 'seed_phrase', 'mnemonic', 'secret_key', 'api_key_secret')):
            return True
    return False


def redacted_record(obj: dict[str, Any]) -> dict[str, Any]:
    if has_secret_shape(obj):
        return {'_redacted': True, 'reason': 'secret-shaped record'}
    clean: dict[str, Any] = {}
    for key, value in obj.items():
        low = key.lower().replace('-', '_')
        if any(token in low for token in ('password', 'secret', 'private_key', 'seed', 'mnemonic', 'credential')):
            clean[key] = '[REDACTED]'
        else:
            clean[key] = value
    return clean


def path_is_sensitive(path: Path) -> bool:
    for part in path.parts:
        p = part.lower().replace('-', '_')
        if p.startswith('.') and p != '.github':
            return True
        if any(token.replace('-', '_') in p for token in SENSITIVE_PARTS):
            return True
    return False


@dataclass
class SourceFile:
    path: str
    size: int
    mtime: str | None
    sha256: str
    kind: str
    records: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class Snapshot:
    generated_at: str
    source_root: str
    source_files: list[SourceFile]
    records: list[dict[str, Any]]
    logs: list[dict[str, Any]]
    treasury: dict[str, Any]
    governor_text: str
    scan_errors: list[str]

    def provenance(self) -> list[dict[str, Any]]:
        return [sf.__dict__ for sf in self.source_files]


class RepositoryReader:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _candidate_files(self) -> Iterable[Path]:
        for dirname in DEFAULT_SCAN_DIRS:
            base = self.root / dirname
            if not base.exists() or not base.is_dir():
                continue
            for path in base.rglob('*'):
                if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES and not path_is_sensitive(path.relative_to(self.root)):
                    yield path
        for exact in (
            self.root / 'config' / 'treasury_destinations.yaml',
            self.root / 'config' / 'treasury_destinations.yml',
            self.root / 'config' / 'governor.yaml',
            self.root / 'config' / 'governor.yml',
        ):
            if exact.exists() and exact.is_file():
                yield exact

    def _source_meta(self, path: Path, raw: bytes, kind: str) -> SourceFile:
        stat = path.stat()
        return SourceFile(
            path=str(path.relative_to(self.root)).replace('\\', '/'),
            size=len(raw),
            mtime=iso(datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)),
            sha256=hashlib.sha256(raw).hexdigest(),
            kind=kind,
        )

    def _read_jsonish(self, path: Path, raw: bytes, source: SourceFile) -> list[dict[str, Any]]:
        text = raw.decode('utf-8', errors='replace')
        out: list[dict[str, Any]] = []
        try:
            if path.suffix.lower() == '.json':
                payload = json.loads(text)
                items = payload if isinstance(payload, list) else [payload]
                for item in items[:MAX_RECORDS_PER_FILE]:
                    if isinstance(item, dict):
                        rec = redacted_record(item)
                        rec['_source'] = source.path
                        out.append(rec)
            else:
                for i, line in enumerate(text.splitlines()):
                    if i >= MAX_RECORDS_PER_FILE:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict):
                        rec = redacted_record(item)
                        rec['_source'] = source.path
                        out.append(rec)
        except Exception as exc:
            source.errors.append(f'{type(exc).__name__}: {exc}')
        source.records = len(out)
        return out

    def _read_log(self, path: Path, raw: bytes, source: SourceFile) -> list[dict[str, Any]]:
        lines = raw.decode('utf-8', errors='replace').splitlines()[-1000:]
        source.records = len(lines)
        out = []
        for line in lines:
            if re.search(r'(?i)(private[_ -]?key|seed phrase|mnemonic|password\s*=|secret\s*=)', line):
                line = '[REDACTED SENSITIVE LOG LINE]'
            out.append({'source': source.path, 'line': line[:2000]})
        return out

    def scan(self) -> Snapshot:
        records: list[dict[str, Any]] = []
        logs: list[dict[str, Any]] = []
        source_files: list[SourceFile] = []
        errors: list[str] = []
        treasury: dict[str, Any] = {}
        governor_text = ''

        try:
            files = list(dict.fromkeys(self._candidate_files()))
        except Exception as exc:
            files = []
            errors.append(f'candidate_scan: {type(exc).__name__}: {exc}')

        for path in files:
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    errors.append(f'{path.relative_to(self.root)} exceeds monitor file-size limit')
                    continue
                raw = path.read_bytes()
                suffix = path.suffix.lower()
                kind = 'log' if suffix in {'.log', '.txt'} else suffix.lstrip('.')
                source = self._source_meta(path, raw, kind)
                source_files.append(source)
                rel = source.path
                if rel.startswith('config/treasury_destinations.'):
                    try:
                        payload = yaml.safe_load(raw.decode('utf-8')) or {}
                        treasury = payload if isinstance(payload, dict) else {}
                        source.records = len(treasury.get('destinations', [])) if isinstance(treasury, dict) else 0
                    except Exception as exc:
                        source.errors.append(f'{type(exc).__name__}: {exc}')
                    continue
                if rel.startswith('config/governor.'):
                    try:
                        payload = yaml.safe_load(raw.decode('utf-8')) or {}
                        rec = payload if isinstance(payload, dict) else {'value': payload}
                        rec['_source'] = rel
                        rec['_kind'] = 'governor'
                        records.append(rec)
                        source.records = 1
                    except Exception as exc:
                        source.errors.append(f'{type(exc).__name__}: {exc}')
                    continue
                if suffix in {'.json', '.jsonl', '.ndjson'}:
                    records.extend(self._read_jsonish(path, raw, source))
                elif suffix in {'.log', '.txt'}:
                    logs.extend(self._read_log(path, raw, source))
                elif suffix in {'.yaml', '.yml'}:
                    try:
                        payload = yaml.safe_load(raw.decode('utf-8')) or {}
                        if isinstance(payload, dict):
                            rec = redacted_record(payload)
                            rec['_source'] = rel
                            records.append(rec)
                            source.records = 1
                    except Exception as exc:
                        source.errors.append(f'{type(exc).__name__}: {exc}')
            except Exception as exc:
                errors.append(f'{path}: {type(exc).__name__}: {exc}')

        gov_path = self.root / 'docs' / 'GOVERNOR.md'
        if gov_path.exists() and gov_path.is_file() and not path_is_sensitive(gov_path.relative_to(self.root)):
            try:
                raw = gov_path.read_bytes()
                governor_text = raw.decode('utf-8', errors='replace')[:20000]
                source_files.append(self._source_meta(gov_path, raw, 'markdown'))
            except Exception as exc:
                errors.append(f'docs/GOVERNOR.md: {type(exc).__name__}: {exc}')

        return Snapshot(
            generated_at=iso(utcnow()) or '',
            source_root=str(self.root),
            source_files=source_files,
            records=records,
            logs=logs,
            treasury=treasury,
            governor_text=governor_text,
            scan_errors=errors,
        )


def record_timestamp(rec: dict[str, Any]) -> datetime | None:
    for key in ('timestamp', 'ts', 'observed_at', 'created_at', 'updated_at', 'event_time', 'time', 'heartbeat_at', 'last_heartbeat'):
        dt = parse_ts(rec.get(key))
        if dt:
            return dt
    return None


def record_id(rec: dict[str, Any]) -> str:
    return safe_text(first(rec, 'experiment_id', 'opportunity_id', 'event_id', 'id', 'task_id', 'receipt_id', 'deployment_id'), 120)


def classify(rec: dict[str, Any]) -> str:
    source = str(rec.get('_source', '')).lower()
    explicit = safe_text(first(rec, 'kind', 'type', 'event_type', 'category', '_kind'), 80).lower()
    hay = f'{source} {explicit}'
    if 'heartbeat' in hay:
        return 'heartbeat'
    if 'opportun' in hay:
        return 'opportunity'
    if any(token in explicit for token in ('evidence', 'receipt', 'observation', 'sample')) or any(token in source for token in ('evidence/', 'receipts/')):
        return 'evidence'
    if 'experiment' in hay:
        return 'experiment'
    if 'reflection' in hay:
        return 'reflection'
    if 'wallet' in hay:
        return 'wallet'
    if 'deployment' in hay or 'release' in hay:
        return 'deployment'
    if any(token in hay for token in ('revenue', 'profit', 'pnl', 'accounting', 'ledger')):
        return 'economic'
    if 'governor' in hay or 'policy' in hay:
        return 'governor'
    return explicit or 'record'


def title_for(rec: dict[str, Any]) -> str:
    return safe_text(first(rec, 'title', 'name', 'description', 'summary', 'message', 'hypothesis', 'mechanism', 'event'), 220) or record_id(rec) or 'Untitled record'


def status_for(rec: dict[str, Any]) -> str:
    return safe_text(first(rec, 'status', 'state', 'stage', 'result_status', 'outcome'), 80).upper() or 'OBSERVED'


def score_for(rec: dict[str, Any]) -> float | None:
    for key in ('score', 'opportunity_score', 'priority_score', 'rank_score', 'expected_value_score'):
        value = rec.get(key)
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            pass
    return None


def public_wallet(rec: dict[str, Any]) -> dict[str, Any] | None:
    addr = first(rec, 'public_address', 'address', 'wallet_address')
    if not isinstance(addr, str) or len(addr.strip()) < 8:
        return None
    if has_secret_shape(rec):
        return None
    return {
        'id': safe_text(first(rec, 'wallet_id', 'id', 'name'), 100) or addr[:10],
        'asset': safe_text(first(rec, 'asset', 'symbol', 'currency'), 20) or 'UNKNOWN',
        'network': safe_text(first(rec, 'network', 'chain'), 80) or 'unknown',
        'address': addr,
        'status': status_for(rec),
        'balance': first(rec, 'balance', 'balance_native', 'amount'),
        'source': rec.get('_source', ''),
    }
