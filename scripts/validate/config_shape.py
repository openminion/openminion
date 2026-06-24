"""Validate config files against the current three-layer shape."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_GUIDE = "the config-shape migration guide"


def validate_payload(payload: dict[str, Any]) -> list[str]:
    """Return a list of violation messages; empty list means shape-compliant."""

    violations: list[str] = []

    if "agent" in payload:
        violations.append(
            "Legacy 'agent' top-level block is no longer supported. "
            "Move identity fields into 'agents.<id>' and set 'default_agent'. "
            f"See {_MIGRATION_GUIDE}."
        )

    agents = payload.get("agents") if isinstance(payload.get("agents"), dict) else {}
    for agent_id, agent_config in agents.items():
        if not isinstance(agent_config, dict):
            continue
        if "runtime_overrides" in agent_config:
            violations.append(
                f"Nested 'runtime_overrides' under agents.{agent_id} is no longer "
                f"supported. Flatten its fields to 'agents.{agent_id}.*' "
                f"(see {_MIGRATION_GUIDE} for the rename table — "
                f"'providers' → 'provider_policy', 'thinking' → 'thinking_policy', "
                f"'brain.*' → top-level on the agent)."
            )

    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    if "brain" in runtime:
        violations.append(
            "Legacy 'runtime.brain' is no longer supported. Flatten "
            "tool_schema_shortlisting_enabled, allow_background_write_authorization, "
            "and trailer_guidance_variant directly under 'runtime'. "
            f"See {_MIGRATION_GUIDE}."
        )
    if isinstance(runtime.get("thinking"), dict):
        violations.append(
            "Legacy 'runtime.thinking' (as a policy object) is no longer supported. "
            "Use 'runtime.thinking_policy' instead. "
            f"See {_MIGRATION_GUIDE}."
        )
    if isinstance(runtime.get("providers"), dict):
        violations.append(
            "Legacy 'runtime.providers' (as a policy object) is no longer supported. "
            "Use 'runtime.provider_policy' instead. "
            f"The top-level 'providers:' provider-catalog is unchanged. "
            f"See {_MIGRATION_GUIDE}."
        )

    agent_count = len(agents)
    default_agent = str(payload.get("default_agent", "") or "").strip()
    if agent_count >= 2 and not default_agent:
        valid_ids = ", ".join(repr(k) for k in sorted(agents))
        violations.append(
            f"multi-agent config requires explicit 'default_agent' "
            f"naming one of: {valid_ids}."
        )
    if default_agent and default_agent not in agents:
        valid_ids = ", ".join(repr(k) for k in sorted(agents))
        violations.append(
            f"default_agent={default_agent!r} is not present in the agents "
            f"catalog. Valid profiles: {valid_ids}."
        )

    return violations


def validate_file(path: Path) -> tuple[bool, list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, [f"{path}: invalid JSON: {exc}"]
    if not isinstance(payload, dict):
        return False, [
            f"{path}: top-level payload must be an object, got {type(payload).__name__}"
        ]
    violations = validate_payload(payload)
    return (not violations), [f"{path}: {v}" for v in violations]


def _default_targets() -> list[Path]:
    test_configs_dir = REPO_ROOT.parent / "test-configs"
    if not test_configs_dir.is_dir():
        return []
    return sorted(test_configs_dir.rglob("*.json"))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Config files to validate. If omitted, scans repo test-configs/*.json.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON results instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    targets = args.paths or _default_targets()
    if not targets:
        print("No config files to validate (and no test-configs directory found).")
        return 0

    all_violations: list[str] = []
    pass_count = 0
    fail_count = 0
    for target in targets:
        if not target.is_file():
            all_violations.append(f"{target}: not a file")
            fail_count += 1
            continue
        ok, violations = validate_file(target)
        if ok:
            pass_count += 1
        else:
            fail_count += 1
            all_violations.extend(violations)

    if args.json:
        print(
            json.dumps(
                {
                    "pass_count": pass_count,
                    "fail_count": fail_count,
                    "violations": all_violations,
                },
                indent=2,
            )
        )
    else:
        payload = {
            "pass_count": pass_count,
            "fail_count": fail_count,
            "checked": pass_count + fail_count,
            "violations": all_violations,
        }
        emit_json_report(
            "validate_config_shape",
            payload,
            summary=(
                ("checked", pass_count + fail_count),
                ("passed", pass_count),
                ("failed", fail_count),
            ),
            findings=all_violations,
            ok_message=(
                f"{pass_count} passed, {fail_count} failed "
                f"of {pass_count + fail_count} checked."
            ),
            report_stream=sys.stderr,
            json_stream=sys.stdout,
        )

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
