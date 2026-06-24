from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from openminion.base.config.env import resolve_environment_config
from openminion.modules.cli_common import (
    DATA_ROOT_OPTION_HELP,
    HOME_ROOT_OPTION_HELP,
    apply_home_data_root_env,
    print_json_payload,
)

from .config import RuntimeConfig, load_config
from .constants import DEFAULT_CONFIG_FILENAME, DEFAULT_SESSION_DB_FILENAME
from .adapters.factory import (
    create_session_adapter,
    create_context_adapter,
    create_tool_adapter,
    create_a2a_adapter,
    create_memory_adapter,
    create_policy_adapter,
    create_llm_adapter,
    create_skill_adapter,
    create_rlm_adapter,
    create_artifact_adapter,
    create_safety_adapter,
    create_compress_adapter,
)
from .interfaces import SessionAPI
from .runner import BrainRunner, RunnerOptions
from .schemas import StepOutput, new_uuid
from openminion.base.constants import STATE_KEY_WORKING

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    yaml = None  # type: ignore[assignment]

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def main_callback(
    home_root: Path = typer.Option(
        None,
        "--home-root",
        help=HOME_ROOT_OPTION_HELP,
    ),
    data_root: Path = typer.Option(
        None,
        "--data-root",
        help=DATA_ROOT_OPTION_HELP,
    ),
) -> None:
    apply_home_data_root_env(home_root=home_root, data_root=data_root)


def _print_obj(obj: dict[str, Any], *, json_out: bool) -> None:
    if json_out or yaml is None:
        print_json_payload(obj, sort_keys=False, ensure_ascii=True)
        return
    print(yaml.safe_dump(obj, sort_keys=False))


def _build_runner(
    *, config: RuntimeConfig, root: Path
) -> tuple[BrainRunner, SessionAPI]:
    sm_cfg = config.brain
    adapters_cfg = sm_cfg.adapters
    adapter_mode = adapters_cfg.mode

    profile = sm_cfg.to_agent_profile()
    session_store = create_session_adapter(
        mode=adapter_mode,
        db_path=adapters_cfg.sessctl.db_path or (root / DEFAULT_SESSION_DB_FILENAME),
    )
    context = create_context_adapter(
        mode=adapter_mode,
        session_store=session_store,
        identity_system_prompt=None,
    )
    llm = create_llm_adapter(
        mode=adapter_mode,
        config=adapters_cfg.llmctl.model_dump() if adapters_cfg.llmctl else None,
    )
    tool = create_tool_adapter(
        mode=adapter_mode,
        workspace=root.parent,
        policy=None,
        agent_id=profile.agent_id,
        agent_profile=profile,
    )
    a2a = create_a2a_adapter(
        mode=adapter_mode,
        home_root=root,
        agent_id=profile.agent_id,
    )
    memory = create_memory_adapter(mode=adapter_mode, db_path=root)
    policy = create_policy_adapter(mode=adapter_mode, db_path=root)

    skill = create_skill_adapter(mode=adapter_mode)
    rlm = create_rlm_adapter(mode=adapter_mode)
    artifact = create_artifact_adapter(mode=adapter_mode)
    safety = create_safety_adapter(mode=adapter_mode)
    compress = create_compress_adapter(mode=adapter_mode)

    options = RunnerOptions(
        max_retries_per_step=sm_cfg.retries.max_retries_per_step,
        max_replans=sm_cfg.retries.max_replans,
        plan_checkpoint_interval=sm_cfg.retries.plan_checkpoint_interval,
        plan_max_iterations=sm_cfg.retries.plan_max_iterations,
        plan_consecutive_failure_limit=sm_cfg.retries.plan_consecutive_failure_limit,
        failure_strategy=sm_cfg.retries.step_failure_strategy,
        adaptive_plan_revision_enabled=sm_cfg.retries.adaptive_plan_revision_enabled,
        adaptive_replan_retained_step_outputs=sm_cfg.retries.adaptive_replan_retained_step_outputs,
        continuous_feasibility_rechecks_enabled=sm_cfg.retries.continuous_feasibility_rechecks_enabled,
        continuous_feasibility_recheck_interval=sm_cfg.retries.continuous_feasibility_recheck_interval,
        continuous_feasibility_trigger=sm_cfg.retries.continuous_feasibility_trigger,
        continuous_feasibility_hard_action=sm_cfg.retries.continuous_feasibility_hard_action,
        plan_auto_scale_max_llm_calls=sm_cfg.plan_auto_scale.max_llm_calls,
        plan_auto_scale_max_ticks=sm_cfg.plan_auto_scale.max_ticks,
        plan_auto_scale_max_tokens=sm_cfg.plan_auto_scale.max_tokens,
        plan_auto_scale_max_elapsed_ms=sm_cfg.plan_auto_scale.max_elapsed_ms,
        plan_auto_scale_base_overhead_ms=sm_cfg.plan_auto_scale.base_overhead_ms,
        plan_auto_scale_per_step_time_ms=sm_cfg.plan_auto_scale.per_step_time_ms,
        reflection_enabled=sm_cfg.reflection.enabled,
        governance_progress_checkpoints_enabled=sm_cfg.reflection.governance_progress_checkpoints_enabled,
        governance_step_risk_gate_enabled=sm_cfg.reflection.governance_step_risk_gate_enabled,
        reflection_reserved_llm_calls=sm_cfg.reflection.reserved_llm_calls,
        idempotency_enabled=sm_cfg.idempotency.enabled,
        idempotency_cache_size=sm_cfg.idempotency.cache_size,
        metactl_enabled=sm_cfg.metactl.enabled,
        metactl_config=sm_cfg.metactl.to_meta_config(),
        clarify_config=sm_cfg.clarify,
        mission_config=sm_cfg.mission,
        skill_selection_strategy=sm_cfg.skill_selection_strategy,
        max_skills_per_session=sm_cfg.max_skills_per_session,
        outcome_attribution_config=sm_cfg.outcome_attribution,
        success_memory_config=sm_cfg.success_memory,
    )
    runner = BrainRunner(
        profile=profile,
        session_api=session_store,
        context_api=context,
        llm_api=llm,
        tool_api=tool,
        a2a_api=a2a,
        memory_api=memory,
        policy_api=policy,
        meta_api=None,
        skill_api=skill,
        rlm_api=rlm,
        artifact_api=artifact,
        safety_api=safety,
        compress_api=compress,
        options=options,
    )
    return runner, session_store


