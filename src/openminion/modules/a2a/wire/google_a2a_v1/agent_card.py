from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

A2A_PROTOCOL_VERSION = "1.0"
AGENT_CARD_WELL_KNOWN_PATH = "/.well-known/agent.json"


@dataclass
class AgentSkill:
    """One skill the agent exposes via A2A."""

    id: str
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    input_modes: list[str] = field(default_factory=lambda: ["text"])
    output_modes: list[str] = field(default_factory=lambda: ["text"])

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            "examples": list(self.examples),
            "inputModes": list(self.input_modes),
            "outputModes": list(self.output_modes),
        }


@dataclass
class AgentCapabilities:
    """Capability flags exposed by the agent endpoint."""

    streaming: bool = True
    push_notifications: bool = False
    state_transition_history: bool = True
    long_running_tasks: bool = False

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "streaming": self.streaming,
            "pushNotifications": self.push_notifications,
            "stateTransitionHistory": self.state_transition_history,
            "longRunningTasks": self.long_running_tasks,
        }


@dataclass
class AgentCard:
    """Public agent descriptor served at ``/.well-known/agent.json``."""

    name: str
    description: str
    url: str
    version: str
    protocol_version: str = A2A_PROTOCOL_VERSION
    provider_organization: str | None = None
    provider_url: str | None = None
    documentation_url: str | None = None
    capabilities: AgentCapabilities = field(default_factory=AgentCapabilities)
    authentication_schemes: list[str] = field(default_factory=lambda: ["bearer"])
    default_input_modes: list[str] = field(default_factory=lambda: ["text"])
    default_output_modes: list[str] = field(default_factory=lambda: ["text"])
    skills: list[AgentSkill] = field(default_factory=list)

    def to_jsonable(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "protocolVersion": self.protocol_version,
            "capabilities": self.capabilities.to_jsonable(),
            "authenticationSchemes": list(self.authentication_schemes),
            "defaultInputModes": list(self.default_input_modes),
            "defaultOutputModes": list(self.default_output_modes),
            "skills": [skill.to_jsonable() for skill in self.skills],
        }
        for field_name, payload_key in (
            ("provider_organization", "providerOrganization"),
            ("provider_url", "providerUrl"),
            ("documentation_url", "documentationUrl"),
        ):
            value = getattr(self, field_name)
            if value:
                payload[payload_key] = value
        return payload


def build_agent_card(
    *,
    name: str,
    description: str,
    url: str,
    version: str,
    skills: list[AgentSkill] | None = None,
    capabilities: AgentCapabilities | None = None,
    provider_organization: str | None = "openminion",
    provider_url: str | None = "https://www.openminion.com",
    documentation_url: str | None = None,
) -> AgentCard:
    return AgentCard(
        name=name,
        description=description,
        url=url,
        version=version,
        skills=list(skills) if skills is not None else [],
        capabilities=capabilities or AgentCapabilities(),
        provider_organization=provider_organization,
        provider_url=provider_url,
        documentation_url=documentation_url,
    )


_asdict = asdict
