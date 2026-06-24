from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlaywrightInstance:
    id: str
    context: Any
    browser: Any | None
    persistent: bool
    profile: str | None
    mode: str
    browser_name: str


@dataclass
class PlaywrightTab:
    id: str
    instance_id: str
    page: Any
    last_snapshot_hints: dict[str, str] = field(default_factory=dict)


class PlaywrightInstanceManager:
    def __init__(self) -> None:
        self._instances: dict[str, PlaywrightInstance] = {}
        self._counter = itertools.count(1)

    def add(
        self,
        *,
        context: Any,
        browser: Any | None,
        persistent: bool,
        profile: str | None,
        mode: str,
        browser_name: str,
    ) -> PlaywrightInstance:
        instance_id = f"inst_pw_{next(self._counter)}"
        inst = PlaywrightInstance(
            id=instance_id,
            context=context,
            browser=browser,
            persistent=persistent,
            profile=profile,
            mode=mode,
            browser_name=browser_name,
        )
        self._instances[instance_id] = inst
        return inst

    def get(self, instance_id: str) -> PlaywrightInstance:
        key = str(instance_id).strip()
        if key not in self._instances:
            raise KeyError(f"instance not found: {instance_id}")
        return self._instances[key]

    def remove(self, instance_id: str) -> PlaywrightInstance:
        key = str(instance_id).strip()
        if key not in self._instances:
            raise KeyError(f"instance not found: {instance_id}")
        return self._instances.pop(key)

    def list(self) -> list[PlaywrightInstance]:
        return [self._instances[key] for key in sorted(self._instances.keys())]

    def __len__(self) -> int:
        return len(self._instances)


class PlaywrightTabManager:
    def __init__(self) -> None:
        self._tabs: dict[str, PlaywrightTab] = {}
        self._counter = itertools.count(1)

    def add(self, *, instance_id: str, page: Any) -> PlaywrightTab:
        tab_id = f"tab_pw_{next(self._counter)}"
        tab = PlaywrightTab(id=tab_id, instance_id=instance_id, page=page)
        self._tabs[tab_id] = tab
        return tab

    def get(self, tab_id: str) -> PlaywrightTab:
        key = str(tab_id).strip()
        if key not in self._tabs:
            raise KeyError(f"tab not found: {tab_id}")
        return self._tabs[key]

    def remove(self, tab_id: str) -> PlaywrightTab:
        key = str(tab_id).strip()
        if key not in self._tabs:
            raise KeyError(f"tab not found: {tab_id}")
        return self._tabs.pop(key)

    def list(self, *, instance_id: str | None = None) -> list[PlaywrightTab]:
        rows = [self._tabs[key] for key in sorted(self._tabs.keys())]
        if instance_id:
            token = str(instance_id).strip()
            rows = [row for row in rows if row.instance_id == token]
        return rows

    def clear_for_instance(self, instance_id: str) -> list[PlaywrightTab]:
        token = str(instance_id).strip()
        deleted: list[PlaywrightTab] = []
        for tab_id in list(self._tabs.keys()):
            tab = self._tabs[tab_id]
            if tab.instance_id != token:
                continue
            deleted.append(self._tabs.pop(tab_id))
        return deleted
