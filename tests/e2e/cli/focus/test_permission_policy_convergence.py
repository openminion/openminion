from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace

import pytest

from openminion.base.config.env import EnvironmentConfig
from openminion.cli.interactive.terminal.shell.approval import (
    build_terminal_approval_callback,
)
from openminion.modules.brain.adapters.tool.permission_mode import (
    WORKSPACE_AUTO_TOOL_NAMES,
    effective_permission_mode_for_tool,
    is_tool_blocked_by_readonly,
)
from openminion.modules.policy.models import PolicyConfig, PolicyRule
from openminion.modules.policy.runtime.service import PolicyCtl
from openminion.tools.blockchain.preparations import save_prepared_transaction
from openminion.tools.blockchain.runtime import preparation_digest
from openminion.tools.commerce.authorization import (
    consume_commerce_authorization,
)
from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario
from tests.e2e.cli.focus.harness.assertions import visible_text
from tests.e2e.cli.focus.harness.ollama_fixture import ollama_fixture_server

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(300)]


class _AlwaysOverlay:
    async def present_approval_async(self, _prompt: str, **_kwargs: object) -> str:
        return "always"


class _FailOverlay:
    async def present_approval_async(self, _prompt: str, **_kwargs: object) -> str:
        raise AssertionError("persisted exact-session grant should avoid a prompt")


def _fixture_config(path: Path, base_url: str, storage_path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "default_agent": "fixture",
                "agents": {
                    "fixture": {
                        "name": "fixture",
                        "provider": "ollama",
                        "model": "qwen2.5:14b",
                    }
                },
                "providers": {"ollama": {"model": "qwen2.5:14b", "base_url": base_url}},
                "action_policy": {
                    "rules": [{"match": {"tool_name": "file.trash"}, "mode": "block"}]
                },
                "runtime": {
                    "demo_mode": False,
                    "log_level": "ERROR",
                    "tools": {
                        "blockchain": {
                            "enabled": True,
                            "rpc_url": "http://127.0.0.1:1",
                            "chain_id": 31337,
                            "signer_secret_key": "unused-e2e-signer",
                            "writes_enabled": True,
                        }
                    },
                },
                "storage": {"path": str(storage_path)},
            }
        ),
        encoding="utf-8",
    )


def _seed_blockchain_preparation(data_root: Path, session_id: str) -> str:
    transaction = {
        "schema_version": "evm-transaction-v1",
        "transaction_type": "eip1559",
        "chain_id": 31337,
        "from_address": "0x" + "11" * 20,
        "to_address": "0x" + "22" * 20,
        "value_wei": "1",
        "nonce": "0",
        "gas_limit": "21000",
        "data": "0x",
        "max_fee_per_gas_wei": "2",
        "max_priority_fee_per_gas_wei": "1",
        "max_total_fee_wei": "42000",
    }
    digest = preparation_digest(transaction, None)
    save_prepared_transaction(
        {
            "transaction": transaction,
            "call_context": None,
            "preparation_digest": digest,
        },
        SimpleNamespace(
            session_id=session_id,
            env=EnvironmentConfig(
                values={
                    "OPENMINION_HOME": str(data_root.parent / "home"),
                    "OPENMINION_DATA_ROOT": str(data_root),
                }
            ),
        ),
    )
    return digest


def _tool_turn(
    call_id: str,
    name: str,
    arguments: dict[str, object],
    marker: str,
) -> tuple[dict[str, object], dict[str, object]]:
    return (
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "function": {"name": name, "arguments": arguments},
                }
            ],
        },
        {
            "role": "assistant",
            "content": (
                f"{marker}\n\n"
                '<finalization_status>{"status":"final_answer",'
                '"reasoning":"deterministic permission fixture completed"}'
                "</finalization_status>"
            ),
        },
    )


def _choose_permission(
    probe: FocusProbe,
    session,
    *,
    number: int,
    marker: str,
) -> str:
    offset = len(session.transcript)
    probe._submit_composer_line(session, "/permissions", wait_for_render=False)
    session.wait_for_after("Choose permissions:", offset=offset, timeout=30)
    session.type_line(str(number))
    if number == 4:
        session.wait_for_after(r"\[y/N\]:", offset=offset, timeout=30)
        session.type_line("y")
    session.wait_for_after(re.escape(marker), offset=offset, timeout=30)
    probe._wait_for_composer(session)
    return visible_text(session.transcript[offset:])


