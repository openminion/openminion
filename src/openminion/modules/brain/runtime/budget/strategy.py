"""Strategy budget helpers for brain runtime."""

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ResearchBudgetSettings:
    checkpoint_interval: int
    max_resume_count: int
    max_research_iterations: int


@dataclass(frozen=True)
class CodingBudgetSettings:
    max_adaptive_iterations: int
    max_self_corrections: int


@dataclass(frozen=True)
class OrchestrateBudgetSettings:
    parallel_enabled: bool
    parallel_writes_enabled: bool
    max_parallel_workers: int
    max_subtasks: int
    max_decompose_depth: int


def _config_value(config: Any, field_name: str) -> Any | None:
    if config is None:
        return None
    value = getattr(config, field_name, None)
    if value is None and isinstance(config, Mapping):
        value = config.get(field_name)
    return value


def _bool_value(config: Any, field_name: str, default: bool) -> bool:
    value = _config_value(config, field_name)
    if value is None:
        return default
    return bool(value)


def _int_value(
    config: Any,
    field_name: str,
    *,
    default: int,
    minimum: int,
) -> int:
    value = _config_value(config, field_name)
    return max(minimum, int(value if value is not None else default))


def resolve_research_budget_settings(
    *,
    config: Any,
    default_checkpoint_interval: int,
    default_max_resume_count: int,
    default_max_research_iterations: int,
) -> ResearchBudgetSettings:
    return ResearchBudgetSettings(
        checkpoint_interval=_int_value(
            config,
            "checkpoint_interval",
            default=default_checkpoint_interval,
            minimum=1,
        ),
        max_resume_count=_int_value(
            config,
            "max_resume_count",
            default=default_max_resume_count,
            minimum=0,
        ),
        max_research_iterations=_int_value(
            config,
            "max_research_iterations",
            default=default_max_research_iterations,
            minimum=1,
        ),
    )


def resolve_coding_budget_settings(
    *,
    config: Any,
    default_max_adaptive_iterations: int,
    default_max_self_corrections: int,
) -> CodingBudgetSettings:
    return CodingBudgetSettings(
        max_adaptive_iterations=_int_value(
            config,
            "max_adaptive_iterations",
            default=default_max_adaptive_iterations,
            minimum=1,
        ),
        max_self_corrections=_int_value(
            config,
            "max_self_corrections",
            default=default_max_self_corrections,
            minimum=1,
        ),
    )


def resolve_orchestrate_budget_settings(
    *,
    config: Any,
    default_parallel_enabled: bool,
    default_parallel_writes_enabled: bool,
    default_max_parallel_workers: int,
    default_max_subtasks: int,
    default_max_decompose_depth: int,
) -> OrchestrateBudgetSettings:
    return OrchestrateBudgetSettings(
        parallel_enabled=_bool_value(
            config,
            "parallel_enabled",
            default_parallel_enabled,
        ),
        parallel_writes_enabled=_bool_value(
            config,
            "parallel_writes_enabled",
            default_parallel_writes_enabled,
        ),
        max_parallel_workers=_int_value(
            config,
            "max_parallel_workers",
            default=default_max_parallel_workers,
            minimum=1,
        ),
        max_subtasks=_int_value(
            config,
            "max_subtasks",
            default=default_max_subtasks,
            minimum=2,
        ),
        max_decompose_depth=_int_value(
            config,
            "max_decompose_depth",
            default=default_max_decompose_depth,
            minimum=1,
        ),
    )
