from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.e2e.cli.focus.harness import FocusProbe  # noqa: E402
from tests.e2e.cli.focus.harness.scenarios import FocusScenario  # noqa: E402
from tests.helpers.live_e2e_profiles import resolve_live_framework_root  # noqa: E402
from tests.helpers.runtime_roots import isolate_runtime_roots  # noqa: E402

FRAMEWORK_ROOT = resolve_live_framework_root(ROOT)
CONFIG_PATH = FRAMEWORK_ROOT / "test-configs" / "per-agent-minimax-official.json"
EVIDENCE_ROOT = FRAMEWORK_ROOT / "workspace-tmp" / "commerce-e2e" / "focus"
ENTRYPOINT = "tests.e2e.runners.commerce_focus_entrypoint"
EXPECTED_INVENTORY = {
    0: [],
    2: ["commerce.inspect", "commerce.prepare_order"],
    3: ["commerce.inspect", "commerce.place_order", "commerce.prepare_order"],
    5: [
        "commerce.apply_order_action",
        "commerce.inspect",
        "commerce.place_order",
        "commerce.prepare_order",
        "commerce.prepare_order_action",
    ],
}


class CommerceFocusProbe(FocusProbe):
    def __init__(
        self,
        *,
        phase: int,
        oracle_path: Path,
        scenario: str,
        **kwargs,
    ) -> None:
        super().__init__(entrypoint_module=ENTRYPOINT, **kwargs)
        self.phase = phase
        self.oracle_path = oracle_path
        self.scenario = scenario

    def environment(self) -> dict[str, str]:
        env = super().environment()
        env.update(
            {
                "OPENMINION_COMMERCE_TEST_PHASE": str(self.phase),
                "OPENMINION_COMMERCE_ORACLE_PATH": str(self.oracle_path),
                "OPENMINION_COMMERCE_TEST_SCENARIO": self.scenario,
            }
        )
        return env


def _probe(
    *, phase: int, scenario: str, data_root: Path, oracle_path: Path, session_id: str
) -> CommerceFocusProbe:
    return CommerceFocusProbe(
        python_bin=Path(sys.executable),
        openminion_root=ROOT,
        framework_root=FRAMEWORK_ROOT,
        data_root=data_root,
        config_path=CONFIG_PATH,
        agent_id="minimax-m2-7",
        workdir=FRAMEWORK_ROOT,
        session_id=session_id,
        include_project_context=False,
        phase=phase,
        oracle_path=oracle_path,
        scenario=scenario,
    )


def _scenario(
    scenario_id: str,
    prompt: str,
    *,
    approval_reply: str | None = None,
    timeout: int = 600,
) -> FocusScenario:
    return FocusScenario(
        scenario_id=scenario_id,
        prompt=prompt,
        expected_markers=(),
        timeout=timeout,
        requires_approval=approval_reply is not None,
        approval_reply=approval_reply or "yes",
        include_project_context=False,
    )


def _rows(database: Path, table: str) -> list[dict]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
    finally:
        connection.close()


def _tool_results(data_root: Path) -> list[dict]:
    results: list[dict] = []
    for database in data_root.rglob("*.db"):
        try:
            rows = _rows(database, "messages")
        except sqlite3.Error:
            continue
        for row in rows:
            try:
                metadata = json.loads(row.get("metadata_json") or "{}")
                values = json.loads(metadata.get("tool_results") or "[]")
            except (json.JSONDecodeError, TypeError):
                continue
            results.extend(
                value
                for value in values
                if str(value.get("tool_name", "")).startswith("commerce.")
                or value.get("tool_name") == "task.watch"
            )
    return results


def _database_rows(data_root: Path, relative: str, table: str) -> list[dict]:
    path = data_root / relative
    return _rows(path, table) if path.exists() else []


def _typed_result_projection(results: list[dict]) -> list[dict]:
    projected: list[dict] = []
    for result in results:
        envelope = result.get("data")
        envelope = envelope if isinstance(envelope, dict) else {}
        data = envelope.get("data")
        data = data if isinstance(data, dict) else {}
        lifecycle = data.get("lifecycle")
        projected.append(
            {
                "call_id": str(result.get("call_id", "")),
                "tool_name": str(result.get("tool_name", "")),
                "ok": result.get("ok") is True,
                "state": str(envelope.get("state", "")),
                "preparation_ref": str(data.get("preparation_ref", "")),
                "order_ref": str(data.get("order_ref", "")),
                "action_ref": str(data.get("action_ref", "")),
                "reference": str(data.get("reference", "")),
                "revision": str(data.get("revision", "")),
                "reason_code": str(data.get("reason_code", "")),
                "task_id": str(data.get("task_id", "")),
                "lifecycle": lifecycle if isinstance(lifecycle, dict) else {},
            }
        )
    return projected


