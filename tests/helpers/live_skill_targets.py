from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from openminion.base.config import configured_agent_ids, load_config
from tests.helpers.live_e2e_profiles import agents_from_bundle, resolve_live_config_path

MATRIX_TYPE_SIMPLE = "skill_simple"
MATRIX_TYPE_DENSE = "skill_dense"

SURFACE_KIND_BUNDLE = "bundle"
SURFACE_KIND_SKILL_E2E = "skill_e2e"
SURFACE_KIND_OFFICIAL = "official"
SURFACE_KIND_PER_AGENT = "per_agent"

DENSE_TARGET_SET_ENV = "OPENMINION_LIVE_SKILL_DENSE_TARGET_SET"


@dataclass(frozen=True)
class SkillLiveTarget:
    target_id: str
    config_path: Path
    agent_id: str
    matrix_type: str
    surface_kind: str


def framework_root() -> Path:
    return Path(__file__).resolve().parents[3]


def openminion_root() -> Path:
    return framework_root() / "openminion"


def runtime_home_root() -> Path:
    return openminion_root()


def _target(
    *,
    target_id: str,
    config_filename: str,
    agent_id: str,
    matrix_type: str,
    surface_kind: str,
) -> SkillLiveTarget:
    return SkillLiveTarget(
        target_id=target_id,
        config_path=resolve_live_config_path(config_filename, framework_root()),
        agent_id=agent_id,
        matrix_type=matrix_type,
        surface_kind=surface_kind,
    )


def _bundle_targets(*, bundle_filename: str) -> tuple[SkillLiveTarget, ...]:
    profiles = agents_from_bundle(bundle_filename, framework_root=framework_root())
    return tuple(
        SkillLiveTarget(
            target_id=profile.profile_id,
            config_path=resolve_live_config_path(
                profile.config_path,
                framework_root(),
            ),
            agent_id=str(profile.agent_id or "").strip(),
            matrix_type=MATRIX_TYPE_SIMPLE,
            surface_kind=SURFACE_KIND_BUNDLE,
        )
        for profile in profiles
        if str(profile.agent_id or "").strip()
    )


def skill_simple_targets() -> tuple[SkillLiveTarget, ...]:
    return (
        _target(
            target_id="openrouter-claude-haiku-4-5",
            config_filename="per-agent-openrouter-claude-haiku-4-5-skill-e2e.json",
            agent_id="hello-agent",
            matrix_type=MATRIX_TYPE_SIMPLE,
            surface_kind=SURFACE_KIND_SKILL_E2E,
        ),
        _target(
            target_id="openrouter-glm-5-turbo",
            config_filename="per-agent-openrouter-glm-5-turbo-skill-e2e.json",
            agent_id="openrouter-glm-5-turbo",
            matrix_type=MATRIX_TYPE_SIMPLE,
            surface_kind=SURFACE_KIND_SKILL_E2E,
        ),
        _target(
            target_id="openrouter-minimax-m2-7",
            config_filename="per-agent-openrouter-minimax-m2-7-skill-e2e.json",
            agent_id="openrouter-minimax-m2-7",
            matrix_type=MATRIX_TYPE_SIMPLE,
            surface_kind=SURFACE_KIND_SKILL_E2E,
        ),
        _target(
            target_id="alibaba-minimax",
            config_filename="per-agent-alibaba-minimax-skill-e2e.json",
            agent_id="alibaba-minimax",
            matrix_type=MATRIX_TYPE_SIMPLE,
            surface_kind=SURFACE_KIND_SKILL_E2E,
        ),
        _target(
            target_id="openrouter-qwen3-5-35b-a3b",
            config_filename="per-agent-openrouter-qwen3-5-35b-a3b-skill-e2e.json",
            agent_id="hello-agent",
            matrix_type=MATRIX_TYPE_SIMPLE,
            surface_kind=SURFACE_KIND_SKILL_E2E,
        ),
        _target(
            target_id="openrouter-qwen3-5-9b",
            config_filename="per-agent-openrouter-qwen3-5-9b-skill-e2e.json",
            agent_id="hello-agent",
            matrix_type=MATRIX_TYPE_SIMPLE,
            surface_kind=SURFACE_KIND_SKILL_E2E,
        ),
        *_bundle_targets(bundle_filename="agents-alibaba.json"),
        *_bundle_targets(bundle_filename="agents-openrouter.json"),
    )


def official_skill_dense_targets() -> tuple[SkillLiveTarget, ...]:
    return (
        _target(
            target_id="minimax-m2-5",
            config_filename="per-agent-minimax-official-skill-e2e.json",
            agent_id="minimax-m2-5",
            matrix_type=MATRIX_TYPE_DENSE,
            surface_kind=SURFACE_KIND_OFFICIAL,
        ),
        _target(
            target_id="minimax-m2-7",
            config_filename="per-agent-minimax-official-skill-e2e.json",
            agent_id="minimax-m2-7",
            matrix_type=MATRIX_TYPE_DENSE,
            surface_kind=SURFACE_KIND_OFFICIAL,
        ),
    )