def _default_session_id() -> str:
    return f"session-{new_uuid()[:8]}"


def _resolve_root_path(root: Path) -> Path:
    default_root = Path("brain")
    if root != default_root:
        return root
    env = resolve_environment_config()
    data_root = env.openminion_data_root.strip()
    if data_root:
        return (
            Path(data_root).expanduser().resolve(strict=False) / default_root
        ).resolve(strict=False)
    home_root = env.openminion_home.strip()
    if home_root:
        return (
            Path(home_root).expanduser().resolve(strict=False) / default_root
        ).resolve(strict=False)
    return root


def load_replay_payload(root: Path, session_id: str) -> dict[str, Any]:
    store = create_session_adapter(
        mode="auto",
        db_path=root / DEFAULT_SESSION_DB_FILENAME,
    )
    return {
        "session_id": session_id,
        "turns": store.list_turns(session_id),
        "events": store.list_events(session_id),
        STATE_KEY_WORKING: store.get_latest_working_state(session_id),
    }


def _step_output_exit_code(output: StepOutput) -> int:
    if output.status == "error":
        return 2
    if output.status == "stopped":
        return 1
    return 0


@app.command()
def run(
    input_text: str = typer.Option(..., "--input", help="User input for the run."),
    session_id: str = typer.Option(
        "", "--session-id", help="Session id; generated if omitted."
    ),
    config: Path = typer.Option(Path(DEFAULT_CONFIG_FILENAME), "--config"),
    root: Path = typer.Option(
        Path("brain"), "--root", help="Local storage root for session/events."
    ),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    cfg = load_config(config)
    resolved_root = _resolve_root_path(root)
    runner, _session_store = _build_runner(config=cfg, root=resolved_root)
    sid = session_id.strip() or _default_session_id()
    result = runner.run(session_id=sid, user_input=input_text)
    _print_obj(result.model_dump(mode="json"), json_out=json_out)
    raise typer.Exit(code=_step_output_exit_code(result))


@app.command()
def step(
    input_text: str = typer.Option("", "--input", help="Optional user input."),
    session_id: str = typer.Option(..., "--session-id", help="Session id."),
    config: Path = typer.Option(Path(DEFAULT_CONFIG_FILENAME), "--config"),
    root: Path = typer.Option(
        Path("brain"), "--root", help="Local storage root for session/events."
    ),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    cfg = load_config(config)
    resolved_root = _resolve_root_path(root)
    runner, _session_store = _build_runner(config=cfg, root=resolved_root)
    prompt = input_text.strip() or None
    result = runner.step(session_id=session_id, user_input=prompt)
    _print_obj(result.model_dump(mode="json"), json_out=json_out)
    raise typer.Exit(code=_step_output_exit_code(result))


@app.command()
def replay(
    session_id: str = typer.Option(..., "--session-id", help="Session id."),
    root: Path = typer.Option(
        Path("brain"), "--root", help="Local storage root for session/events."
    ),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    payload = load_replay_payload(_resolve_root_path(root), session_id)
    _print_obj(payload, json_out=json_out)


def _coerce_exit_code(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    return 1


def main(argv: list[str] | None = None) -> int:
    try:
        app(args=argv, standalone_mode=False)
        return 0
    except typer.Exit as exc:
        return _coerce_exit_code(exc.exit_code)
    except SystemExit as exc:  # pragma: no cover - defensive compatibility
        return _coerce_exit_code(exc.code)


if __name__ == "__main__":
    raise SystemExit(main())
