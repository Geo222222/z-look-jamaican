"""Checksum-verified same-exchange spot/perpetual mechanism falsification.

This module is intentionally specific to EXP-HISTORICAL-MECHANISMS-002.  It is
not a generic backtesting framework and it grants no execution authority.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import io
import json
import math
import os
import statistics
import time
import urllib.error
import urllib.request
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple


EXPERIMENT_ID = "EXP-HISTORICAL-MECHANISMS-002"
PARENT_EXPERIMENT_ID = "EXP-HISTORICAL-MECHANISMS-001"
PREREGISTRATION = Path("artifacts/evidence/market/exp-historical-mechanisms-002-preregistration.json")
PREREGISTRATION_SHA256 = "0574aafc7d3e676681758c0d7e7ccf1e5de5eec43702775b74046dbe31c842bf"
PARENT_PREREGISTRATION_SHA256 = "d4bd986b9b6a97dad281ebe87cad9c9ca3d86c08ae5834c7a78cfce8d39a1bc2"
DATA_ROOT = Path("artifacts/market_data/historical/binance")
MANIFEST = Path("artifacts/market_data/historical/binance-spot-perpetual-5m-2025-08_2026-07.manifest.json")
MANIFEST_SHA256 = "7c80252ade4e9742cf05d25af28be465e98252ce74033ff52b72e6e8a297b5d4"
RESULT = Path("artifacts/evidence/market/exp-historical-mechanisms-002-result.json")
REPORT = Path("artifacts/evidence/market/exp-historical-mechanisms-002-report.md")
INTERVAL_MS = 5 * 60 * 1000
DAY_MS = 24 * 60 * 60 * 1000
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
SURFACES = {
    "spot_klines": "https://data.binance.vision/data/spot/monthly/klines/{symbol}/5m/{symbol}-5m-{month}.zip",
    "perpetual_klines": "https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/5m/{symbol}-5m-{month}.zip",
    "premium_index_klines": "https://data.binance.vision/data/futures/um/monthly/premiumIndexKlines/{symbol}/5m/{symbol}-5m-{month}.zip",
    "funding_rates": "https://data.binance.vision/data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip",
}
FAMILIES = {
    "CONFIRMED-FLOW-CONTINUATION-30M": {"holding_bars": 6, "targets": ("spot", "perpetual")},
    "PERPETUAL-LEADS-SPOT-30M": {"holding_bars": 6, "targets": ("spot",)},
    "SPOT-LEADS-PERPETUAL-30M": {"holding_bars": 6, "targets": ("perpetual",)},
    "BASIS-CONVERGENCE-6H": {"holding_bars": 72, "targets": ("long_spot_short_perpetual",)},
    "CONFIRMED-SLOW-BREAKOUT-6H": {"holding_bars": 72, "targets": ("spot", "perpetual")},
}
COSTS_BPS = (5, 10, 20, 40)
PRIMARY_COST_BPS = 20
MULTIPLICITY = 14


class Kline(NamedTuple):
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float
    trade_count: int
    taker_buy_quote_volume: float


class AlignedBar(NamedTuple):
    timestamp_ms: int
    spot_open: float
    spot_close: float
    spot_return: float
    spot_imbalance: float
    spot_quote_volume: float
    perpetual_open: float
    perpetual_close: float
    perpetual_return: float
    perpetual_imbalance: float
    perpetual_quote_volume: float
    premium_index: float
    log_basis: float
    basis_change: float
    spot_breakout: bool
    perpetual_breakout: bool


class Trade(NamedTuple):
    fold_id: str
    entry_timestamp_ms: int
    exit_timestamp_ms: int
    gross_return: float
    funding_sum: float


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def iso_timestamp(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_timestamp_ms(value: object) -> int:
    timestamp = int(str(value))
    if timestamp >= 10**15:
        timestamp //= 1000
    elif timestamp < 10**12:
        raise ValueError(f"unsupported timestamp precision: {value}")
    if not (10**12 <= timestamp < 10**14):
        raise ValueError(f"timestamp is outside expected millisecond range: {value}")
    return timestamp


def month_range(first: str = "2025-08", last: str = "2026-07") -> List[str]:
    year, month = map(int, first.split("-"))
    end_year, end_month = map(int, last.split("-"))
    values = []
    while (year, month) <= (end_year, end_month):
        values.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return values


def _request_bytes(url: str, timeout: int = 90, attempts: int = 3) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "z-look-jamaican-historical-research/1.0", "Cache-Control": "no-cache"},
    )
    error: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            error = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {error}")


def _expected_member(url: str) -> str:
    return url.rsplit("/", 1)[-1].replace(".zip", ".csv")


def _parse_checksum(raw: bytes, filename: str) -> str:
    parts = raw.decode("utf-8").strip().split()
    if not parts or len(parts[0]) != 64:
        raise ValueError(f"invalid official checksum for {filename}")
    if len(parts) > 1 and parts[-1].lstrip("*") != filename:
        raise ValueError(f"checksum names {parts[-1]} instead of {filename}")
    return parts[0].lower()


def _inspect_zip(raw: bytes, expected_member: str) -> Tuple[int, int, int]:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        if names != [expected_member]:
            raise ValueError(f"archive members {names!r} do not equal [{expected_member!r}]")
        member = PurePosixPath(names[0])
        if member.is_absolute() or ".." in member.parts:
            raise ValueError(f"unsafe archive member: {names[0]}")
        row_count = 0
        first_timestamp: Optional[int] = None
        last_timestamp: Optional[int] = None
        with archive.open(names[0]) as source:
            reader = csv.reader(io.TextIOWrapper(source, encoding="utf-8", newline=""))
            for row in reader:
                if not row or not row[0].lstrip("-").isdigit():
                    continue
                timestamp = normalize_timestamp_ms(row[0])
                if first_timestamp is None:
                    first_timestamp = timestamp
                last_timestamp = timestamp
                row_count += 1
        if row_count == 0 or first_timestamp is None or last_timestamp is None:
            raise ValueError(f"archive contains no data rows: {expected_member}")
        return row_count, first_timestamp, last_timestamp


def _collect_one(root: Path, surface: str, symbol: str, month: str) -> Dict[str, object]:
    url = SURFACES[surface].format(symbol=symbol, month=month)
    filename = url.rsplit("/", 1)[-1]
    expected_member = _expected_member(url)
    directory = root / DATA_ROOT / surface / symbol
    archive_path = directory / filename
    checksum_path = directory / f"{filename}.CHECKSUM"
    directory.mkdir(parents=True, exist_ok=True)

    if checksum_path.exists():
        checksum_raw = checksum_path.read_bytes()
    else:
        checksum_raw = _request_bytes(f"{url}.CHECKSUM")
        temporary = checksum_path.with_suffix(checksum_path.suffix + ".tmp")
        temporary.write_bytes(checksum_raw)
        os.replace(temporary, checksum_path)
    expected_sha = _parse_checksum(checksum_raw, filename)

    archive_raw: Optional[bytes] = None
    if not archive_path.exists() or sha256_file(archive_path) != expected_sha:
        archive_raw = _request_bytes(url)
        if sha256_bytes(archive_raw) != expected_sha:
            raise ValueError(f"official checksum mismatch for {url}")
        temporary = archive_path.with_suffix(archive_path.suffix + ".tmp")
        temporary.write_bytes(archive_raw)
        os.replace(temporary, archive_path)
    actual_sha = sha256_file(archive_path)
    if actual_sha != expected_sha:
        raise ValueError(f"stored checksum mismatch for {archive_path}")
    if archive_raw is None:
        archive_raw = archive_path.read_bytes()
    rows, first_timestamp, last_timestamp = _inspect_zip(archive_raw, expected_member)
    return {
        "surface": surface,
        "symbol": symbol,
        "month": month,
        "source_url": url,
        "checksum_url": f"{url}.CHECKSUM",
        "archive_path": archive_path.relative_to(root).as_posix(),
        "checksum_path": checksum_path.relative_to(root).as_posix(),
        "archive_member": expected_member,
        "sha256": actual_sha,
        "bytes": archive_path.stat().st_size,
        "row_count": rows,
        "first_timestamp": iso_timestamp(first_timestamp),
        "last_timestamp": iso_timestamp(last_timestamp),
    }


def verify_preregistration(root: Path) -> str:
    path = root / PREREGISTRATION
    digest = sha256_file(path)
    if digest != PREREGISTRATION_SHA256:
        raise ValueError(
            f"preregistration hash changed: expected {PREREGISTRATION_SHA256}, observed {digest}"
        )
    return digest


def collect(root: Path, workers: int = 8) -> Dict[str, object]:
    verify_preregistration(root)
    manifest_path = root / MANIFEST
    if manifest_path.exists():
        manifest, _ = _manifest_integrity(root)
        return manifest
    raise ValueError(
        "the child experiment is bound to the immutable parent source manifest; restore that artifact instead of recollecting"
    )


def _collect_parent_sources(root: Path, workers: int = 8) -> Dict[str, object]:
    """Retained only to document how the immutable parent manifest was built."""
    preregistration_sha = PARENT_PREREGISTRATION_SHA256
    tasks = [
        (surface, symbol, month)
        for surface in SURFACES
        for symbol in SYMBOLS.values()
        for month in month_range()
    ]
    records: List[Dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(_collect_one, root, surface, symbol, month): (surface, symbol, month)
            for surface, symbol, month in tasks
        }
        for future in as_completed(futures):
            records.append(future.result())
    records.sort(key=lambda item: (str(item["surface"]), str(item["symbol"]), str(item["month"])))
    manifest = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "created_at": utc_now(),
        "provider": "binance_official_public_data_archive",
        "exchange": "binance",
        "preregistration_sha256": preregistration_sha,
        "official_source": "https://github.com/binance/binance-public-data/blob/master/README.md",
        "archive_count": len(records),
        "total_compressed_bytes": sum(int(item["bytes"]) for item in records),
        "all_official_checksums_verified": True,
        "records": records,
    }
    path = root / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return manifest


def _archive_paths(root: Path, surface: str, symbol: str) -> Iterable[Path]:
    directory = root / DATA_ROOT / surface / symbol
    for month in month_range():
        url = SURFACES[surface].format(symbol=symbol, month=month)
        yield directory / url.rsplit("/", 1)[-1]


def _iter_archive_rows(path: Path) -> Iterable[List[str]]:
    expected = path.name.replace(".zip", ".csv")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if names != [expected]:
            raise ValueError(f"unexpected archive members for {path}: {names!r}")
        member = PurePosixPath(names[0])
        if member.is_absolute() or ".." in member.parts:
            raise ValueError(f"unsafe archive member in {path}")
        with archive.open(expected) as source:
            reader = csv.reader(io.TextIOWrapper(source, encoding="utf-8", newline=""))
            for row in reader:
                if row and row[0].lstrip("-").isdigit():
                    yield row


def load_klines(
    root: Path, surface: str, symbol: str, premium: bool = False, allow_gaps: bool = False
) -> List[Kline]:
    values: List[Kline] = []
    previous_timestamp: Optional[int] = None
    for path in _archive_paths(root, surface, symbol):
        for row in _iter_archive_rows(path):
            if len(row) < 11:
                raise ValueError(f"short kline row in {path}")
            timestamp = normalize_timestamp_ms(row[0])
            open_price, high, low, close = map(float, row[1:5])
            quote_volume = float(row[7])
            trade_count = int(row[8])
            taker_buy_quote = float(row[10])
            numeric = (open_price, high, low, close, quote_volume, taker_buy_quote)
            if not all(math.isfinite(item) for item in numeric):
                raise ValueError(f"non-finite kline value at {timestamp} in {path}")
            if low > min(open_price, close) or high < max(open_price, close) or low > high:
                raise ValueError(f"invalid OHLC relation at {timestamp} in {path}")
            if premium:
                if quote_volume != 0 or taker_buy_quote != 0:
                    raise ValueError(f"unexpected premium-index volume at {timestamp} in {path}")
            else:
                if min(open_price, high, low, close) <= 0:
                    raise ValueError(f"non-positive market price at {timestamp} in {path}")
                if quote_volume < 0 or taker_buy_quote < 0 or taker_buy_quote > quote_volume + 1e-8:
                    raise ValueError(f"invalid volume relation at {timestamp} in {path}")
                if trade_count < 0:
                    raise ValueError(f"negative trade count at {timestamp} in {path}")
            if (
                previous_timestamp is not None
                and timestamp - previous_timestamp != INTERVAL_MS
                and not allow_gaps
            ):
                raise ValueError(
                    f"non-contiguous {surface}/{symbol}: {previous_timestamp} -> {timestamp}"
                )
            values.append(
                Kline(timestamp, open_price, high, low, close, quote_volume, trade_count, taker_buy_quote)
            )
            previous_timestamp = timestamp
    if not values:
        raise ValueError(f"no klines loaded for {surface}/{symbol}")
    return values


def load_funding(root: Path, symbol: str) -> Tuple[List[int], List[float]]:
    timestamps: List[int] = []
    rates: List[float] = []
    previous: Optional[int] = None
    for path in _archive_paths(root, "funding_rates", symbol):
        for row in _iter_archive_rows(path):
            if len(row) < 3:
                raise ValueError(f"short funding row in {path}")
            timestamp = normalize_timestamp_ms(row[0])
            interval_hours = int(row[1])
            rate = float(row[2])
            if interval_hours <= 0 or not math.isfinite(rate):
                raise ValueError(f"invalid funding row at {timestamp} in {path}")
            if previous is not None and timestamp <= previous:
                raise ValueError(f"non-increasing funding timestamp at {timestamp} in {path}")
            timestamps.append(timestamp)
            rates.append(rate)
            previous = timestamp
    if not timestamps:
        raise ValueError(f"no funding rates loaded for {symbol}")
    return timestamps, rates


def _breakout_flags(closes: Sequence[float], lookback: int = 288) -> List[bool]:
    queue: deque = deque()
    flags = [False] * len(closes)
    for index, close in enumerate(closes):
        cutoff = index - lookback
        while queue and queue[0] < cutoff:
            queue.popleft()
        if index >= lookback and queue:
            flags[index] = close > closes[queue[0]]
        while queue and closes[queue[-1]] <= close:
            queue.pop()
        queue.append(index)
    return flags


def align_market_rows(
    spot: Sequence[Kline],
    perpetual: Sequence[Kline],
    premium: Sequence[Kline],
    allow_missing_premium: bool = False,
) -> List[AlignedBar]:
    if len(spot) != len(perpetual):
        raise ValueError(f"same-exchange spot/perpetual length mismatch: {len(spot)}, {len(perpetual)}")
    if not allow_missing_premium and len(spot) != len(premium):
        raise ValueError(f"same-exchange series length mismatch: {len(spot)}, {len(perpetual)}, {len(premium)}")
    premium_by_timestamp = {bar.timestamp_ms: bar for bar in premium}
    if len(premium_by_timestamp) != len(premium):
        raise ValueError("duplicate premium-index timestamps")
    spot_timestamps = {bar.timestamp_ms for bar in spot}
    if any(timestamp not in spot_timestamps for timestamp in premium_by_timestamp):
        raise ValueError("premium-index timestamp falls outside the complete spot/perpetual clock")
    spot_breakouts = _breakout_flags([bar.close for bar in spot])
    perpetual_breakouts = _breakout_flags([bar.close for bar in perpetual])
    values: List[AlignedBar] = []
    prior_basis = 0.0
    for index, (spot_bar, perpetual_bar) in enumerate(zip(spot, perpetual)):
        premium_bar = premium_by_timestamp.get(spot_bar.timestamp_ms)
        if spot_bar.timestamp_ms != perpetual_bar.timestamp_ms:
            raise ValueError(
                f"same-exchange timestamp mismatch: {spot_bar.timestamp_ms}, {perpetual_bar.timestamp_ms}"
            )
        if premium_bar is None and not allow_missing_premium:
            raise ValueError(f"same-exchange premium timestamp missing: {spot_bar.timestamp_ms}")
        spot_return = math.log(spot_bar.close / spot_bar.open)
        perpetual_return = math.log(perpetual_bar.close / perpetual_bar.open)
        spot_imbalance = (
            2 * spot_bar.taker_buy_quote_volume / spot_bar.quote_volume - 1
            if spot_bar.quote_volume
            else 0.0
        )
        perpetual_imbalance = (
            2 * perpetual_bar.taker_buy_quote_volume / perpetual_bar.quote_volume - 1
            if perpetual_bar.quote_volume
            else 0.0
        )
        basis = math.log(perpetual_bar.close / spot_bar.close)
        basis_change = basis - prior_basis if index else 0.0
        values.append(
            AlignedBar(
                spot_bar.timestamp_ms,
                spot_bar.open,
                spot_bar.close,
                spot_return,
                spot_imbalance,
                spot_bar.quote_volume,
                perpetual_bar.open,
                perpetual_bar.close,
                perpetual_return,
                perpetual_imbalance,
                perpetual_bar.quote_volume,
                premium_bar.close if premium_bar is not None else math.nan,
                basis,
                basis_change,
                spot_breakouts[index],
                perpetual_breakouts[index],
            )
        )
        prior_basis = basis
    return values


def quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("quantile requires at least one value")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _folds(rows: Sequence[AlignedBar]) -> List[Dict[str, int]]:
    data_end = rows[-1].timestamp_ms + INTERVAL_MS
    first_test_start = rows[0].timestamp_ms + 90 * DAY_MS
    values = []
    start = first_test_start
    while start + 30 * DAY_MS <= data_end:
        end = start + 30 * DAY_MS
        values.append(
            {
                "id": f"FOLD-{len(values) + 1:02d}",
                "start_ms": start,
                "end_ms": end,
                "train_end_index": bisect.bisect_left([row.timestamp_ms for row in rows], start),
                "test_start_index": bisect.bisect_left([row.timestamp_ms for row in rows], start),
                "test_end_index": bisect.bisect_left([row.timestamp_ms for row in rows], end),
            }
        )
        start = end
    return values


def _thresholds(rows: Sequence[AlignedBar], train_end: int) -> Dict[str, float]:
    training = rows[:train_end]
    return {
        "spot_imbalance_q80": quantile([row.spot_imbalance for row in training], 0.80),
        "perpetual_imbalance_q80": quantile([row.perpetual_imbalance for row in training], 0.80),
        "spot_volume_q50": quantile([row.spot_quote_volume for row in training], 0.50),
        "perpetual_volume_q50": quantile([row.perpetual_quote_volume for row in training], 0.50),
        "spot_volume_q70": quantile([row.spot_quote_volume for row in training], 0.70),
        "perpetual_volume_q70": quantile([row.perpetual_quote_volume for row in training], 0.70),
        "spot_positive_return_q90": quantile([row.spot_return for row in training if row.spot_return > 0], 0.90),
        "perpetual_positive_return_q90": quantile(
            [row.perpetual_return for row in training if row.perpetual_return > 0], 0.90
        ),
        "basis_q90": quantile([row.log_basis for row in training], 0.90),
    }


def mechanism_signal(family: str, row: AlignedBar, thresholds: Dict[str, float]) -> bool:
    if family == "CONFIRMED-FLOW-CONTINUATION-30M":
        return (
            row.spot_imbalance >= thresholds["spot_imbalance_q80"]
            and row.perpetual_imbalance >= thresholds["perpetual_imbalance_q80"]
            and row.spot_return > 0
            and row.perpetual_return > 0
            and row.spot_quote_volume >= thresholds["spot_volume_q50"]
            and row.perpetual_quote_volume >= thresholds["perpetual_volume_q50"]
        )
    if family == "PERPETUAL-LEADS-SPOT-30M":
        return (
            row.perpetual_return >= thresholds["perpetual_positive_return_q90"]
            and row.perpetual_imbalance >= thresholds["perpetual_imbalance_q80"]
            and row.perpetual_quote_volume >= thresholds["perpetual_volume_q50"]
            and row.spot_return < 0.5 * row.perpetual_return
        )
    if family == "SPOT-LEADS-PERPETUAL-30M":
        return (
            row.spot_return >= thresholds["spot_positive_return_q90"]
            and row.spot_imbalance >= thresholds["spot_imbalance_q80"]
            and row.spot_quote_volume >= thresholds["spot_volume_q50"]
            and row.perpetual_return < 0.5 * row.spot_return
        )
    if family == "BASIS-CONVERGENCE-6H":
        return (
            row.log_basis >= thresholds["basis_q90"]
            and row.basis_change > 0
            and row.premium_index > 0
        )
    if family == "CONFIRMED-SLOW-BREAKOUT-6H":
        return (
            row.spot_breakout
            and row.perpetual_breakout
            and row.spot_imbalance > 0
            and row.perpetual_imbalance > 0
            and row.spot_quote_volume >= thresholds["spot_volume_q70"]
            and row.perpetual_quote_volume >= thresholds["perpetual_volume_q70"]
        )
    raise ValueError(f"unknown mechanism family: {family}")


def funding_prefix(rates: Sequence[float]) -> List[float]:
    values = [0.0]
    for rate in rates:
        values.append(values[-1] + rate)
    return values


def funding_between(
    timestamps: Sequence[int], prefix: Sequence[float], entry_timestamp_ms: int, exit_timestamp_ms: int
) -> float:
    lower = bisect.bisect_right(timestamps, entry_timestamp_ms)
    upper = bisect.bisect_right(timestamps, exit_timestamp_ms)
    return prefix[upper] - prefix[lower]


def _trade_return(target: str, entry: AlignedBar, exit_bar: AlignedBar, funding_sum: float) -> float:
    spot_return = exit_bar.spot_open / entry.spot_open - 1
    perpetual_return = exit_bar.perpetual_open / entry.perpetual_open - 1
    if target == "spot":
        return spot_return
    if target == "perpetual":
        return perpetual_return - funding_sum
    if target == "long_spot_short_perpetual":
        return spot_return - perpetual_return + funding_sum
    raise ValueError(f"unknown target: {target}")


def evaluate_stream(
    rows: Sequence[AlignedBar],
    folds: Sequence[Dict[str, int]],
    fold_thresholds: Sequence[Dict[str, float]],
    family: str,
    target: str,
    funding_timestamps: Sequence[int],
    funding_rates: Sequence[float],
) -> List[Trade]:
    holding_bars = int(FAMILIES[family]["holding_bars"])
    prefix = funding_prefix(funding_rates)
    trades: List[Trade] = []
    for fold, thresholds in zip(folds, fold_thresholds):
        index = fold["test_start_index"]
        while index < fold["test_end_index"]:
            entry_index = index + 1
            exit_index = entry_index + holding_bars
            if exit_index >= fold["test_end_index"]:
                break
            if not mechanism_signal(family, rows[index], thresholds):
                index += 1
                continue
            entry = rows[entry_index]
            exit_bar = rows[exit_index]
            funding_sum = funding_between(
                funding_timestamps, prefix, entry.timestamp_ms, exit_bar.timestamp_ms
            )
            trades.append(
                Trade(
                    str(fold["id"]),
                    entry.timestamp_ms,
                    exit_bar.timestamp_ms,
                    _trade_return(target, entry, exit_bar, funding_sum),
                    funding_sum,
                )
            )
            index = exit_index
    return trades


def _compounded(returns: Sequence[float]) -> float:
    equity = 1.0
    for value in returns:
        equity *= 1 + value
    return equity - 1


def _max_drawdown(returns: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    maximum = 0.0
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        if peak:
            maximum = max(maximum, (peak - equity) / peak)
    return maximum


def _one_sided_p_value(returns: Sequence[float]) -> float:
    if len(returns) < 2:
        return 1.0
    mean = statistics.fmean(returns)
    deviation = statistics.stdev(returns)
    if deviation == 0:
        return 0.0 if mean > 0 else 1.0
    statistic = mean / (deviation / math.sqrt(len(returns)))
    return 0.5 * math.erfc(statistic / math.sqrt(2))


def summarize_trades(trades: Sequence[Trade], fold_ids: Sequence[str], target: str, cost_bps: int) -> Dict[str, object]:
    sides = 4 if target == "long_spot_short_perpetual" else 2
    cost = sides * cost_bps / 10000
    gross = [trade.gross_return for trade in trades]
    net = [value - cost for value in gross]
    by_fold: Dict[str, List[float]] = {fold_id: [] for fold_id in fold_ids}
    gross_by_fold: Dict[str, List[float]] = {fold_id: [] for fold_id in fold_ids}
    for trade, gross_return, net_return in zip(trades, gross, net):
        by_fold[trade.fold_id].append(net_return)
        gross_by_fold[trade.fold_id].append(gross_return)
    fold_net = [_compounded(by_fold[fold_id]) for fold_id in fold_ids]
    positive_fold_returns = [value for value in fold_net if value > 0]
    concentration = (
        max(positive_fold_returns) / sum(positive_fold_returns) if positive_fold_returns else 1.0
    )
    p_value = _one_sided_p_value(net)
    mean_gross = statistics.fmean(gross) if gross else 0.0
    mean_net = statistics.fmean(net) if net else 0.0
    summary: Dict[str, object] = {
        "cost_bps_per_side": cost_bps,
        "trading_sides": sides,
        "trade_count": len(trades),
        "gross_compounded_return": _compounded(gross),
        "net_compounded_return": _compounded(net),
        "mean_gross_return": mean_gross,
        "mean_net_return": mean_net,
        "median_net_return": statistics.median(net) if net else 0.0,
        "hit_rate": sum(value > 0 for value in net) / len(net) if net else 0.0,
        "maximum_drawdown": _max_drawdown(net),
        "evaluated_fold_count": len(fold_ids),
        "positive_fold_fraction": sum(value > 0 for value in fold_net) / len(fold_ids) if fold_ids else 0.0,
        "one_sided_p_value": p_value,
        "bonferroni_p_value": min(1.0, p_value * MULTIPLICITY),
        "best_fold_profit_share": concentration,
        "break_even_per_side_cost_bps": mean_gross * 10000 / sides,
        "funding_events_crossed": sum(1 for trade in trades if trade.funding_sum != 0),
        "funding_sum_across_trades": sum(trade.funding_sum for trade in trades),
        "folds": [
            {
                "fold_id": fold_id,
                "trade_count": len(by_fold[fold_id]),
                "gross_compounded_return": _compounded(gross_by_fold[fold_id]),
                "net_compounded_return": _compounded(by_fold[fold_id]),
            }
            for fold_id in fold_ids
        ],
    }
    if cost_bps == PRIMARY_COST_BPS:
        gates = {
            "positive_mean_net_return": mean_net > 0,
            "minimum_trades": len(trades) >= 50,
            "minimum_test_folds": len(fold_ids) >= 6,
            "positive_fold_fraction": summary["positive_fold_fraction"] >= 0.70,
            "maximum_drawdown": summary["maximum_drawdown"] <= 0.15,
            "maximum_best_fold_profit_share": concentration <= 0.50,
            "maximum_bonferroni_p_value": summary["bonferroni_p_value"] <= 0.05,
        }
        summary["gates"] = gates
        summary["gate_passed"] = all(gates.values())
        summary["gate_failures"] = [name for name, passed in gates.items() if not passed]
    return summary


def _manifest_integrity(root: Path) -> Tuple[Dict[str, object], str]:
    path = root / MANIFEST
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if sha256_file(path) != MANIFEST_SHA256:
        raise ValueError("immutable parent source manifest changed")
    if manifest.get("experiment_id") != PARENT_EXPERIMENT_ID:
        raise ValueError("historical manifest has the wrong parent experiment ID")
    if manifest.get("preregistration_sha256") != PARENT_PREREGISTRATION_SHA256:
        raise ValueError("historical manifest is not bound to the frozen parent preregistration")
    records = manifest.get("records", [])
    if len(records) != 96:
        raise ValueError(f"expected 96 frozen archives, found {len(records)}")
    for record in records:
        path = root / str(record["archive_path"])
        if sha256_file(path) != record["sha256"]:
            raise ValueError(f"archive changed after manifest: {path}")
    return manifest, sha256_file(path=root / MANIFEST)


def evaluate(root: Path) -> Dict[str, object]:
    verify_preregistration(root)
    manifest, manifest_sha = _manifest_integrity(root)
    stream_results: List[Dict[str, object]] = []
    data_quality: List[Dict[str, object]] = []
    fold_definitions: Optional[List[Dict[str, object]]] = None

    for asset, symbol in SYMBOLS.items():
        spot = load_klines(root, "spot_klines", symbol)
        perpetual = load_klines(root, "perpetual_klines", symbol)
        premium = load_klines(root, "premium_index_klines", symbol, premium=True, allow_gaps=True)
        funding_timestamps, funding_rates = load_funding(root, symbol)
        rows = align_market_rows(spot, perpetual, premium, allow_missing_premium=True)
        premium_timestamps = {bar.timestamp_ms for bar in premium}
        missing_premium = [row.timestamp_ms for row in rows if row.timestamp_ms not in premium_timestamps]
        expected_missing = list(range(1782691200000, 1782777600000, INTERVAL_MS))
        if missing_premium != expected_missing:
            raise ValueError(
                f"unexpected premium-index unavailability for {symbol}: "
                f"expected {len(expected_missing)} fixed bars, observed {len(missing_premium)}"
            )
        if funding_timestamps[0] < rows[0].timestamp_ms or funding_timestamps[-1] > rows[-1].timestamp_ms + INTERVAL_MS:
            raise ValueError(f"funding timestamps outside aligned range for {symbol}")
        folds = _folds(rows)
        thresholds = [_thresholds(rows, fold["train_end_index"]) for fold in folds]
        if fold_definitions is None:
            fold_definitions = [
                {
                    "fold_id": fold["id"],
                    "test_start": iso_timestamp(fold["start_ms"]),
                    "test_end_exclusive": iso_timestamp(fold["end_ms"]),
                    "training_rows": fold["train_end_index"],
                }
                for fold in folds
            ]
        data_quality.append(
            {
                "exchange": "binance",
                "asset": asset,
                "spot_market": "binance_spot",
                "perpetual_market": "binance_usdm_perpetual",
                "aligned_bar_count": len(rows),
                "first_timestamp": iso_timestamp(rows[0].timestamp_ms),
                "last_timestamp": iso_timestamp(rows[-1].timestamp_ms),
                "funding_observation_count": len(funding_timestamps),
                "unexplained_bar_gaps": 0,
                "timestamp_mismatches": 0,
                "premium_index_observation_count": len(premium),
                "premium_index_unavailable_count": len(missing_premium),
                "premium_index_unavailable_start": iso_timestamp(missing_premium[0]),
                "premium_index_unavailable_end": iso_timestamp(missing_premium[-1]),
                "premium_index_missing_policy": "explicit_unavailable; no interpolation or row deletion; current basis signal blocked",
                "quality_gate_passed": True,
            }
        )
        fold_ids = [str(fold["id"]) for fold in folds]
        for family, specification in FAMILIES.items():
            for target in specification["targets"]:
                trades = evaluate_stream(
                    rows,
                    folds,
                    thresholds,
                    family,
                    str(target),
                    funding_timestamps,
                    funding_rates,
                )
                cost_scenarios = {
                    str(cost): summarize_trades(trades, fold_ids, str(target), cost) for cost in COSTS_BPS
                }
                stream_results.append(
                    {
                        "stream_id": f"BINANCE-{asset}-{family}-{str(target).upper()}",
                        "exchange": "binance",
                        "asset": asset,
                        "spot_symbol": symbol,
                        "perpetual_symbol": symbol,
                        "family": family,
                        "target": target,
                        "holding_bars": specification["holding_bars"],
                        "cost_scenarios": cost_scenarios,
                        "primary": cost_scenarios[str(PRIMARY_COST_BPS)],
                    }
                )

    family_decisions = []
    for family in FAMILIES:
        family_streams = [stream for stream in stream_results if stream["family"] == family]
        passed = all(bool(stream["primary"]["gate_passed"]) for stream in family_streams)
        family_decisions.append(
            {
                "family": family,
                "stream_count": len(family_streams),
                "passing_stream_count": sum(bool(stream["primary"]["gate_passed"]) for stream in family_streams),
                "decision": "PASS" if passed else "REJECT",
                "failed_streams": [
                    {
                        "stream_id": stream["stream_id"],
                        "failures": stream["primary"]["gate_failures"],
                    }
                    for stream in family_streams
                    if not stream["primary"]["gate_passed"]
                ],
            }
        )
    passing_families = [item["family"] for item in family_decisions if item["decision"] == "PASS"]
    nomination = passing_families[0] if len(passing_families) == 1 else None
    if nomination:
        overall_decision = "NOMINATE_ONE_FOR_SEPARATE_PROSPECTIVE_PREREGISTRATION"
    elif passing_families:
        overall_decision = "MULTIPLE_PASS_NO_POST_HOC_SELECTION"
    else:
        overall_decision = "NO_FAMILY_PASSED"

    comparisons = []
    for family in ("CONFIRMED-FLOW-CONTINUATION-30M", "CONFIRMED-SLOW-BREAKOUT-6H"):
        for asset in SYMBOLS:
            streams = {
                str(stream["target"]): stream
                for stream in stream_results
                if stream["family"] == family and stream["asset"] == asset
            }
            spot_primary = streams["spot"]["primary"]
            perpetual_primary = streams["perpetual"]["primary"]
            comparisons.append(
                {
                    "exchange": "binance",
                    "asset": asset,
                    "family": family,
                    "spot_mean_net_return": spot_primary["mean_net_return"],
                    "perpetual_mean_net_return": perpetual_primary["mean_net_return"],
                    "perpetual_minus_spot_mean_net_return": perpetual_primary["mean_net_return"]
                    - spot_primary["mean_net_return"],
                    "spot_gate_passed": spot_primary["gate_passed"],
                    "perpetual_gate_passed": perpetual_primary["gate_passed"],
                }
            )

    result: Dict[str, object] = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "evaluated_at": utc_now(),
        "claim_class": "HISTORICAL_MECHANISM_FALSIFICATION",
        "parent_experiment_id": PARENT_EXPERIMENT_ID,
        "preregistration_path": PREREGISTRATION.as_posix(),
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "manifest_path": MANIFEST.as_posix(),
        "manifest_sha256": manifest_sha,
        "source_integrity": {
            "archive_count": manifest["archive_count"],
            "all_official_checksums_verified": manifest["all_official_checksums_verified"],
            "total_compressed_bytes": manifest["total_compressed_bytes"],
        },
        "data_quality": data_quality,
        "folds": fold_definitions or [],
        "multiplicity": {"evaluated_streams": MULTIPLICITY, "method": "Bonferroni"},
        "streams": stream_results,
        "family_decisions": family_decisions,
        "spot_perpetual_comparisons": comparisons,
        "passing_families": passing_families,
        "nominated_family": nomination,
        "decision": overall_decision,
        "history_role": "Mechanism discovery and falsification only; not live, venue-transfer, or capital evidence.",
        "next_action": (
            f"Create a new prospective preregistration on the intended venue for {nomination}."
            if nomination
            else "Do not tune this experiment. Preserve the rejection and design a new hypothesis only from a stated economic mechanism."
        ),
        "capital_used_usd": "0.00",
        "orders_sent": 0,
        "live_or_capital_authority_earned": False,
    }
    path = root / RESULT
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return result


def _percent(value: object) -> str:
    return f"{100 * float(value):.3f}%"


def render_report(result: Dict[str, object], root: Path) -> Path:
    lines = [
        "# Same-exchange spot/perpetual mechanism falsification",
        "",
        f"Experiment: `{EXPERIMENT_ID}`  ",
        f"Decision: **{result['decision']}**  ",
        f"Nominated family: **{result['nominated_family'] or 'none'}**  ",
        "Capital used: **$0.00**; orders sent: **0**",
        "",
        "## What was compared",
        "",
        "BTCUSDT and ETHUSDT spot were paired only with their Binance USD-M perpetual equivalents. "
        "Every spot and perpetual bar was aligned by timestamp; the known one-day auxiliary premium-index gap "
        "remained explicit and blocked only affected basis signals. Official funding was applied to perpetual "
        "holdings. The twelve-month history was used for falsification, not as live proof.",
        "",
        "## Data-quality result",
        "",
        "| Exchange | Asset | Aligned 5m bars | Funding rows | Premium unavailable | Range | Gate |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for item in result["data_quality"]:
        lines.append(
            f"| {item['exchange']} | {item['asset']} | {item['aligned_bar_count']:,} | "
            f"{item['funding_observation_count']:,} | {item['premium_index_unavailable_count']:,} | "
            f"{item['first_timestamp']} to {item['last_timestamp']} | "
            f"{'PASS' if item['quality_gate_passed'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "All 96 source archives were checked against the official SHA-256 files. The 288 missing premium "
            "observations per asset were neither filled nor used. A quality pass only means "
            "the evidence is admissible; it does not mean the data contains a profitable signal.",
            "",
            "## Family decisions at 20 bps per side",
            "",
            "| Mechanism family | Passing streams | Required streams | Decision |",
            "|---|---:|---:|---|",
        ]
    )
    for family in result["family_decisions"]:
        lines.append(
            f"| {family['family']} | {family['passing_stream_count']} | {family['stream_count']} | "
            f"{family['decision']} |"
        )
    lines.extend(
        [
            "",
            "## Every frozen asset/target stream",
            "",
            "| Exchange | Asset | Mechanism | Target | Trades | Mean net | Net compounded | Positive folds | Adjusted p | Max DD | Gate |",
            "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for stream in result["streams"]:
        primary = stream["primary"]
        lines.append(
            f"| {stream['exchange']} | {stream['asset']} | {stream['family']} | {stream['target']} | "
            f"{primary['trade_count']} | {_percent(primary['mean_net_return'])} | "
            f"{_percent(primary['net_compounded_return'])} | {_percent(primary['positive_fold_fraction'])} | "
            f"{primary['bonferroni_p_value']:.4g} | {_percent(primary['maximum_drawdown'])} | "
            f"{'PASS' if primary['gate_passed'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "## Cost sensitivity: mean net return per trade",
            "",
            "| Asset | Mechanism | Target | 5 bps/side | 10 bps/side | 20 bps/side | 40 bps/side | Gross break-even/side |",
            "|---:|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for stream in result["streams"]:
        scenarios = stream["cost_scenarios"]
        lines.append(
            f"| {stream['asset']} | {stream['family']} | {stream['target']} | "
            f"{_percent(scenarios['5']['mean_net_return'])} | {_percent(scenarios['10']['mean_net_return'])} | "
            f"{_percent(scenarios['20']['mean_net_return'])} | {_percent(scenarios['40']['mean_net_return'])} | "
            f"{stream['primary']['break_even_per_side_cost_bps']:.3f} bps |"
        )
    lines.extend(
        [
            "",
            "## Direct spot-versus-perpetual comparison",
            "",
            "| Exchange | Asset | Shared signal | Spot mean net | Perpetual mean net | Perpetual - spot |",
            "|---|---:|---|---:|---:|---:|",
        ]
    )
    for item in result["spot_perpetual_comparisons"]:
        lines.append(
            f"| {item['exchange']} | {item['asset']} | {item['family']} | "
            f"{_percent(item['spot_mean_net_return'])} | {_percent(item['perpetual_mean_net_return'])} | "
            f"{_percent(item['perpetual_minus_spot_mean_net_return'])} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation and boundary",
            "",
            f"{result['next_action']}",
            "",
            "A historical pass would only nominate a prospective experiment. It would not establish Coinbase "
            "transferability, actual fees, queue position, fill probability, latency, margin/liquidation behavior, "
            "capacity, or realized profit. Level 2 data belongs later, when a survivor needs execution calibration; "
            "it is not required to decide whether these bar-level economic mechanisms are already dead after "
            "conservative costs.",
            "",
            "## Reproduce",
            "",
            "```powershell",
            "python -m experiments.historical_mechanisms run --root .",
            "```",
            "",
            f"Preregistration SHA-256: `{result['preregistration_sha256']}`  ",
            f"Manifest SHA-256: `{result['manifest_sha256']}`",
            "",
        ]
    )
    path = root / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("collect", "evaluate", "run"))
    parser.add_argument("--root", default=".")
    parser.add_argument("--workers", type=int, default=8)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = build_parser().parse_args(argv)
    root = Path(arguments.root).resolve()
    if arguments.command in ("collect", "run"):
        manifest = collect(root, workers=arguments.workers)
        print(
            json.dumps(
                {
                    "status": "collected",
                    "archive_count": manifest["archive_count"],
                    "manifest": MANIFEST.as_posix(),
                }
            )
        )
    if arguments.command in ("evaluate", "run"):
        result = evaluate(root)
        report_path = render_report(result, root)
        print(
            json.dumps(
                {
                    "status": "evaluated",
                    "decision": result["decision"],
                    "nominated_family": result["nominated_family"],
                    "result": RESULT.as_posix(),
                    "report": report_path.relative_to(root).as_posix(),
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
