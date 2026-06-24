from __future__ import annotations

from openminion.providers.base import LLMProvider, ProviderRequest, ProviderResponse


class HelloProvider(LLMProvider):
    name = "hello"

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        user = request.user_message.strip() or "friend"
        text = f"Hello, {user}. This reply came from HelloProvider."
        usage = {
            "input_chars": len(request.user_message),
            "output_chars": len(text),
        }
        return ProviderResponse(text=text, model="hello-v1", usage=usage)
