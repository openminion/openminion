import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openminion.api.runtime import APIRuntime
from openminion.base.config import (
    OpenMinionConfig,
    build_runtime_config,
    resolve_agent_config,
)
from openminion.modules.brain.schemas import BudgetCounters, StepOutput, WorkingState
from openminion.modules.brain.runner import BrainRunner
from openminion.modules.brain.tools.schema import collect_runtime_tool_names
from openminion.modules.identity.runtime.service import IdentityCtl
from openminion.modules.identity.storage import (
    InMemoryIdentityStore,
    SQLiteIdentityStore,
)
from openminion.modules.session.capture import CaptureIdentity
from openminion.modules.skill.runtime.skill import Skill
from openminion.modules.tool import build_default_tool_registry
from openminion.services.agent import AgentService
from openminion.modules.brain.loop.services import turn_tool_allowlist
from openminion.services.runtime.ingress.requests import (
    runtime_turn_request_from_payload,
)
from openminion.services.runtime.plugins import PluginRegistry
from tests.skill.admission_helpers import ingest_file_and_admit

ROOT = Path(__file__).resolve().parents[3]
SKILL_PATH = ROOT / "examples" / "skills" / "security-researcher-readonly" / "SKILL.md"
IDENTITY_PATH = ROOT / "examples" / "identity" / "security-researcher-readonly.yaml"
PROFILE_PATH = ROOT / "examples" / "security-researcher-readonly" / "profile.json"
EXPECTED_TOOLS = {
    "file.list_dir",
    "file.read",
    "file.read_range",
    "file.find",
    "code.grep",
    "code.repo_map",
    "code.repo_index",
    "code.symbol_find",
    "git.status",
    "git.diff",
    "git.log",
    "git.show",
    "git.blame",
    "security.scan_code",
    "security.scan_dependencies",
    "security.scan_artifact",
    "security.scan_secrets",
    "security.publish_report",
}


def test_researcher_skill_is_agent_scoped_and_admitted(
    tmp_path: Path, monkeypatch
) -> None:
    home_root = tmp_path / "home"
    data_root = home_root / "data"
    monkeypatch.setenv("OPENMINION_HOME", str(home_root))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    known_tools = sorted(build_default_tool_registry().list())
    skill = Skill(
        {
            "skill": {
                "sqlite_path": str(data_root / "skill.db"),
                "blob_root": str(data_root / "blobs"),
                "fallback_root": str(data_root / "fallback"),
                "wal": False,
                "known_tools": known_tools,
            }
        }
    )
    try:
        skill_id, _version_hash, warnings = ingest_file_and_admit(
            skill,
            SKILL_PATH,
            name="security-researcher-readonly",
            scope="agent",
            agent_id="security-researcher-readonly",
        )
        package = skill.get_skill(skill_id)
    finally:
        skill.close()

    assert skill_id == "security-researcher-readonly"
    assert package.scope == "agent"
    assert package.agent_id == "security-researcher-readonly"
    assert set(package.tools) == EXPECTED_TOOLS
    assert not any(item.startswith("lint.error:") for item in warnings)


def test_researcher_profile_and_identity_require_explicit_selection() -> None:
    config = OpenMinionConfig.from_dict(json.loads(PROFILE_PATH.read_text()))

    assert resolve_agent_config(config).name == "default-agent"
    researcher = resolve_agent_config(config, "security-researcher-readonly")
    assert researcher.skill == "security-researcher-readonly"

    identity = IdentityCtl(store=InMemoryIdentityStore())
    loaded = identity.load_profiles_from_path(IDENTITY_PATH)
    profile = identity.get_profile("security-researcher-readonly")
    identity.close()

    assert loaded == ["security-researcher-readonly"]
    assert profile is not None
    assert profile.tool_posture.tool_use == "read_only"
    assert set(profile.tool_posture.allowed_tools) == EXPECTED_TOOLS
    assert not any(
        tool.startswith(("exec.", "browser.", "web.", "plan.", "task.delegate"))
        for tool in profile.tool_posture.allowed_tools
    )


