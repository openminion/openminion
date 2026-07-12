from pathlib import Path

import yaml  # type: ignore[import-untyped]

_DEFAULT_ANTONYM_CONFIG_PAYLOAD = {
    "version": 1,
    "pairs": [
        ["enabled", "disabled"],
        ["allow", "deny"],
        ["allow", "block"],
        ["allow", "forbid"],
        ["use", "avoid"],
        ["require", "optional"],
        ["required", "forbidden"],
        ["accept", "reject"],
        ["include", "exclude"],
        ["always", "never"],
        ["true", "false"],
        ["public", "private"],
        ["open", "closed"],
        ["start", "stop"],
        ["sync", "async"],
        ["synchronous", "asynchronous"],
        ["online", "offline"],
        ["local", "remote"],
        ["read", "write"],
        ["reads", "writes"],
        ["success", "failure"],
        ["enabled", "inactive"],
        ["active", "inactive"],
        ["safe", "unsafe"],
        ["allowlist", "denylist"],
        ["present", "absent"],
        ["accepts", "rejects"],
        ["preferred", "discouraged"],
        ["recommended", "discouraged"],
        ["required", "optional"],
        ["strict", "lenient"],
        ["incremental", "full"],
        ["pinned", "unpinned"],
        ["stable", "unstable"],
        ["cached", "uncached"],
        ["verbose", "terse"],
        ["verbose", "concise"],
        ["detailed", "brief"],
        ["long", "short"],
        ["expand", "summarize"],
    ],
}


def _ensure_default_yaml_payload(path: str | Path, payload: dict[str, object]) -> Path:
    resolved = Path(path).expanduser().resolve(strict=False)
    if resolved.exists():
        return resolved
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        yaml.safe_dump(
            payload,
            sort_keys=False,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    return resolved


def ensure_default_antonym_config(path: str | Path) -> Path:
    return _ensure_default_yaml_payload(path, _DEFAULT_ANTONYM_CONFIG_PAYLOAD)


def load_antonym_pairs(config_path: str | Path) -> set[frozenset[str]]:
    path = Path(config_path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(
            "memory antonyms payload must be a mapping"
        )  # allow-bare-raise: config payload validation guard
    pairs_payload = payload.get("pairs") or []
    if not isinstance(pairs_payload, list):
        raise ValueError(
            "memory antonyms pairs must be a list"
        )  # allow-bare-raise: config payload validation guard
    pairs: set[frozenset[str]] = set()
    for raw_pair in pairs_payload:
        if not isinstance(raw_pair, (list, tuple)) or len(raw_pair) != 2:
            continue
        left = str(raw_pair[0] or "").strip().lower()
        right = str(raw_pair[1] or "").strip().lower()
        if not left or not right or left == right:
            continue
        pairs.add(frozenset((left, right)))
    if not pairs:
        raise ValueError(
            "memory antonyms config did not contain any valid pairs"
        )  # allow-bare-raise: config payload validation guard
    return pairs


__all__ = [
    "ensure_default_antonym_config",
    "load_antonym_pairs",
]
