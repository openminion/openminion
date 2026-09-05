from __future__ import annotations

import logging
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from openminion.base.config import OpenMinionConfig
from openminion.modules.brain.paths import (
    resolve_brain_runtime_db_path,
    resolve_brain_sessions_db_path,
)
from openminion.modules.memory.runtime.assembly import RuntimeMemoryAssembly
from openminion.services.runtime.bootstrap import (
    build_agent_runtime_service,
    build_brain_runner_bundle,
)
from tests._csc_fixtures import _csc_install_default_agent


def test_brain_path_helpers_keep_session_and_runtime_dbs_separate(
    tmp_path: Path,
) -> None:
    storage_path = tmp_path / "state" / "runtime.db"
    resolved_session_db_path = tmp_path / "state" / "brain" / "sessions.db"

    assert resolve_brain_sessions_db_path(storage_path=storage_path) == (
        tmp_path / "state" / "brain" / "sessions.db"
    )
    assert resolve_brain_runtime_db_path(storage_path=storage_path) == (
        tmp_path / "state" / "brain" / "brain.db"
    )
    assert resolve_brain_runtime_db_path(storage_path=resolved_session_db_path) == (
        tmp_path / "state" / "brain" / "brain.db"
    )


def test_disabled_memory_does_not_install_terminal_capture_writer(
    tmp_path: Path,
) -> None:
    config = OpenMinionConfig()
    sessions = SimpleNamespace()
    disabled = RuntimeMemoryAssembly(gateway=object())
    active = RuntimeMemoryAssembly(gateway=object(), memctl=object())

    with mock.patch(
        "openminion.services.brain.service.BrainBridgeService",
        return_value=object(),
    ) as bridge:
        common = {
            "config": config,
            "plugins": object(),
            "provider": object(),
            "logger": logging.getLogger("test.bootstrap.capture_writer"),
            "tools": object(),
            "security_policy": None,
            "self_improvement": object(),
            "storage_path": tmp_path / "runtime.db",
            "home_root": tmp_path,
            "data_root": tmp_path,
            "config_path": tmp_path / "config.yaml",
            "sessions": sessions,
        }
        build_agent_runtime_service(
            **common,
            runtime_memory_assembly=disabled,
        )
        assert bridge.call_args.kwargs["terminal_capture_writer"] is None

        build_agent_runtime_service(
            **common,
            runtime_memory_assembly=active,
        )
        assert bridge.call_args.kwargs["terminal_capture_writer"] is not None


def test_build_brain_runner_bundle_uses_brain_runtime_db_for_goal_runtime(
    tmp_path: Path,
) -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")
    session_db_path = tmp_path / "state" / "brain" / "sessions.db"
    expected_runtime_db_path = tmp_path / "state" / "brain" / "brain.db"

    service = SimpleNamespace(
        _config=config,
        mode="auto",
        db_path=str(session_db_path),
        _telemetryctl=None,
        _runtime_handle=None,
        _logger=logging.getLogger("test.bootstrap.brain_runtime_db"),
        _retrieve_service=None,
        _action_policy_service=None,
        _self_improvement=None,
        _tools=None,
        _provider=None,
        _env=None,
        _vector_sync=None,
        _terminal_capture_writer=None,
        _runtime_memory_assembly=SimpleNamespace(
            memctl=SimpleNamespace(),
            vector_adapter=SimpleNamespace(),
        ),
        _context=SimpleNamespace(
            home_paths=SimpleNamespace(home_root=tmp_path, data_root=tmp_path),
            workspace_root=str(tmp_path),
        ),
        _get_manager_config=lambda _name: None,
        _validate_adapter_contracts=lambda **_kwargs: None,
        _resolve_override_value=lambda _key: "",
        _resolve_brain_config=lambda: None,
        _validate_runner_contract=lambda _runner: None,
        _resolve_llm_wrapper=lambda _llm_api: None,
    )

    shared_task_manager = object()
    fake_runner = SimpleNamespace(task_manager=shared_task_manager)
    captured: dict[str, object] = {}

    def _capture_goal_store(path: str, *args, **kwargs):
        del args, kwargs
        captured["goal_db_path"] = Path(path)
        return SimpleNamespace()

    def _capture_mission_store(path: str, *args, **kwargs):
        del args, kwargs
        captured["mission_db_path"] = Path(path)
        return SimpleNamespace()

    def _capture_goal_runtime(
        *, goal_store, mission_store, checkpoint_manager, owns_stores
    ):
        captured["goal_store"] = goal_store
        captured["mission_store"] = mission_store
        captured["checkpoint_manager"] = checkpoint_manager
        captured["owns_stores"] = owns_stores
        return SimpleNamespace(goal_store=goal_store, mission_store=mission_store)

    with ExitStack() as stack:
        stack.enter_context(
            mock.patch(
                "openminion.services.brain.service.create_llm_adapter",
                return_value=SimpleNamespace(),
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.services.brain.service.create_session_api",
                return_value=SimpleNamespace(),
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.services.brain.service.create_a2a_api",
                return_value=SimpleNamespace(),
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.services.brain.service.create_context_api",
                return_value=SimpleNamespace(),
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.services.brain.service.create_memory_api",
                return_value=SimpleNamespace(),
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.services.brain.service.create_policy_api",
                return_value=SimpleNamespace(),
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.services.brain.service.create_safety_api",
                return_value=SimpleNamespace(),
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.services.brain.service.init_retrieve_adapter",
                return_value=SimpleNamespace(),
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.services.brain.service.create_skill_api",
                return_value=SimpleNamespace(),
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.services.brain.service.init_rlm_adapter",
                return_value=SimpleNamespace(),
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.services.brain.service.create_compress_api",
                return_value=SimpleNamespace(),
            )
        )
        tool_api_factory = stack.enter_context(
            mock.patch(
                "openminion.services.brain.service.create_tool_api",
                return_value=SimpleNamespace(),
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.services.brain.factory.vector.init_vector_adapter",
                return_value=(SimpleNamespace(), None),
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.services.brain.metadata.resolve_llm_profiles",
                return_value=SimpleNamespace(),
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.services.brain.metadata.resolve_agent_budgets",
                return_value=SimpleNamespace(),
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.services.brain.metadata.resolve_runner_options",
                return_value=SimpleNamespace(),
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.modules.brain.schemas.AgentProfile",
                side_effect=lambda **kwargs: SimpleNamespace(**kwargs),
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.modules.session.storage.repository.create_sqlite_cron_repository",
                return_value=SimpleNamespace(),
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.modules.task.TaskManager.from_cron_repository",
                return_value=shared_task_manager,
            )
        )
        runner_factory = stack.enter_context(
            mock.patch(
                "openminion.services.brain.service.BrainRunner",
                return_value=fake_runner,
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.modules.brain.storage.goals.SQLiteGoalStore",
                side_effect=_capture_goal_store,
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.modules.brain.storage.missions.SQLiteMissionStateStore",
                side_effect=_capture_mission_store,
            )
        )
        stack.enter_context(
            mock.patch(
                "openminion.modules.brain.runtime.goal.long_running.LongRunningGoalRuntime",
                side_effect=_capture_goal_runtime,
            )
        )
        runner = build_brain_runner_bundle(service)

    assert runner is fake_runner
    assert captured["goal_db_path"] == expected_runtime_db_path
    assert captured["mission_db_path"] == expected_runtime_db_path
    assert captured["owns_stores"] is True
    assert captured["goal_db_path"] != session_db_path
    assert tool_api_factory.call_args.kwargs["task_manager"] is shared_task_manager
    assert runner_factory.call_args.kwargs["task_manager"] is shared_task_manager
