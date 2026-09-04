import json
import subprocess
import unittest
from pathlib import Path

from autonomous_kernel.store import repository_root


# These four paths are the only committed files allowed to check out as CRLF.
# They were originally hashed on Windows CRLF bytes. Do not enlarge this set
# without a new exact-byte diagnosis; new checksum-bound files must be -text.
FROZEN_CRLF_CHECKOUT_EXCEPTIONS = (
    "artifacts/evidence/apify_store/snapshot-20260821T194047Z.json",
    "artifacts/evidence/apify_store/snapshot-20260821T194850Z.json",
    "artifacts/evidence/apify_store/snapshot-20260821T195023Z.json",
    "config/treasury_destinations.yaml",
)


def _checksum_bound_paths(root: Path):
    bound = []
    sources = root / "evidence" / "sources.jsonl"
    for line in sources.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        digest = record.get("sha256")
        relative = str(record.get("path") or "").split("#", 1)[0]
        if not digest or not relative:
            continue
        bound.append(relative)
    return bound


def _check_attr(root: Path, path: str, name: str) -> str:
    output = subprocess.check_output(
        ["git", "check-attr", name, "--", path],
        cwd=root,
        text=True,
    ).strip()
    return output.rsplit(":", 1)[-1].strip()


class EvidenceCheckoutAttributeTests(unittest.TestCase):
    def test_checksum_bound_paths_are_protected_by_checkout_policy(self):
        root = Path(repository_root())
        bound = _checksum_bound_paths(root)
        self.assertGreaterEqual(len(bound), 30)
        self.assertEqual(len(bound), len(set(bound)))

        violations = []
        crlf_exceptions = set(FROZEN_CRLF_CHECKOUT_EXCEPTIONS)
        for relative in bound:
            target = root / relative
            if not target.is_file():
                violations.append("%s is checksum-bound but missing from the working tree" % relative)
                continue
            text = _check_attr(root, relative, "text")
            eol = _check_attr(root, relative, "eol")
            if relative in crlf_exceptions:
                if text != "set" or eol != "crlf":
                    violations.append("%s text=%s eol=%s (frozen CRLF exception wants text=set eol=crlf)" % (relative, text, eol))
                continue
            # Unspecified text follows core.autocrlf and is not a safe policy.
            if text != "unset":
                violations.append("%s text=%s eol=%s (checksum-bound exact-byte path wants -text)" % (relative, text, eol))
        self.assertEqual([], violations)

    def test_frozen_crlf_exceptions_remain_exactly_the_legacy_four(self):
        root = Path(repository_root())
        self.assertEqual(4, len(FROZEN_CRLF_CHECKOUT_EXCEPTIONS))
        self.assertEqual(4, len(set(FROZEN_CRLF_CHECKOUT_EXCEPTIONS)))
        violations = []
        for relative in FROZEN_CRLF_CHECKOUT_EXCEPTIONS:
            if not (root / relative).is_file():
                violations.append("%s missing" % relative)
                continue
            text = _check_attr(root, relative, "text")
            eol = _check_attr(root, relative, "eol")
            if text != "set" or eol != "crlf":
                violations.append("%s text=%s eol=%s (want text=set eol=crlf)" % (relative, text, eol))
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