def test_provider_free_focus_permission_journey(
    focus_probe: FocusProbe,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    data_root = tmp_path / "data"
    floor_session_id = "oppc-permission-boundaries"
    exact_session_id = "oppc-full-exact"
    preparation_digest_value = _seed_blockchain_preparation(
        data_root,
        f"{exact_session_id}::conv:focus-{exact_session_id}",
    )
    source_status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=focus_probe.openminion_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    responses = (
        *_tool_turn(
            "auto-write",
            "file.write",
            {"path": "auto.txt", "content": "auto"},
            "OPPC_AUTO_OK",
        ),
        *_tool_turn(
            "auto-edit",
            "file.edit",
            {
                "path": "auto.txt",
                "operations": [
                    {"op": "replace", "old_text": "auto", "new_text": "edited"}
                ],
            },
            "OPPC_AUTO_EDIT_OK",
        ),
        *_tool_turn(
            "restored-edit",
            "file.edit",
            {
                "path": "auto.txt",
                "operations": [
                    {
                        "op": "replace",
                        "old_text": "edited",
                        "new_text": "restored",
                    }
                ],
            },
            "OPPC_RESTORED_AUTO_OK",
        ),
        *_tool_turn(
            "override-write",
            "file.write",
            {"path": "override.txt", "content": "once\n", "append": True},
            "OPPC_TOOL_AUTO_OK",
        ),
        *_tool_turn(
            "session-write",
            "file.write",
            {"path": "session.txt", "content": "session"},
            "OPPC_SESSION_CREATED",
        ),
        *_tool_turn(
            "session-reuse",
            "file.write",
            {"path": "session.txt", "content": "session"},
            "OPPC_SESSION_REUSED",
        ),
        *_tool_turn(
            "allow-once",
            "file.write",
            {"path": "once.txt", "content": "once"},
            "OPPC_ALLOW_ONCE_OK",
        ),
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "readonly-write",
                    "function": {
                        "name": "file.write",
                        "arguments": {
                            "path": "readonly-denied.txt",
                            "content": "must not exist",
                        },
                    },
                },
                {
                    "id": "readonly-exec",
                    "function": {
                        "name": "exec.run",
                        "arguments": {"command": "pwd"},
                    },
                },
            ],
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "request-full-exact-blockchain",
                    "function": {
                        "name": "tool.request",
                        "arguments": {
                            "name": "blockchain.send_transaction",
                            "terminal_after_success": True,
                            "freshness": {
                                "domain": "general",
                                "time_sensitive": False,
                                "needs_live_data": False,
                                "needs_sources": False,
                                "needs_exact_date": False,
                                "answer_mode": "local_only",
                            },
                        },
                    },
                }
            ],
        },
        _tool_turn(
            "full-exact-blockchain",
            "blockchain.send_transaction",
            {"preparation_digest": preparation_digest_value},
            "OPPC_FULL_EXACT_DENIED",
        )[0],
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "post-action-judgment",
                    "function": {
                        "name": "submit_output",
                        "arguments": {
                            "outcome": "halt",
                            "reason": "operator denied exact blockchain send",
                            "user_message": "OPPC_FULL_EXACT_DENIED",
                            "confidence": 1.0,
                        },
                    },
                }
            ],
        },
        *(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "outside-write",
                        "function": {
                            "name": "file.write",
                            "arguments": {
                                "path": "../outside.txt",
                                "content": "must not escape",
                            },
                        },
                    },
                ],
            },
            {
                "role": "assistant",
                "content": (
                    "OPPC_FULL_FLOORS_OK: the workspace path floor held.\n\n"
                    '<finalization_status>{"status":"final_answer",'
                    '"reasoning":"full-access structural floors observed"}'
                    "</finalization_status>"
                ),
            },
        ),
        _tool_turn(
            "blocked-trash",
            "file.trash",
            {"path": "blocked.txt"},
            "OPPC_FULL_BLOCK_OK",
        )[0],
    )

    with ollama_fixture_server(responses) as (base_url, requests):
        config_path = tmp_path / "config.json"
        _fixture_config(config_path, base_url, tmp_path / "openminion.db")
        monkeypatch.setenv("OLLAMA_API_KEY", "fixture-key-not-for-network-use")
        probe = FocusProbe(
            python_bin=focus_probe.python_bin,
            openminion_root=focus_probe.openminion_root,
            framework_root=focus_probe.framework_root,
            data_root=data_root,
            config_path=config_path,
            agent_id="fixture",
            workdir=project,
            session_id="oppc-focus",
            include_project_context=False,
            allow_unsandboxed_exec=False,
        )
        assert "--allow-unsandboxed-exec" not in probe.command()

        with probe.session(rows=50, cols=160) as session:
            probe.wait_ready(session)
            _choose_permission(
                probe, session, number=1, marker="permissions → read-only"
            )
            _choose_permission(probe, session, number=2, marker="permissions → ask")
            _choose_permission(probe, session, number=3, marker="permissions → auto")
            _choose_permission(
                probe, session, number=4, marker="permissions → full access"
            )
            probe.run_slash(session, "/permissions auto", marker="permissions → auto")
            auto_events: list[dict[str, object]] = []
            auto_turn = probe.run_turn(
                session,
                FocusScenario(
                    scenario_id="oppc-auto",
                    prompt="Run the deterministic workspace-auto edit.",
                    expected_markers=("OPPC_AUTO_OK",),
                ),
                approval_events=auto_events,
            )
            assert not auto_events
            assert "fixture-key-not-for-network-use" not in auto_turn
            assert (project / "auto.txt").read_text(encoding="utf-8") == "auto"

            edit_events: list[dict[str, object]] = []
            probe.run_turn(
                session,
                FocusScenario(
                    scenario_id="oppc-auto-edit",
                    prompt="Run the deterministic workspace-auto structured edit.",
                    expected_markers=("OPPC_AUTO_EDIT_OK",),
                ),
                approval_events=edit_events,
            )
            assert not edit_events
            assert (project / "auto.txt").read_text(encoding="utf-8") == "edited"
            probe.run_slash(
                session,
                "/permissions file.trash readonly",
                marker="file.trash: readonly",
            )

        with probe.session(rows=50, cols=160) as restored:
            probe.wait_ready(restored)
            restored_events: list[dict[str, object]] = []
            probe.run_turn(
                restored,
                FocusScenario(
                    scenario_id="oppc-restored-auto",
                    prompt="Run the deterministic edit after session reconstruction.",
                    expected_markers=("OPPC_RESTORED_AUTO_OK",),
                ),
                approval_events=restored_events,
            )
            assert not restored_events
            assert (project / "auto.txt").read_text(encoding="utf-8") == "restored"
            probe.run_slash(
                restored,
                "/permissions file.trash default",
                marker="cleared override for file.trash",
            )
            probe.run_slash(
                restored, "/permissions default", marker="permissions → ask"
            )
            probe.run_slash(
                restored,
                "/permissions file.write auto",
                marker="file.write: auto",
            )
            override_events: list[dict[str, object]] = []
            probe.run_turn(
                restored,
                FocusScenario(
                    scenario_id="oppc-tool-auto",
                    prompt="Run the explicitly auto-authorized write.",
                    expected_markers=("OPPC_TOOL_AUTO_OK",),
                ),
                approval_events=override_events,
            )
            assert not override_events
            assert (project / "override.txt").read_text(encoding="utf-8") == "once\n"
            probe.run_slash(
                restored,
                "/permissions file.write default",
                marker="cleared override for file.write",
            )

            session_events: list[dict[str, object]] = []
            probe.run_turn(
                restored,
                FocusScenario(
                    scenario_id="oppc-session-create",
                    prompt="Run the deterministic exact session edit.",
                    requires_approval=True,
                    approval_reply="session",
                ),
                approval_events=session_events,
            )
            assert len(session_events) == 1

        with probe.session(rows=50, cols=160) as resumed:
            probe.wait_ready(resumed)
            reuse_events: list[dict[str, object]] = []
            probe.run_turn(
                resumed,
                FocusScenario(
                    scenario_id="oppc-session-reuse",
                    prompt="Repeat the deterministic exact session edit.",
                    expected_markers=("OPPC_SESSION_REUSED",),
                ),
                approval_events=reuse_events,
            )
            assert not reuse_events

            grants = visible_text(
                probe.run_slash(resumed, "/permissions grants", marker="file.write")
            )
            match = re.search(
                r"([0-9a-f-]+)\s+file\.write\s+scope=.*duration=session",
                grants,
            )
            assert match is not None, grants
            probe.run_slash(
                resumed,
                f"/permissions revoke {match.group(1)}",
                marker=f"revoked {match.group(1)}",
            )

        fresh_probe = probe.for_session("oppc-permission-floors")
        with fresh_probe.session(rows=50, cols=160) as fresh:
            fresh_probe.wait_ready(fresh)
            once_events: list[dict[str, object]] = []
            once_turn = fresh_probe.run_turn(
                fresh,
                FocusScenario(
                    scenario_id="oppc-allow-once",
                    prompt="Run the deterministic edit once after revocation.",
                    requires_approval=True,
                    approval_reply="yes",
                ),
                approval_events=once_events,
            )
            assert len(once_events) == 1, once_turn
            empty_grants = visible_text(
                fresh_probe.run_slash(
                    fresh,
                    "/permissions grants",
                    marker="no active grants",
                )
            )
            assert "no active grants" in empty_grants

        floor_probe = probe.for_session(floor_session_id)
        with floor_probe.session(rows=50, cols=160) as floors:
            floor_probe.wait_ready(floors)
            floor_probe.run_slash(
                floors, "/permissions readonly", marker="permissions → read-only"
            )
            floor_probe.run_turn(
                floors,
                FocusScenario(
                    scenario_id="oppc-readonly",
                    prompt="Run the deterministic read-only floor checks.",
                    expected_markers=("Denied by configured exact-tool rule",),
                ),
            )
            assert not (project / "readonly-denied.txt").exists()
            project_offset = len(floors.transcript)
            floor_probe._submit_composer_line(
                floors,
                (
                    "/project start --goal 'prove project approval floor' "
                    "--verification-domain research --verify-command true"
                ),
                wait_for_render=False,
            )
            floors.wait_for_after(r"\[y/N\]:", offset=project_offset, timeout=30)
            floors.type_line("n")
            floors.wait_for_after(
                r"Project launch denied\.", offset=project_offset, timeout=30
            )
            floor_probe._wait_for_composer(floors)
            project_denied = visible_text(floors.transcript[project_offset:])
            assert "Project launch denied." in project_denied
            assert "Choices: allow_once, deny" in project_denied
            telemetry = visible_text(
                floor_probe.run_slash(
                    floors,
                    "/telemetry events --limit 50",
                    marker="telemetry events:",
                )
            )
            assert "fixture-key-not-for-network-use" not in telemetry

        exact_probe = probe.for_session(exact_session_id)
        with exact_probe.session(rows=50, cols=160) as exact_session:
            exact_probe.wait_ready(exact_session)
            _choose_permission(
                exact_probe,
                exact_session,
                number=4,
                marker="permissions → full access",
            )
            exact_events: list[dict[str, object]] = []
            exact_turn = exact_probe.run_turn(
                exact_session,
                FocusScenario(
                    scenario_id="oppc-full-exact",
                    prompt=(
                        "tool blockchain.send_transaction for the deterministic "
                        "exact-approval floor check"
                    ),
                    expected_markers=("OPPC_FULL_EXACT_DENIED",),
                    requires_approval=True,
                    approval_reply="no",
                ),
                approval_events=exact_events,
            )
            assert len(exact_events) == 1, exact_turn
            assert exact_events[0]["kind"] == "policy"
            assert exact_events[0]["decision"] == "no"

        full_floor_probe = probe.for_session("oppc-full-path-floor")
        with full_floor_probe.session(rows=50, cols=160) as full_floor_session:
            full_floor_probe.wait_ready(full_floor_session)
            _choose_permission(
                full_floor_probe,
                full_floor_session,
                number=4,
                marker="permissions → full access",
            )
            full_floor_probe.run_turn(
                full_floor_session,
                FocusScenario(
                    scenario_id="oppc-full-floors",
                    prompt="Run the deterministic full-access floor checks.",
                    expected_markers=("OPPC_FULL_FLOORS_OK",),
                ),
            )
            assert not (tmp_path / "outside.txt").exists()

        full_block_probe = probe.for_session("oppc-full-config-block")
        with full_block_probe.session(rows=50, cols=160) as full_block_session:
            full_block_probe.wait_ready(full_block_session)
            _choose_permission(
                full_block_probe,
                full_block_session,
                number=4,
                marker="permissions → full access",
            )
            (project / "blocked.txt").write_text("keep", encoding="utf-8")
            full_block_probe.run_turn(
                full_block_session,
                FocusScenario(
                    scenario_id="oppc-full-block",
                    prompt="Run the deterministic configured-block floor check.",
                    expected_markers=("Denied by configured exact-tool rule",),
                ),
            )
            assert (project / "blocked.txt").read_text(encoding="utf-8") == "keep"

    policy_databases = list(tmp_path.rglob("policy.db"))
    assert len(policy_databases) == 1
    policy = PolicyCtl.with_sqlite(policy_databases[0])
    try:
        decisions = policy.list_decisions(limit=200)
    finally:
        policy.close()
    assert decisions
    assert any(row["reason_code"] == "CONFIG_RULE_BLOCK" for row in decisions)
    assert any(
        row["reason_code"] == "EXACT_AUTHORIZATION_REQUIRED"
        and row["tool"] == "blockchain"
        and row["method"] == "send_transaction"
        for row in decisions
    )
    assert all(
        "fixture-key-not-for-network-use" not in json.dumps(row) for row in decisions
    )
    assert all(isinstance(row["risk_spec_json"], dict) for row in decisions)

    assert requests
    assert (
        subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=focus_probe.openminion_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == source_status
    )


