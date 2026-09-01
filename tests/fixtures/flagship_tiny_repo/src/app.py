class RuntimeGraph:
    """Coordinate knowledge graph provider results."""


def build_gateway_service() -> RuntimeGraph:
    """Wire the runtime graph into the gateway."""
    return RuntimeGraph()
