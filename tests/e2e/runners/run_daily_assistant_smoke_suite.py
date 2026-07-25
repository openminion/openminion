#!/usr/bin/env python3.11
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = (
    ROOT.parent
    / "docs"
    / "reference"
    / "openminion-day-to-day-assistant-scenario-catalog-2026-07-23.md"
)
DISPOSITIONS = frozenset(
    {
        "pass",
        "real_regression",
        "provider_residual",
        "harness_issue",
        "governance_friction",
        "blocked_external",
        "unclassified",
    }
)
LIVE_SCENARIOS = frozenset(
    {
        "D2AR-S01",
        "D2AR-S02",
        "D2AR-S03",
        "D2AR-S04",
        "D2AR-S05",
        "D2AR-S06",
        "D2AR-S07",
        "D2AR-S08",
        "D2AR-S09",
        "D2AR-S11",
    }
)


@dataclass(frozen=True)
class ScenarioDefinition:
    scenario_id: str
    owners: tuple[str, ...]
    allowed_dispositions: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    disposition: str
    owner: str
    missing_prerequisite: str = ""
    evidence_refs: tuple[dict[str, Any], ...] = ()
    message: str = ""

    def __post_init__(self) -> None:
        if self.disposition not in DISPOSITIONS:
            raise ValueError(f"unknown disposition: {self.disposition}")
        if self.disposition == "pass" and not self.evidence_refs:
            raise ValueError(f"{self.scenario_id} pass requires structured evidence")
        if self.disposition == "blocked_external" and not self.missing_prerequisite:
            raise ValueError(
                f"{self.scenario_id} blocked_external requires missing_prerequisite"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DailyAssistantSmokeLedger:
    schema_version: str = "openminion.daily_assistant_smoke.v1"
    generated_at: str = field(
        default_factory=lambda: (
            datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
    )
    catalog_path: str = str(CATALOG_PATH)
    mode: str = "hermetic"
    results: list[ScenarioResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        summary = {
            disposition: sum(
                1 for result in self.results if result.disposition == disposition
            )
            for disposition in sorted(DISPOSITIONS)
        }
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "catalog_path": self.catalog_path,
            "mode": self.mode,
            "summary": summary,
            "results": [result.to_dict() for result in self.results],
        }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    definitions = _load_catalog_definitions(Path(args.catalog))
    output_dir = _resolve_output_dir(args.output_dir)
    ledger = DailyAssistantSmokeLedger(
        catalog_path=str(Path(args.catalog).resolve()),
        mode="live" if args.include_live else "hermetic",
    )
    for definition in definitions:
        ledger.results.append(
            _run_scenario(definition, args=args, output_dir=output_dir)
        )
    payload = ledger.to_dict()
    ledger_path = output_dir / "daily-assistant-smoke-ledger.json"
    ledger_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_summary(payload, ledger_path=ledger_path)
    return 1 if payload["summary"]["real_regression"] else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the OpenMinion day-to-day assistant smoke ledger."
    )
    parser.add_argument("--catalog", default=str(CATALOG_PATH), help="Scenario catalog")
    parser.add_argument("--output-dir", default="", help="Directory for ledger output")
    parser.add_argument(
        "--include-live",
        action="store_true",
        help="Attempt live/account-backed scenarios when prerequisites are configured",
    )
    parser.add_argument("--json", action="store_true", help="Print ledger JSON")
    return parser.parse_args(argv)


def _load_catalog_definitions(catalog_path: Path) -> list[ScenarioDefinition]:
    text = catalog_path.read_text(encoding="utf-8")
    definitions: list[ScenarioDefinition] = []
    for line in text.splitlines():
        if not line.startswith("| `D2AR-S"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 9:
            continue
        definitions.append(
            ScenarioDefinition(
                scenario_id=cells[0].strip("`"),
                owners=_split_backtick_tokens(cells[7]),
                allowed_dispositions=tuple(
                    DISPOSITIONS if cells[8] == "all dispositions" else ()
                ),
            )
        )
    if len(definitions) != 12:
        raise RuntimeError(f"expected 12 catalog scenarios, found {len(definitions)}")
    return definitions


def _split_backtick_tokens(raw: str) -> tuple[str, ...]:
    values = [part for idx, part in enumerate(raw.split("`")) if idx % 2 == 1]
    return tuple(value for value in values if value)


def _resolve_output_dir(raw_output_dir: str) -> Path:
    if raw_output_dir:
        output_dir = Path(raw_output_dir).expanduser().resolve()
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output_dir = ROOT / ".openminion" / "runtime" / "daily-assistant-smoke" / stamp
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _run_scenario(
    definition: ScenarioDefinition,
    *,
    args: argparse.Namespace,
    output_dir: Path,
) -> ScenarioResult:
    if definition.scenario_id == "D2AR-S08":
        return _run_reminder_lifecycle(definition, output_dir=output_dir)
    if definition.scenario_id == "D2AR-S09":
        return _run_proactive_noop(definition, output_dir=output_dir)
    if definition.scenario_id == "D2AR-S10":
        return _run_memory_control(definition, output_dir=output_dir)
    if definition.scenario_id == "D2AR-S12":
        return _run_readiness(definition, output_dir=output_dir)
    if definition.scenario_id in LIVE_SCENARIOS:
        return _blocked_live_scenario(definition, include_live=bool(args.include_live))
    return ScenarioResult(
        scenario_id=definition.scenario_id,
        disposition="unclassified",
        owner=_primary_owner(definition),
        message="Scenario is present in catalog but has no runner branch.",
    )


def _run_reminder_lifecycle(
    definition: ScenarioDefinition,
    *,
    output_dir: Path,
) -> ScenarioResult:
    from openminion.modules.tool.runtime import RuntimeContext
    from openminion.modules.tool.runtime.policy import Policy
    from openminion.tools.task.reminder_ux import (
        ReminderControlScenario,
        run_reminder_control_scenario,
    )

    workspace = output_dir / "reminder-workspace"
    run_root = output_dir / "reminder-run"
    workspace.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    context = RuntimeContext(
        policy=Policy(
            raw={
                "workspace_root": str(workspace),
                "context_metadata": {"agent_id": "agent-daily-smoke"},
                "paths": {
                    "read_allow": [str(workspace)],
                    "write_allow": [str(workspace)],
                    "deny": [],
                },
                "tools": {"allow_prefix": [""]},
            }
        ),
        workspace=workspace,
        run_root=run_root,
        scope="WRITE_SAFE",
        confirm=False,
    )
    result = run_reminder_control_scenario(
        context=context,
        scenario=ReminderControlScenario(
            instruction="Remind me to check the daily assistant smoke ledger.",
            name="daily-assistant-smoke-reminder",
            schedule={"kind": "every", "every_ms": 60_000},
            delivery_destination="focus:daily-assistant-smoke",
        ),
    )
    evidence_path = output_dir / "reminder-lifecycle-result.json"
    evidence_path.write_text(
        json.dumps(result.as_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return ScenarioResult(
        scenario_id=definition.scenario_id,
        disposition="blocked_external",
        owner=_primary_owner(definition),
        missing_prerequisite="live Focus or configured channel delivery receipt not available in hermetic runner",
        evidence_refs=(
            {
                "kind": "reminder_lifecycle_result",
                "path": str(evidence_path),
                "proof_mode": result.proof_mode,
                "task_id": result.task_id,
                "delivery_destination": result.delivery_destination,
                "delivery_event_id": result.delivery_event_id,
                "history_event_id": result.history_event_id,
                "final_state": result.final_state,
                "task_complete_supported": result.task_complete_supported,
            },
        ),
        message=(
            "Reminder schedule/list/show/pause/resume/cancel lifecycle passed; "
            "live visible delivery remains external setup."
        ),
    )


def _run_proactive_noop(
    definition: ScenarioDefinition,
    *,
    output_dir: Path,
) -> ScenarioResult:
    from openminion.tools.task.reminder_ux import run_proactive_noop_scenario

    result = run_proactive_noop_scenario()
    evidence_path = output_dir / "proactive-noop-result.json"
    evidence_path.write_text(
        json.dumps(result.as_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not result.active_result.get("scheduled") or result.no_op_result.get(
        "scheduled"
    ):
        return ScenarioResult(
            scenario_id=definition.scenario_id,
            disposition="real_regression",
            owner=_primary_owner(definition),
            evidence_refs=(
                {"kind": "proactive_noop_result", "path": str(evidence_path)},
            ),
            message="Proactive active/no-op contract regressed.",
        )
    return ScenarioResult(
        scenario_id=definition.scenario_id,
        disposition="pass",
        owner=_primary_owner(definition),
        evidence_refs=(
            {
                "kind": "proactive_noop_result",
                "path": str(evidence_path),
                "proof_mode": result.proof_mode,
                "active_tick_id": result.active_tick_id,
                "no_op_tick_id": result.no_op_tick_id,
                "active_event_ids": list(result.active_event_ids),
                "no_op_event_ids": list(result.no_op_event_ids),
            },
        ),
        message="Proactive active tick schedules and disabled no-op stays quiet.",
    )


def _run_memory_control(
    definition: ScenarioDefinition,
    *,
    output_dir: Path,
) -> ScenarioResult:
    from openminion.modules.memory.runtime.promotion import PromotionPolicy
    from openminion.modules.memory.service import MemoryService
    from openminion.modules.memory.storage import (
        AuditedMemoryStore,
        InMemoryMemoryAuditSink,
    )
    from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
    from openminion.modules.tool.base import ToolExecutionContext
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.tools.memory import REGISTRAR
    from openminion.tools.memory.control_ux import (
        MemoryControlScenario,
        run_memory_control_scenario,
    )

    sink = InMemoryMemoryAuditSink()
    store = AuditedMemoryStore(SQLiteMemoryStore(output_dir / "memory.db"), sink=sink)
    service = MemoryService(store=store, policy=PromotionPolicy())
    registry = ToolRegistry()
    REGISTRAR.register(registry)
    result = run_memory_control_scenario(
        registry=registry,
        context=ToolExecutionContext(
            channel="console",
            target="daily-assistant-smoke",
            session_id="daily-assistant-smoke-memory",
            metadata={},
            memory_service=service,
        ),
        scenario=MemoryControlScenario(
            scope="session:daily-assistant-smoke-memory",
            record_type="fact",
            title="Daily smoke explicit memory",
            content={"value": "temporary smoke memory"},
            search_query="temporary smoke memory",
            correction_title="Daily smoke explicit memory correction",
            correction_content={"value": "corrected temporary smoke memory"},
            forget_reason="daily assistant smoke cleanup",
            tags=("daily-assistant-smoke",),
        ),
    )
    evidence_path = output_dir / "memory-control-result.json"
    evidence_path.write_text(
        json.dumps(result.as_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return ScenarioResult(
        scenario_id=definition.scenario_id,
        disposition="pass",
        owner=_primary_owner(definition),
        evidence_refs=(
            {
                "kind": "memory_control_result",
                "path": str(evidence_path),
                "record_id": result.original_record_id,
                "correction_record_id": result.correction_record_id,
                "forget_deleted": result.forget_deleted,
                "audit_event_types": list(result.audit_event_types),
            },
        ),
        message="Explicit memory write/search/correction/forget scenario passed.",
    )


def _run_readiness(
    definition: ScenarioDefinition,
    *,
    output_dir: Path,
) -> ScenarioResult:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "missing-config.json"
        command = [
            sys.executable,
            "-m",
            "openminion",
            "--config",
            str(config_path),
            "status",
            "readiness",
            "--json",
        ]
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=_runner_env(),
            text=True,
            capture_output=True,
            timeout=120,
        )
    payload = _parse_json_payload(completed.stdout)
    if completed.returncode or not payload:
        return ScenarioResult(
            scenario_id=definition.scenario_id,
            disposition="harness_issue",
            owner=_primary_owner(definition),
            message=completed.stderr or "readiness command did not return JSON",
        )
    evidence_path = output_dir / "readiness-result.json"
    evidence_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    checks = {
        item.get("id") for item in payload.get("checks", []) if isinstance(item, dict)
    }
    required = {
        "provider",
        "search_fetch",
        "browser",
        "gws",
        "channels",
        "task_cron",
        "memory",
        "policy",
    }
    if payload.get("overall") != "blocked" or not required.issubset(checks):
        return ScenarioResult(
            scenario_id=definition.scenario_id,
            disposition="real_regression",
            owner=_primary_owner(definition),
            evidence_refs=({"kind": "readiness_payload", "path": str(evidence_path)},),
            message="Readiness payload shape or clean-profile blocked status regressed.",
        )
    return ScenarioResult(
        scenario_id=definition.scenario_id,
        disposition="pass",
        owner=_primary_owner(definition),
        evidence_refs=(
            {
                "kind": "readiness_payload",
                "path": str(evidence_path),
                "overall": payload.get("overall"),
                "check_ids": sorted(checks),
            },
        ),
        message="Clean-profile readiness diagnosis returned structured safe actions.",
    )


def _blocked_live_scenario(
    definition: ScenarioDefinition,
    *,
    include_live: bool,
) -> ScenarioResult:
    missing = _missing_live_prerequisite(
        definition.scenario_id, include_live=include_live
    )
    return ScenarioResult(
        scenario_id=definition.scenario_id,
        disposition="blocked_external",
        owner=_primary_owner(definition),
        missing_prerequisite=missing,
        message="Live scenario is cataloged but not run without explicit prerequisites.",
    )


def _missing_live_prerequisite(scenario_id: str, *, include_live: bool) -> str:
    if not include_live:
        return "operator did not pass --include-live"
    scenario_env = {
        "D2AR-S01": "OPENMINION_DAILY_SMOKE_SEARCH_FETCH_READY",
        "D2AR-S02": "OPENMINION_DAILY_SMOKE_BROWSER_PROFILE",
        "D2AR-S03": "OPENMINION_DAILY_SMOKE_GWS_GMAIL_ACCOUNT",
        "D2AR-S04": "OPENMINION_DAILY_SMOKE_GWS_GMAIL_SEND_ACCOUNT",
        "D2AR-S05": "OPENMINION_DAILY_SMOKE_GWS_CALENDAR_ID",
        "D2AR-S06": "OPENMINION_DAILY_SMOKE_GWS_DRIVE_FOLDER_ID",
        "D2AR-S07": "OPENMINION_DAILY_SMOKE_GWS_PEOPLE_ACCOUNT",
        "D2AR-S08": "OPENMINION_DAILY_SMOKE_DELIVERY_CHANNEL",
        "D2AR-S09": "OPENMINION_DAILY_SMOKE_PROACTIVE_FIXTURE",
        "D2AR-S11": "OPENMINION_DAILY_SMOKE_CROSS_APP_READY",
    }[scenario_id]
    return (
        scenario_env
        if not os.environ.get(scenario_env)
        else "live executor branch not implemented"
    )


def _runner_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return env


def _parse_json_payload(stdout: str) -> dict[str, Any] | None:
    lines = stdout.splitlines()
    for idx, line in enumerate(lines):
        if line.strip() != "{":
            continue
        try:
            payload = json.loads("\n".join(lines[idx:]))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _primary_owner(definition: ScenarioDefinition) -> str:
    return definition.owners[-1] if definition.owners else "D2AR-16"


def _print_summary(payload: dict[str, Any], *, ledger_path: Path) -> None:
    summary = payload["summary"]
    print(
        "daily assistant smoke: "
        f"pass={summary['pass']} blocked_external={summary['blocked_external']} "
        f"real_regression={summary['real_regression']} ledger={ledger_path}"
    )
    for result in payload["results"]:
        print(
            f"- {result['scenario_id']}: {result['disposition']} "
            f"owner={result['owner']} "
            f"missing={result.get('missing_prerequisite') or '-'}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