def test_permission_policy_contract_without_terminal(tmp_path: Path) -> None:
    assert WORKSPACE_AUTO_TOOL_NAMES == frozenset({"file.write", "file.edit"})
    assert effective_permission_mode_for_tool(
        global_mode="auto", permission_overrides={}, tool_name="file.write"
    ) == ("auto", "global")
    assert effective_permission_mode_for_tool(
        global_mode="ask",
        permission_overrides={"file.copy": "auto"},
        tool_name="file.copy",
    ) == ("auto", "tool_override")
    assert is_tool_blocked_by_readonly("exec.run") is True
    assert is_tool_blocked_by_readonly("file.read") is False

    ctl = PolicyCtl.with_sqlite(
        tmp_path / "policy.db",
        config=PolicyConfig(mode="enforce", default_action="require_confirm"),
    )
    try:
        first = build_terminal_approval_callback(
            overlay=_AlwaysOverlay(),
            policy_ctl=ctl,
            session_id="focus-session",
        )
        assert asyncio.run(first("file.write", {"path": "note.md"}, "call-1"))

        reconstructed = build_terminal_approval_callback(
            overlay=_FailOverlay(),
            policy_ctl=ctl,
            session_id="focus-session",
        )
        assert asyncio.run(reconstructed("file.write", {"path": "note.md"}, "call-2"))
        grants = ctl.list_grants(active_only=True)
        assert len(grants) == 1
        assert grants[0].reason == "created_from_confirmation:allow_session_exact"
        assert grants[0].invocation_hash

        changed_args = build_terminal_approval_callback(
            overlay=_AlwaysOverlay(),
            policy_ctl=ctl,
            session_id="focus-session",
        )
        assert asyncio.run(changed_args("file.write", {"path": "other.md"}, "call-3"))
        assert len(ctl.list_grants(active_only=True)) == 2
    finally:
        ctl.close()

    full_access = PolicyCtl.with_sqlite(
        tmp_path / "full-access-policy.db",
        config=PolicyConfig(
            mode="disabled",
            rules=(PolicyRule(tool_name="commerce.prepare_order", mode="auto"),),
        ),
    )
    try:
        decision = full_access.check(
            {"tool": "commerce", "method": "prepare_order", "args": {}},
            {"session_id": "focus-session", "subject_id": "local"},
            confirmation_preview={},
        )
        assert decision.decision == "REQUIRE_CONFIRM"
        assert decision.reason_code == "EXACT_AUTHORIZATION_REQUIRED"
        assert decision.approval_id
        full_access.resolve_confirmation(decision.approval_id, "allow_once")
        authorization = consume_commerce_authorization(
            method="prepare_order",
            policy_ctl=full_access,
            permission_mode="bypass",
            args={},
            subject_id="local",
            session_id="focus-session",
        )
        assert authorization.duration_type == "once"
        assert (
            full_access.check(
                {"tool": "commerce", "method": "prepare_order", "args": {}},
                {"session_id": "focus-session", "subject_id": "local"},
                confirmation_preview={},
            ).decision
            == "REQUIRE_CONFIRM"
        )
    finally:
        full_access.close()
