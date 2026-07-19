# Provider Capabilities

OpenMinion provider profiles declare the features they support. Runtime requests
can require those features before a provider call starts, which keeps routing
deterministic and avoids probing providers with unsupported requests.

## Profile facts

A profile may declare these transport capabilities:

- `supports_json`
- `supports_tools`
- `supports_vision`
- `supports_streaming`
- `supports_prompt_caching`

The runtime capability matrix also reports whether the profile includes cost
metadata and an authentication reference. Those are configuration facts, not
claims that credentials are valid or that an endpoint is currently reachable.

```yaml
profiles:
  - id: primary
    provider: openai-compatible
    model: example-model
    auth_ref: env:PROVIDER_API_KEY
    capabilities:
      supports_json: true
      supports_tools: true
      supports_streaming: true
    cost_hint:
      input_per_1k: 0.001
      output_per_1k: 0.002
```

## Request requirements

`RuntimeLLMRequest.required_capabilities` accepts explicit capability names:

```python
from openminion.modules.llm.orchestration import RuntimeLLMRequest

request = RuntimeLLMRequest(
    purpose="act",
    required_capabilities=["tools", "streaming"],
)
```

Structured-output requests automatically require `json`. A profile that lacks
any required capability is rejected with `INVALID_ARGUMENT` before its provider
transport is called. Agent routing may then use its configured fallback profiles
without first sending an incompatible request to the primary profile.

The runtime does not infer capabilities from provider names, model names,
responses, or failed calls. Update the profile configuration when provider facts
change.

## Inspecting the matrix

Library callers can build a reproducible matrix from the loaded catalog:

```python
from openminion.modules.llm.orchestration import provider_capability_matrix

for row in provider_capability_matrix(catalog):
    print(row.as_dict())
```

This matrix describes configured facts. Provider certification remains a
separate exercise that proves endpoint access and behavior with live evidence.

