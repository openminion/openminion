from typing import Any

from ..schemas import ContextPack, RenderMessage


def _normalize_openai_role(role: str) -> str:
    return "system" if role == "developer" else role


def render_openai(pack: ContextPack | dict[str, Any], *, model: str) -> dict[str, Any]:
    """Render a ContextPack as an OpenAI chat completion request payload.

    All message roles (system, user, assistant, tool) are passed through as-is.
    Developer role is mapped to system for OpenAI compatibility.
    """
    if isinstance(pack, ContextPack):
        messages = [_normalize_message_openai(m) for m in pack.messages]
    else:
        messages = [_normalize_message_dict_openai(m) for m in pack.get("messages", [])]
    return {
        "model": model,
        "messages": messages,
    }


def render_anthropic(
    pack: ContextPack | dict[str, Any], *, model: str
) -> dict[str, Any]:
    """Render a ContextPack as an Anthropic messages API request payload.

    System and developer blocks are merged into top-level `system` string.
    Only user/assistant turns go into `messages`.
    """
    if isinstance(pack, ContextPack):
        raw_messages = [{"role": m.role, "content": m.content} for m in pack.messages]
    else:
        raw_messages = list(pack.get("messages", []))

    system_parts = [
        item["content"]
        for item in raw_messages
        if item["role"] in {"system", "developer"}
    ]
    messages = []
    for item in raw_messages:
        role = item["role"]
        if role not in {"user", "assistant"}:
            continue
        messages.append(
            {"role": role, "content": [{"type": "text", "text": item["content"]}]}
        )

    return {
        "model": model,
        "system": "\n\n".join(system_parts).strip(),
        "messages": messages,
    }


def render_openai_with_tools(
    pack: ContextPack | dict[str, Any],
    *,
    model: str,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] = "auto",
) -> dict[str, Any]:
    """Extended OpenAI render with tool/function calling payload."""
    payload = render_openai(pack, model=model)
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice
    return payload


def _normalize_message_openai(message: RenderMessage) -> dict[str, Any]:
    return {
        "role": _normalize_openai_role(message.role),
        "content": message.content,
    }


def _normalize_message_dict_openai(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": _normalize_openai_role(str(message.get("role", "user"))),
        "content": message.get("content", ""),
    }
