from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SampleConfig:
    """Config for the sample example module."""

    provider_id: str = "default"
    prefix: str = ""
    suffix: str = ""
    enabled: bool = True
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider_id or not str(self.provider_id).strip():
            raise ValueError("provider_id cannot be empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "prefix": self.prefix,
            "suffix": self.suffix,
            "enabled": self.enabled,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> SampleConfig:
        return cls(
            provider_id=str(data.get("provider_id", "default")),
            prefix=str(data.get("prefix", "")),
            suffix=str(data.get("suffix", "")),
            enabled=bool(data.get("enabled", True)),
            metadata=dict(data.get("metadata", {})),
        )
