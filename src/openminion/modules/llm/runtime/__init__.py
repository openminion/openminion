from .sync import run_async_compat


def __getattr__(name: str):
    if name not in {"LLMCTL", "LLMClient", "ToolPolicyContext"}:
        raise AttributeError(name)
    from .client import LLMCTL, LLMClient, ToolPolicyContext

    return {
        "LLMCTL": LLMCTL,
        "LLMClient": LLMClient,
        "ToolPolicyContext": ToolPolicyContext,
    }[name]


__all__ = [
    "LLMCTL",
    "LLMClient",
    "ToolPolicyContext",
    "run_async_compat",
]
