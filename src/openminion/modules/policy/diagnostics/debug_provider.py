from openminion.base.debug import (
    DebugProvider,
    DebugStatus,
    ModuleDebugPayload,
    WiringSource,
    get_debug_registry,
)


def _probe_execution_boundary_policy() -> ModuleDebugPayload:
    return ModuleDebugPayload(
        module="execution.boundary.policy",
        status=DebugStatus.OK,
        mode="runtime",
        wiring_source=WiringSource.REAL,
        details={
            "adapter": "execution-boundary",
        },
    )


def _register_provider() -> None:
    registry = get_debug_registry()
    registry.register(
        DebugProvider(
            module_name="execution.boundary.policy",
            probe_fn=_probe_execution_boundary_policy,
        )
    )


_register_provider()
