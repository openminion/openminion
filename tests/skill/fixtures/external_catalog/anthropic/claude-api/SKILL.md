---
name: claude-api
id: claude-api
tools: [http_request]
tags: [api, backend]
---

# Summary
Build against the Claude API with safe request patterns and response checks.

# Procedure
- Check requirements.txt, pyproject.toml, or setup.py for the anthropic SDK dependency.
- Also check package.json if the project uses the TypeScript SDK.
- Review the models.md reference for available model identifiers.
- Use Anthropic.Tool definitions for structured tool use, e.g. function calling.
- Draft the request payload and required headers.
- Validate response fields before consuming the result.
- Update the README.md with usage examples after integration.

# Verification
- Confirm model output shape and error handling expectations.