def _runtime_evidence(data_root: Path, results: list[dict]) -> dict:
    policy_rows = _database_rows(
        data_root,
        "policy/policy.db",
        "policy_pending_confirmations",
    )
    order_rows = _database_rows(
        data_root,
        "commerce/commerce.db",
        "commerce_orders",
    )
    attempt_rows = _database_rows(
        data_root,
        "commerce/commerce.db",
        "commerce_attempts",
    )
    telemetry_rows = _database_rows(
        data_root,
        "telemetry/telemetry.db",
        "events",
    )
    task_rows = []
    for database in data_root.rglob("*.db"):
        try:
            rows = _rows(database, "cron_jobs")
        except sqlite3.Error:
            continue
        for row in rows:
            payload = json.loads(row.get("payload_json") or "{}")
            routine = (payload.get("_openminion_watch") or {}).get("routine")
            if (
                isinstance(routine, dict)
                and routine.get("routine_kind") == "commerce_order"
            ):
                task_rows.append(
                    {
                        "task_id": row.get("job_id"),
                        "enabled": bool(row.get("enabled")),
                        "local_order_ref": routine.get("config", {}).get(
                            "local_order_ref"
                        ),
                        "material_cursor": routine.get("cursor", {}).get(
                            "material_cursor"
                        ),
                    }
                )
    return {
        "tool_results": _typed_result_projection(results),
        "policy": [
            {
                key: row.get(key)
                for key in (
                    "approval_id",
                    "subject_id",
                    "tool",
                    "method",
                    "invocation_hash",
                    "invocation_id",
                    "session_id",
                    "state",
                    "resolution_action",
                    "grant_id",
                )
            }
            for row in policy_rows
            if row.get("tool") == "commerce"
        ],
        "orders": [
            {
                key: row.get(key)
                for key in (
                    "order_id",
                    "subject_id",
                    "preparation_id",
                    "placement_attempt_id",
                )
            }
            for row in order_rows
        ],
        "attempts": [
            {
                key: row.get(key)
                for key in (
                    "attempt_id",
                    "subject_id",
                    "kind",
                    "target_id",
                    "idempotency_key",
                    "authorization_hash",
                    "attempt_count",
                    "state",
                    "active",
                )
            }
            for row in attempt_rows
        ],
        "audit_events": [
            {
                "event_type": row.get("event_type"),
                "data": json.loads(row.get("data") or "{}"),
            }
            for row in telemetry_rows
            if row.get("event_type") == "tool.commerce.action"
        ],
        "tasks": task_rows,
    }


