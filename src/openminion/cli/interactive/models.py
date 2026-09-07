from dataclasses import dataclass


@dataclass
class SidebarItem:
    id: str
    label: str
    active: bool = False
    meta: dict | None = None


@dataclass(frozen=True)
class ModelSelection:
    index: int
    connection_id: str
    connection_name: str
    provider: str
    transport_adapter: str
    model: str
    configured_connection: bool = True
    active: bool = False
    agent_default: bool = False
