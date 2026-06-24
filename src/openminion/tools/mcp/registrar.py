"""MCP tool registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openminion.modules.tool.contracts import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)

from .interfaces import require_mcp_tool_registration_state
from .plugin import (
    describe_mcp_prompt,
    describe_mcp_resource,
    describe_mcp_resource_template,
    describe_mcp_tool,
)

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext


class MCPRegistrar:
    module_id = "mcp"
    is_provider_only = False

    def get_manifest(self, ctx: ToolRegisterContext) -> ToolBindingManifest:
        state = require_mcp_tool_registration_state(
            getattr(ctx, "prepared_state", None)
        )
        model_tools = []
        runtime_bindings = []
        for tool in state.supported_tools:
            runtime_tool_name, runtime_binding_id = describe_mcp_tool(tool=tool)
            model_tools.append(
                ModelToolDef(
                    model_tool_id=runtime_tool_name,
                    description=tool.description or runtime_tool_name,
                    parameters={},
                )
            )
            runtime_bindings.append(
                RuntimeBindingDef(
                    runtime_binding_id=runtime_binding_id,
                    model_tool_id=runtime_tool_name,
                    runtime_candidates=(runtime_tool_name,),
                )
            )
        for prompt in state.supported_prompts:
            runtime_tool_name, runtime_binding_id = describe_mcp_prompt(prompt=prompt)
            model_tools.append(
                ModelToolDef(
                    model_tool_id=runtime_tool_name,
                    description=prompt.description or runtime_tool_name,
                    parameters={},
                )
            )
            runtime_bindings.append(
                RuntimeBindingDef(
                    runtime_binding_id=runtime_binding_id,
                    model_tool_id=runtime_tool_name,
                    runtime_candidates=(runtime_tool_name,),
                )
            )
        for resource in state.supported_resources:
            runtime_tool_name, runtime_binding_id = describe_mcp_resource(
                resource=resource
            )
            model_tools.append(
                ModelToolDef(
                    model_tool_id=runtime_tool_name,
                    description=resource.description
                    or resource.resource_name
                    or runtime_tool_name,
                    parameters={},
                )
            )
            runtime_bindings.append(
                RuntimeBindingDef(
                    runtime_binding_id=runtime_binding_id,
                    model_tool_id=runtime_tool_name,
                    runtime_candidates=(runtime_tool_name,),
                )
            )
        for template in state.supported_resource_templates:
            runtime_tool_name, runtime_binding_id = describe_mcp_resource_template(
                template=template
            )
            model_tools.append(
                ModelToolDef(
                    model_tool_id=runtime_tool_name,
                    description=template.description
                    or template.template_name
                    or runtime_tool_name,
                    parameters={},
                )
            )
            runtime_bindings.append(
                RuntimeBindingDef(
                    runtime_binding_id=runtime_binding_id,
                    model_tool_id=runtime_tool_name,
                    runtime_candidates=(runtime_tool_name,),
                )
            )
        return ToolBindingManifest(
            module_id=self.module_id,
            model_tools=tuple(model_tools),
            runtime_bindings=tuple(runtime_bindings),
        )

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext = None) -> None:
        """Register MCP tool handlers from prepared discovery state."""
        from .plugin import register as tool_register

        tool_register(registry, ctx)


REGISTRAR = MCPRegistrar()


__all__ = ["REGISTRAR"]
