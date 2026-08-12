from dataclasses import dataclass, field


@dataclass
class OTELExporterConfig:
    enabled: bool = False
    endpoint: str = ""
    protocol: str = "http"
    service_name: str = "openminion"
    sample_rate: float = 1.0
    include_assistant_body: bool = False
    include_input_messages: bool = False
    include_output_messages: bool = False
    include_tool_content: bool = False
    include_local_content: bool = False
    backend: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    noncritical_queue_capacity: int = 1024
    queue_flush_timeout_seconds: float = 2.0
