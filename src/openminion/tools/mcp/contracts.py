MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_PROTOCOL_VERSION_FLOOR = "2025-03-26"
MCP_MODERN_PROTOCOL_VERSION = "2026-07-28"
MCP_SUPPORTED_PROTOCOL_VERSIONS = (
    MCP_MODERN_PROTOCOL_VERSION,
    MCP_PROTOCOL_VERSION,
)


def protocol_version_tuple(value: str) -> tuple[int, int, int]:
    token = str(value or "").strip()
    parts = token.split("-")
    if len(parts) != 3:
        raise ValueError(f"Unsupported MCP protocol version format: {value!r}")
    year, month, day = (int(part) for part in parts)
    return (year, month, day)


__all__ = [
    "MCP_PROTOCOL_VERSION",
    "MCP_PROTOCOL_VERSION_FLOOR",
    "MCP_MODERN_PROTOCOL_VERSION",
    "MCP_SUPPORTED_PROTOCOL_VERSIONS",
    "protocol_version_tuple",
]
