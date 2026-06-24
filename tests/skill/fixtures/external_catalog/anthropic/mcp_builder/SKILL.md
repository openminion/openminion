---
name: mcp_builder
id: mcp_builder
tools: [file]
tags: [mcp, builder]
---

# Summary
Build and configure MCP server integrations for agent tool access.

# Procedure
- Identify the target service and its API surface.
- Scaffold the MCP server project structure with transport and handler stubs.
- Wire the tool definitions and test with a local agent session.

# Verification
- Confirm the MCP server starts, registers tools, and responds to a test call.