def test_api_selected_profile_loads_identity_cap_and_cannot_be_widened(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENMINION_IDENTITY_DB", raising=False)
    config = OpenMinionConfig.from_dict(json.loads(PROFILE_PATH.read_text()))
    request = runtime_turn_request_from_payload(
        runtime=SimpleNamespace(config=config, tool_workspace_root=tmp_path),
        payload={
            "message": "Audit the approved source.",
            "agent_id": "security-researcher-readonly",
            "permission_mode": "readonly",
            "allowed_tools": ["web.fetch", "file.read"],
        },
    )
    identity_path = data_root / "identity" / "identity.db"
    identity_path.parent.mkdir(parents=True)
    identity = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(identity_path)))
    identity.load_profiles_from_path(IDENTITY_PATH)
    identity.close()

    service = AgentService(
        config=build_runtime_config(config, agent_id=request.profile_agent_id),
        plugins=PluginRegistry([]),
        provider=None,
        logger=logging.getLogger("openminion.tests.security.profile"),
        home_root=tmp_path / "home",
        tools=build_default_tool_registry(),
    )
    try:
        assert service._identity_tool_filter is not None  # noqa: SLF001
        assert set(service._identity_tool_filter["allowed_tools"]) == EXPECTED_TOOLS  # noqa: SLF001
        assert turn_tool_allowlist(
            dict(request.inbound_metadata or {}),
            service._identity_tool_filter,  # noqa: SLF001
        ) == ("file.read",)
    finally:
        service.close()


def test_normal_api_turn_applies_researcher_identity_and_exposure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_HOME", str(home_root))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    monkeypatch.delenv("OPENMINION_IDENTITY_DB", raising=False)
    config = OpenMinionConfig.from_dict(json.loads(PROFILE_PATH.read_text()))
    config.runtime.tool_workspace_root = str(tmp_path)
    runtime = APIRuntime.from_config(
        config=config,
        home_root=home_root,
        data_root=data_root,
    )
    session_id = "api-security-researcher"
    identity = IdentityCtl(
        store=SQLiteIdentityStore(
            sqlite_path=str(data_root / "identity" / "identity.db")
        )
    )
    identity.load_profiles_from_path(IDENTITY_PATH)
    identity.close()
    brain_session_id = f"{session_id}::conv:security-conversation"
    runtime.activate_tool_profile(
        "security_readonly",
        session_id=brain_session_id,
        approved=True,
    )
    observed: dict[str, Any] = {}

    def _run(runner: BrainRunner, **kwargs: Any) -> StepOutput:
        active_session = str(kwargs["session_id"])
        observed["session_id"] = active_session
        observed["permission_mode"] = runner._pending_permission_mode  # noqa: SLF001
        observed["tools"] = collect_runtime_tool_names(
            runner,
            metadata={"session_id": active_session},
        )
        observed["blocked"] = runner.tool_api.execute(
            command={"tool_name": "exec.run", "args": {"command": "pwd"}},
            session_id=active_session,
            trace_id="api-security-trace",
        )
        state = WorkingState(
            session_id=active_session,
            agent_id="security-researcher-readonly",
            permission_mode="readonly",
            status="done",
            budgets_remaining=BudgetCounters(
                ticks=1,
                tool_calls=1,
                a2a_calls=0,
                tokens=1,
                time_ms=1,
            ),
        )
        capture_identity = CaptureIdentity(
            runtime_session_id=str(kwargs["runtime_session_id"]),
            root_turn_id=str(kwargs["root_turn_id"]),
            event_id=str(kwargs["capture_event_id"]),
            capture_id=str(kwargs["capture_id"]),
        )
        receipt = runner.terminal_capture_writer.commit_terminal_capture_intent(
            identity=capture_identity,
            event_payload={"status": "done", "agent_id": state.agent_id},
            state="excluded",
        )
        return StepOutput(
            session_id=active_session,
            status="done",
            message="Provider-free API composition proof.",
            working_state=state,
            action_result=None,
            terminal_capture_intent_receipt=receipt,
        )

    monkeypatch.setattr(BrainRunner, "run", _run)
    try:
        result = runtime.run_turn(
            payload={
                "message": "Audit the approved source.",
                "agent_id": "security-researcher-readonly",
                "session_id": session_id,
                "conversation_id": "security-conversation",
                "channel": "console",
                "permission_mode": "readonly",
                "allowed_tools": [*sorted(EXPECTED_TOOLS), "exec.run"],
            },
            request_id="api-security-trace",
        )
    finally:
        runtime.close()

    assert result["agent_id"] == "security-researcher-readonly"
    assert observed["session_id"] == brain_session_id
    assert observed["permission_mode"] == "readonly"
    assert observed["tools"] == EXPECTED_TOOLS
    assert observed["blocked"]["error"]["code"] == "POLICY_DENIED"
