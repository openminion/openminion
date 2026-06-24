from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LiveAgentProfile:
    profile_id: str
    config_path: Path
    agent_id: str | None = None


def resolve_live_config_path(config_path: str | Path, framework_root: Path) -> Path:
    candidate = Path(config_path)
    if candidate.is_absolute():
        return candidate
    direct = framework_root / candidate
    if direct.exists():
        return direct
    return framework_root / "test-configs" / candidate


def agents_from_bundle(
    bundle_filename: str | Path,
    *,
    framework_root: Path,
    profile_prefix: str = "bundle",
) -> tuple[LiveAgentProfile, ...]:
    bundle_path = resolve_live_config_path(bundle_filename, framework_root)
    if not bundle_path.exists():
        return ()
    with open(bundle_path, encoding="utf-8") as fh:
        data = json.load(fh)
    agents = data.get("agents", {})
    if not isinstance(agents, dict):
        return ()
    config_ref = (
        bundle_path
        if bundle_path.is_absolute()
        and bundle_path.parent != framework_root / "test-configs"
        else Path(bundle_filename)
    )
    return tuple(
        LiveAgentProfile(
            profile_id=f"{profile_prefix}:{agent_key}",
            config_path=config_ref,
            agent_id=agent_key,
        )
        for agent_key in sorted(agents)
    )


def parse_live_agent_targets_env(
    env_name: str,
    *,
    framework_root: Path,
) -> tuple[LiveAgentProfile, ...]:
    raw = str(os.getenv(env_name, "")).strip()
    if not raw:
        return ()

    profiles: list[LiveAgentProfile] = []
    for token in raw.split(","):
        target = token.strip()
        if not target:
            continue
        if target.startswith("bundle:"):
            profiles.extend(
                agents_from_bundle(
                    target[len("bundle:") :],
                    framework_root=framework_root,
                )
            )
            continue

        config_token, sep, agent_token = target.partition("#")
        config_token = config_token.strip()
        agent_id = agent_token.strip() if sep else None
        if not config_token:
            continue
        profile_id = agent_id or Path(config_token).stem
        profiles.append(
            LiveAgentProfile(
                profile_id=profile_id,
                config_path=Path(config_token),
                agent_id=agent_id or None,
            )
        )

    return tuple(profiles)
