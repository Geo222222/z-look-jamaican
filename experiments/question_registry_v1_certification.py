from __future__ import annotations

import argparse
import json
from pathlib import Path

from autonomous_kernel.questions import (
    build_question_registry_v1_qualified,
    certify_question_registry_v1,
    default_question_registry_v1,
)


# Fixed preregistration times are part of the evidence identity. They are not
# wall-clock generation times, so independent reruns produce the same artifact.
REGISTERED_AT_NS = 1_788_700_000_000_000_000
EFFECTIVE_AT_NS = REGISTERED_AT_NS + 1
QUALIFIED_KNOWN_AT_NS = REGISTERED_AT_NS + 2
QUALIFIED_EFFECTIVE_AT_NS = REGISTERED_AT_NS + 3


def build_certificate():
    base = default_question_registry_v1(
        registered_at_ns=REGISTERED_AT_NS,
        effective_at_ns=EFFECTIVE_AT_NS,
    )
    snapshot = build_question_registry_v1_qualified(
        base,
        known_at_ns=QUALIFIED_KNOWN_AT_NS,
        effective_at_ns=QUALIFIED_EFFECTIVE_AT_NS,
    )
    return certify_question_registry_v1(snapshot)


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit frozen QUESTION_REGISTRY_V1_QUALIFIED evidence")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(build_certificate(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
