#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.helpers.flagship_differentiation_proof import (  # noqa: E402
    DEFAULT_FLAGSHIP_INPUT,
    run_flagship_differentiation_proof,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the flagship differentiation proof and write an evidence packet."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output path for the evidence packet JSON.",
    )
    parser.add_argument(
        "--input",
        default=DEFAULT_FLAGSHIP_INPUT,
        help="User input to replay through the deterministic proof harness.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full artifact JSON to stdout.",
    )
    args = parser.parse_args()

    result = run_flagship_differentiation_proof(
        user_input=args.input,
        output_path=args.output,
    )
    if args.json:
        sys.stdout.write(json.dumps(result.artifact, indent=2, sort_keys=True))
        sys.stdout.write("\n")
        return 0

    sys.stdout.write(f"artifact={result.artifact_path}\n")
    sys.stdout.write(f"final_answer={result.final_answer}\n")
    return 0
