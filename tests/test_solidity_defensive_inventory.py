from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from experiments.solidity_defensive_inventory import InventoryError, inventory


class SolidityDefensiveInventoryTests(unittest.TestCase):
    def test_inventory_records_entrypoints_and_dynamic_returndata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Bounded.sol"
            source.write_text(
                """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.23;
contract Bounded {
    function validate(bytes calldata data) external returns (bool) {
        (bool success, bytes memory result) = address(this).delegatecall(data);
        return success && result.length > 0;
    }
}
""",
                encoding="utf-8",
            )

            result = inventory(root, [Path("Bounded.sol")])

            self.assertEqual(result["totals"]["files"], 1)
            self.assertEqual(result["totals"]["external_or_public_functions"], 1)
            signals = {item["id"] for item in result["files"][0]["signals"]}
            self.assertIn("delegatecall", signals)
            self.assertIn("delegatecall_dynamic_returndata_capture", signals)

    def test_inventory_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.mkdir()
            outside = Path(temporary) / "Outside.sol"
            outside.write_text("pragma solidity ^0.8.23;", encoding="utf-8")

            with self.assertRaises(InventoryError):
                inventory(root, [outside])

    def test_inventory_rejects_duplicate_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Bounded.sol"
            source.write_text("pragma solidity ^0.8.23;", encoding="utf-8")

            with self.assertRaises(InventoryError):
                inventory(root, [Path("Bounded.sol"), Path("Bounded.sol")])


if __name__ == "__main__":
    unittest.main()