def cross_provider_skill_dense_targets() -> tuple[SkillLiveTarget, ...]:
    return (
        _target(
            target_id="ollamacloud-glm-5",
            config_filename="per-agent-ollamacloud-glm-5.json",
            agent_id="ollamacloud-glm-5",
            matrix_type=MATRIX_TYPE_DENSE,
            surface_kind=SURFACE_KIND_PER_AGENT,
        ),
        _target(
            target_id="ollamacloud-minimax-m2-7",
            config_filename="per-agent-ollamacloud-minimax-m2-7.json",
            agent_id="ollamacloud-minimax-m2-7",
            matrix_type=MATRIX_TYPE_DENSE,
            surface_kind=SURFACE_KIND_PER_AGENT,
        ),
        _target(
            target_id="openrouter-minimax-m2-7",
            config_filename="per-agent-openrouter-minimax-m2-7-skill-e2e.json",
            agent_id="openrouter-minimax-m2-7",
            matrix_type=MATRIX_TYPE_DENSE,
            surface_kind=SURFACE_KIND_SKILL_E2E,
        ),
        _target(
            target_id="openrouter-claude-haiku-4-5",
            config_filename="per-agent-openrouter-claude-haiku-4-5-skill-e2e.json",
            agent_id="hello-agent",
            matrix_type=MATRIX_TYPE_DENSE,
            surface_kind=SURFACE_KIND_SKILL_E2E,
        ),
        _target(
            target_id="openrouter-gpt-4o",
            config_filename="per-agent-openrouter-gpt-4o-skill-e2e.json",
            agent_id="hello-agent",
            matrix_type=MATRIX_TYPE_DENSE,
            surface_kind=SURFACE_KIND_SKILL_E2E,
        ),
    )


def representative_skill_dense_targets() -> tuple[SkillLiveTarget, ...]:
    return (*official_skill_dense_targets(), *cross_provider_skill_dense_targets())


def official_skill_matrix_target_ids() -> tuple[str, ...]:
    return tuple(target.target_id for target in official_skill_dense_targets())


def resolve_dense_skill_target_set(
    target_set: str | None = None,
) -> tuple[str, tuple[SkillLiveTarget, ...]]:
    requested = (
        str(target_set or os.getenv(DENSE_TARGET_SET_ENV, "official")).strip().lower()
        or "official"
    )
    if requested == "official":
        return requested, official_skill_dense_targets()
    if requested in {"representative", "cross-provider", "provider-reruns"}:
        return "representative", representative_skill_dense_targets()
    raise ValueError(
        f"unknown dense skill target set {requested!r}; expected 'official' or 'representative'"
    )


def dense_skill_artifact_dirname(target_set: str) -> str:
    if target_set == "official":
        return "skill-complex-official-matrix"
    if target_set == "representative":
        return "skill-complex-provider-reruns"
    raise ValueError(f"unknown dense skill target set {target_set!r}")


def validate_skill_live_target(target: SkillLiveTarget) -> None:
    resolved_config_path = resolve_live_config_path(
        target.config_path, framework_root()
    )
    if not resolved_config_path.exists():
        raise AssertionError(
            f"missing config file for live skill target {target.target_id}: {resolved_config_path}"
        )

    config = load_config(str(resolved_config_path), home_root=runtime_home_root())
    configured = configured_agent_ids(config)
    if target.agent_id not in configured:
        raise AssertionError(
            f"invalid live skill target {target.target_id}: agent_id={target.agent_id!r} "
            f"not in configured ids={configured} for {resolved_config_path.name}"
        )

    if (
        target.surface_kind == SURFACE_KIND_SKILL_E2E
        and not resolved_config_path.name.endswith("-skill-e2e.json")
    ):
        raise AssertionError(
            f"invalid live skill target {target.target_id}: expected a *-skill-e2e.json surface, "
            f"got {resolved_config_path.name}"
        )

    if (
        target.surface_kind == SURFACE_KIND_OFFICIAL
        and resolved_config_path.name
        not in {
            "per-agent-minimax-official.json",
            "per-agent-minimax-official-skill-e2e.json",
        }
    ):
        raise AssertionError(
            f"invalid official live skill target {target.target_id}: expected an official MiniMax config, "
            f"got {resolved_config_path.name}"
        )

    if target.surface_kind == SURFACE_KIND_BUNDLE and resolved_config_path.name not in {
        "agents-alibaba.json",
        "agents-openrouter.json",
    }:
        raise AssertionError(
            f"invalid bundle live skill target {target.target_id}: unexpected bundle surface "
            f"{resolved_config_path.name}"
        )


__all__ = [
    "DENSE_TARGET_SET_ENV",
    "MATRIX_TYPE_DENSE",
    "MATRIX_TYPE_SIMPLE",
    "SURFACE_KIND_BUNDLE",
    "SURFACE_KIND_OFFICIAL",
    "SURFACE_KIND_PER_AGENT",
    "SURFACE_KIND_SKILL_E2E",
    "SkillLiveTarget",
    "cross_provider_skill_dense_targets",
    "dense_skill_artifact_dirname",
    "framework_root",
    "official_skill_dense_targets",
    "official_skill_matrix_target_ids",
    "representative_skill_dense_targets",
    "resolve_dense_skill_target_set",
    "skill_simple_targets",
    "validate_skill_live_target",
]