def _identity(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


def _source_commit() -> str:
    import subprocess

    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def _read_oracle(oracle_path: Path) -> dict:
    assert oracle_path.exists(), f"missing commerce Focus oracle: {oracle_path}"
    return json.loads(oracle_path.read_text())


def _run_inventory(phase: int, root: Path) -> dict:
    data_root = root / f"phase-{phase}"
    oracle_path = data_root / "oracle.json"
    probe = _probe(
        phase=phase,
        scenario="inventory",
        data_root=data_root,
        oracle_path=oracle_path,
        session_id=f"commerce-inventory-{phase}",
    )
    with probe.session(rows=36, cols=140) as session:
        probe.wait_ready(session)
    oracle = _read_oracle(oracle_path)
    assert oracle["phase"] == phase
    assert oracle["inventory"] == EXPECTED_INVENTORY[phase]
    assert oracle["fixture_ledger"] == []
    return oracle


def _run_order_care(root: Path) -> tuple[dict, dict, list[dict]]:
    data_root = root / "order-care"
    oracle_path = data_root / "oracle.json"
    probe = _probe(
        phase=5,
        scenario="order-care",
        data_root=data_root,
        oracle_path=oracle_path,
        session_id="commerce-order-care",
    )
    turns = (
        _scenario(
            "inspect-product",
            'tool commerce.inspect {"kind":"product","product_id":"product-1",'
            '"offer_id":"offer-1"}',
        ),
        _scenario(
            "deny-prepare",
            'tool commerce.prepare_order {"items":[{"offer_id":"offer-1",'
            '"variant_id":"standard","quantity":1}]}',
            approval_reply="no",
        ),
        _scenario(
            "approve-prepare",
            'tool commerce.prepare_order {"items":[{"offer_id":"offer-1",'
            '"variant_id":"standard","quantity":1}]}',
            approval_reply="yes",
        ),
        _scenario(
            "place-order",
            "Call commerce.place_order now using every exact field from the latest "
            "successful preparation. Stop for the separate human approval; do not "
            "call another tool to answer the approval prompt.",
            approval_reply="yes",
        ),
        _scenario(
            "prepare-cancel",
            "Call only commerce.prepare_order_action exactly once with "
            'local_order_ref="order-1", order_revision="order-1:r1", and '
            'kind="cancel". Do not inspect files or call any other tool.',
        ),
        _scenario(
            "apply-cancel",
            "Call commerce.apply_order_action now using every exact field from the "
            "latest action preparation. Stop for human approval before applying it; "
            "do not call another tool to answer the approval prompt.",
            approval_reply="yes",
        ),
        _scenario(
            "watch-order",
            'tool task.watch {"description":"Watch order-1",'
            '"check_instruction":"Inspect order-1 lifecycle","interval_minutes":1,'
            '"max_checks":5,"alert_condition":"the order lifecycle changes",'
            '"delivery":"announce","routine":{"routine_kind":"commerce_order",'
            '"config":{"subject_id":"local","local_order_ref":"order-1",'
            '"expires_at":"2099-01-01T00:00:00Z",'
            '"terminal_policy":"fully_settled","max_failures":3},'
            '"cursor":{"failure_count":0}}}',
            approval_reply="yes",
        ),
    )
    approvals: list[dict[str, object]] = []
    transcript: list[str] = []
    expected_tool = {
        "inspect-product": "commerce.inspect",
        "approve-prepare": "commerce.prepare_order",
        "place-order": "commerce.place_order",
        "prepare-cancel": "commerce.prepare_order_action",
        "apply-cancel": "commerce.apply_order_action",
        "watch-order": "task.watch",
    }
    with probe.session(rows=52, cols=180) as session:
        probe.wait_ready(session)
        for turn in turns:
            tool_name = expected_tool.get(turn.scenario_id)
            count_before = sum(
                result.get("tool_name") == tool_name
                for result in _tool_results(data_root)
            )
            approval_count_before = len(approvals)
            transcript.append(probe.run_turn(session, turn, approval_events=approvals))
            if turn.scenario_id == "deny-prepare":
                assert len(approvals) > approval_count_before
                assert approvals[-1]["decision"] == "no"
                continue
            count_after = sum(
                result.get("tool_name") == tool_name
                for result in _tool_results(data_root)
            )
            assert count_after > count_before, (
                f"{turn.scenario_id} did not execute {tool_name}"
            )
    oracle = _read_oracle(oracle_path)
    (EVIDENCE_ROOT / "order-care-transcript.txt").write_text("\n".join(transcript))
    results = _tool_results(data_root)
    ledger = oracle["fixture_ledger"]
    assert [item["operation"] for item in ledger] == [
        "prepare_order",
        "place_order",
        "apply_action",
    ]
    assert [item["decision"] for item in approvals].count("no") == 1
    assert [item["decision"] for item in approvals].count("yes") >= 3
    names = [item.get("tool_name") for item in results]
    assert names.count("commerce.prepare_order") >= 2
    assert names.count("commerce.place_order") == 1
    assert names.count("commerce.prepare_order_action") == 1
    assert names.count("commerce.apply_order_action") == 1
    assert names.count("task.watch") == 1
    applied = next(
        item
        for item in results
        if item.get("tool_name") == "commerce.apply_order_action"
    )
    assert applied.get("data", {}).get("state") == "pending"
    assert oracle["task_runs"] == [
        {
            "task_id": oracle["task_runs"][0]["task_id"],
            "material_cursor": "order-1:r1",
            "delivery_requested": True,
        },
        {
            "task_id": oracle["task_runs"][0]["task_id"],
            "material_cursor": "order-1:r1",
            "delivery_requested": False,
        },
        {
            "task_id": oracle["task_runs"][0]["task_id"],
            "material_cursor": "order-1:r2",
            "delivery_requested": True,
        },
    ]
    runtime_evidence = _runtime_evidence(data_root, results)
    decisions = [row["state"] for row in runtime_evidence["policy"]]
    assert "denied" in decisions and decisions.count("allowed") >= 3
    assert len(runtime_evidence["orders"]) == 1
    assert runtime_evidence["tasks"] == [
        {
            "task_id": oracle["task_runs"][0]["task_id"],
            "enabled": True,
            "local_order_ref": "order-1",
            "material_cursor": "order-1:r2",
        }
    ]
    attempts = runtime_evidence["attempts"]
    assert {row["kind"] for row in attempts} == {
        "preparation",
        "placement",
        "action",
    }
    authorization_hashes = {
        row["invocation_hash"]
        for row in runtime_evidence["policy"]
        if row["state"] == "allowed"
    }
    assert all(
        row["kind"] == "preparation"
        or row["authorization_hash"] in authorization_hashes
        for row in attempts
    )
    policy_by_hash = {
        row["invocation_hash"]: row
        for row in runtime_evidence["policy"]
        if row["state"] == "allowed"
    }
    assert len(runtime_evidence["audit_events"]) == 3
    tool_by_name = {
        row["tool_name"]: row
        for row in runtime_evidence["tool_results"]
        if row["tool_name"]
    }
    for event in runtime_evidence["audit_events"]:
        data = event["data"]
        policy = policy_by_hash[data["invocation_id"]]
        assert data["policy_approval_id"] == policy["approval_id"]
        assert data["policy_grant_id"] == policy["grant_id"]
        tool = tool_by_name[data["tool_name"]]
        subject_ref = (
            tool["preparation_ref"]
            if data["action"] == "prepare_order"
            else tool["order_ref"]
        )
        assert data["order_id"] == _identity(subject_ref)
        assert data["attempt_id"] == _identity(
            {
                "invocation_id": data["invocation_id"],
                "tool_call_id": tool["call_id"],
                "action": data["action"],
            }
        )
        if data["action"] == "prepare_order":
            assert any(attempt["kind"] == "preparation" for attempt in attempts)
        else:
            assert any(
                attempt["authorization_hash"] == data["invocation_id"]
                for attempt in attempts
            )
    return oracle, runtime_evidence, approvals


def _run_handoff(root: Path) -> tuple[dict, dict]:
    data_root = root / "unsupported-action"
    oracle_path = data_root / "oracle.json"
    probe = _probe(
        phase=5,
        scenario="unsupported-action",
        data_root=data_root,
        oracle_path=oracle_path,
        session_id="commerce-unsupported-action",
    )
    with probe.session(rows=40, cols=160) as session:
        probe.wait_ready(session)
        transcript = probe.run_turn(
            session,
            _scenario(
                "unsupported-action",
                "Call only commerce.prepare_order_action exactly once with "
                'local_order_ref="order-1", order_revision="order-1:r1", and '
                'kind="cancel". Use the typed result and do not inspect files or call '
                "any other tool.",
            ),
        )
    oracle = _read_oracle(oracle_path)
    (EVIDENCE_ROOT / "unsupported-action-transcript.txt").write_text(transcript)
    results = _tool_results(data_root)
    action_result = next(
        item
        for item in results
        if item.get("tool_name") == "commerce.prepare_order_action"
    )
    assert action_result.get("data", {}).get("state") == "handoff_required"
    assert action_result.get("data", {}).get("data", {}).get("reason_code") == (
        "unsupported_action"
    )
    assert [item["operation"] for item in oracle["fixture_ledger"]] == [
        "prepare_order",
        "place_order",
    ]
    return oracle, _runtime_evidence(data_root, results)


def main() -> int:
    if os.getenv("OPENMINION_LIVE_CLI_FOCUS_E2E") != "1":
        raise RuntimeError("OPENMINION_LIVE_CLI_FOCUS_E2E=1 is required")
    config = json.loads(CONFIG_PATH.read_text())
    credential_env = config["providers"]["openai"]["api_key_env"]
    configured_env = config.get("runtime", {}).get("env", {})
    if (
        not os.getenv(credential_env)
        and not str(configured_env.get(credential_env, "")).strip()
    ):
        raise RuntimeError(f"{credential_env} is required")
    shutil.rmtree(EVIDENCE_ROOT, ignore_errors=True)
    EVIDENCE_ROOT.mkdir(parents=True)
    runtime_root = isolate_runtime_roots(prefix="openminion-commerce-focus-").parent
    inventories = {
        str(phase): _run_inventory(phase, runtime_root) for phase in (0, 2, 3, 5)
    }
    order_oracle, order_evidence, approvals = _run_order_care(runtime_root)
    handoff_oracle, handoff_evidence = _run_handoff(runtime_root)
    evidence = {
        "schema_version": "commerce-focus-evidence-v1",
        "source_commit": _source_commit(),
        "agent_id": "minimax-m2-7",
        "inventories": inventories,
        "order_care": {
            "oracle": order_oracle,
            "runtime": order_evidence,
            "approvals": approvals,
        },
        "unsupported_action": {
            "oracle": handoff_oracle,
            "runtime": handoff_evidence,
        },
    }
    (EVIDENCE_ROOT / "evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps({"status": "passed", "evidence": str(EVIDENCE_ROOT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
