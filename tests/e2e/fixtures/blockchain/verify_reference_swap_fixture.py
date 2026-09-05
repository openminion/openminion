from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

FIXTURE_ROOT = Path(__file__).resolve().parent


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify(compiler_output: Path, compiler_version_output: Path) -> None:
    source_path = FIXTURE_ROOT / "ReferenceSwap.sol"
    standard_path = FIXTURE_ROOT / "reference_swap.standard.json"
    artifact_path = FIXTURE_ROOT / "reference_swap.json"
    source = source_path.read_bytes()
    standard_bytes = standard_path.read_bytes()
    standard = json.loads(standard_bytes)
    artifact = json.loads(artifact_path.read_text())
    compiled = json.loads(compiler_output.read_text())
    compiler_version = compiler_version_output.read_text()

    assert artifact["compiler_version"] == "0.8.30"
    assert re.search(r"\bVersion: 0\.8\.30\+commit\.[0-9a-f]+\b", compiler_version)
    assert standard["sources"]["ReferenceSwap.sol"]["content"] == source.decode()
    assert not [
        item for item in compiled.get("errors", []) if item.get("severity") == "error"
    ]
    contract = compiled["contracts"]["ReferenceSwap.sol"]["ReferenceSwap"]
    creation = contract["evm"]["bytecode"]["object"]
    deployed = contract["evm"]["deployedBytecode"]["object"]

    assert artifact["source_sha256"] == _sha256(source)
    assert artifact["standard_input_sha256"] == _sha256(standard_bytes)
    assert artifact["creation_bytecode_sha256"] == _sha256(bytes.fromhex(creation))
    assert artifact["deployed_bytecode_sha256"] == _sha256(bytes.fromhex(deployed))
    assert artifact["abi"] == contract["abi"]
    assert artifact["creation_bytecode"] == f"0x{creation}"
    assert artifact["deployed_bytecode"] == f"0x{deployed}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler-output", type=Path, required=True)
    parser.add_argument("--compiler-version-output", type=Path, required=True)
    args = parser.parse_args()
    verify(args.compiler_output, args.compiler_version_output)
    print("ReferenceSwap fixture verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
