"""Deterministic, non-executing inventory for bounded Solidity review scopes.

The inventory is deliberately descriptive: it identifies review-relevant trust
boundaries and state-transition primitives without declaring vulnerabilities.
It never compiles or executes target code and has no third-party dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SIGNAL_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("delegatecall", re.compile(r"\.delegatecall\s*\(")),
    ("staticcall", re.compile(r"\.staticcall\s*\(")),
    ("low_level_call", re.compile(r"\.call(?:\{[^}]*\})?\s*\(")),
    ("implementation_upgrade", re.compile(r"\bupgradeToAndCall\s*\(")),
    ("signature_recovery", re.compile(r"\b(?:recover|tryRecover)\s*\(")),
    ("chain_context", re.compile(r"\bblock\.chainid\b")),
    ("caller_context", re.compile(r"\bmsg\.sender\b")),
    ("external_self_call", re.compile(r"\bthis\.[A-Za-z_]\w*\s*\(")),
    ("destructive_opcode", re.compile(r"\b(?:selfdestruct|suicide)\s*\(")),
    ("origin_authorization", re.compile(r"\btx\.origin\b")),
)

FUNCTION_PATTERN = re.compile(
    r"\bfunction\s+(?P<name>[A-Za-z_]\w*)\s*\([^;{]*?\)"
    r"(?P<tail>[^;{]*?)\{",
    re.DOTALL,
)

RETURNDATA_CAPTURE_PATTERN = re.compile(
    r"\(\s*bool\s+[A-Za-z_]\w*\s*,\s*bytes\s+memory\s+[A-Za-z_]\w*\s*\)"
    r"\s*=\s*[^;]*?\.delegatecall\s*\(",
    re.DOTALL,
)


class InventoryError(ValueError):
    """Raised when the requested source scope is unsafe or malformed."""


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _resolve_scoped_file(root: Path, requested: Path) -> Path:
    root = root.resolve()
    candidate = requested if requested.is_absolute() else root / requested
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise InventoryError(f"source path escapes root: {requested}") from exc
    if candidate.suffix.lower() != ".sol":
        raise InventoryError(f"source path is not Solidity: {requested}")
    if not candidate.is_file():
        raise InventoryError(f"source file does not exist: {requested}")
    return candidate


def _brace_end(text: str, opening_offset: int) -> int:
    depth = 0
    for offset in range(opening_offset, len(text)):
        character = text[offset]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return offset
    raise InventoryError("unbalanced function braces")


def _functions(text: str) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for match in FUNCTION_PATTERN.finditer(text):
        opening_offset = match.end() - 1
        closing_offset = _brace_end(text, opening_offset)
        tail = " ".join(match.group("tail").split())
        visibility = next((item for item in ("external", "public", "internal", "private") if re.search(rf"\b{item}\b", tail)), "unspecified")
        mutability = next((item for item in ("view", "pure", "payable") if re.search(rf"\b{item}\b", tail)), "nonpayable")
        results.append(
            {
                "name": match.group("name"),
                "start_line": _line_number(text, match.start()),
                "end_line": _line_number(text, closing_offset),
                "visibility": visibility,
                "mutability": mutability,
            }
        )
    return results


def _signals(text: str) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for signal_id, pattern in SIGNAL_PATTERNS:
        for match in pattern.finditer(text):
            results.append({"id": signal_id, "line": _line_number(text, match.start())})
    for match in RETURNDATA_CAPTURE_PATTERN.finditer(text):
        results.append(
            {
                "id": "delegatecall_dynamic_returndata_capture",
                "line": _line_number(text, match.start()),
            }
        )
    results.sort(key=lambda item: (item["line"], item["id"]))
    return results


def inventory(root: Path, requested_files: Iterable[Path]) -> Dict[str, Any]:
    root = root.resolve()
    paths = [_resolve_scoped_file(root, requested) for requested in requested_files]
    if not paths:
        raise InventoryError("at least one source file is required")
    if len(set(paths)) != len(paths):
        raise InventoryError("source scope contains duplicate files")

    files: List[Dict[str, Any]] = []
    totals = {"files": 0, "lines": 0, "functions": 0, "external_or_public_functions": 0, "signals": 0}
    for path in sorted(paths):
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        functions = _functions(text)
        signals = _signals(text)
        line_count = len(text.splitlines())
        relative = path.relative_to(root).as_posix()
        files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "lines": line_count,
                "functions": functions,
                "signals": signals,
            }
        )
        totals["files"] += 1
        totals["lines"] += line_count
        totals["functions"] += len(functions)
        totals["external_or_public_functions"] += sum(
            function["visibility"] in {"external", "public"} for function in functions
        )
        totals["signals"] += len(signals)

    return {
        "schema_version": 1,
        "method": "static UTF-8 source inventory; no compilation or execution",
        "root": str(root),
        "totals": totals,
        "files": files,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inventory defensive Solidity review primitives")
    parser.add_argument("--root", type=Path, required=True, help="root containing the bounded source scope")
    parser.add_argument("--file", type=Path, action="append", required=True, help="scoped .sol file, repeatable")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = inventory(args.root, args.file)
    except (InventoryError, OSError, UnicodeError) as exc:
        print(json.dumps({"status": "invalid", "errors": [str(exc)]}, indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
