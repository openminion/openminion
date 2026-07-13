from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .schemas import OperationTarget


class TargetRegistry:
    def __init__(self, targets: Iterable[OperationTarget] = ()) -> None:
        self._targets: dict[str, OperationTarget] = {}
        for target in targets:
            self.register(target)

    def register(self, target: OperationTarget) -> None:
        current = self._targets.get(target.target_id)
        if current is not None and target.revision <= current.revision:
            raise ValueError(
                f"target {target.target_id!r} revision must increase beyond "
                f"{current.revision}"
            )
        self._targets[target.target_id] = target

    def get(self, target_id: str) -> OperationTarget:
        try:
            return self._targets[target_id]
        except KeyError as exc:
            raise KeyError(f"unknown operation target: {target_id}") from exc

    def list(self) -> tuple[OperationTarget, ...]:
        return tuple(self._targets[key] for key in sorted(self._targets))


def registry_from_config(config: Mapping[str, Any]) -> TargetRegistry:
    raw_targets = config.get("targets", ())
    if not isinstance(raw_targets, (list, tuple)):
        raise TypeError("runtime.system_operations.targets must be a list")
    return TargetRegistry(OperationTarget.model_validate(item) for item in raw_targets)
