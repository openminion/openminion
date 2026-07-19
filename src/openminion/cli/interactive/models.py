from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SidebarItem:
    id: str
    label: str
    active: bool = False
    meta: dict | None = None
