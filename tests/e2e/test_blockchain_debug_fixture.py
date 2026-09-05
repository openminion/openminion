from __future__ import annotations

import json

from tests.e2e.fixtures.blockchain.verify_reference_swap_fixture import FIXTURE_ROOT


def test_reference_swap_fixture_has_exact_composition_api() -> None:
    artifact = json.loads((FIXTURE_ROOT / "reference_swap.json").read_text())

    assert artifact["compiler_version"] == "0.8.30"
    assert {entry["name"] for entry in artifact["abi"]} == {
        "MinimumOutput",
        "Swap",
        "outputOf",
        "quote",
        "swap",
    }
    swap = next(entry for entry in artifact["abi"] if entry["name"] == "swap")
    assert [item["type"] for item in swap["inputs"][0]["components"]] == [
        "address",
        "uint256",
        "uint256",
    ]
    assert artifact["creation_bytecode"].startswith("0x")
    assert artifact["deployed_bytecode"].startswith("0x")
