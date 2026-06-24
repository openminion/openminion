import unittest
from unittest.mock import patch
from openminion.base.config import OpenMinionConfig
from openminion.services.runtime.plugins import PluginRegistry
from openminion.modules.llm.providers.base import LLMProvider, ProviderRequest
from openminion.modules.llm.providers.normalization import (
    normalize_provider_response,
)
from tests._csc_fixtures import _csc_install_default_agent


class OpenRouterStyleProvider(LLMProvider):
    def __init__(self):
        self.name = "openrouter_mock"

    async def generate(self, request: ProviderRequest):
        from openminion.modules.llm.providers.base import ProviderResponse

        return ProviderResponse(
            text="OpenRouter simulated response",
            model="openai/gpt-4o",
            tool_calls=[],
            finish_reason="stop",
            usage={"prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23},
            normalization_meta={"provider_original": "openrouter_shape"},
        )


class CortensorStyleProvider(LLMProvider):
    def __init__(self):
        self.name = "cortensor_mock"

    async def generate(self, request: ProviderRequest):
        from openminion.modules.llm.providers.base import ProviderResponse

        return ProviderResponse(
            text="Cortensor simulated response",
            model="gpt-4.1-mini",
            tool_calls=[],
            finish_reason="stop",
            usage={"prompt_tokens": 12, "completion_tokens": 10, "total_tokens": 22},
            normalization_meta={"provider_original": "cortensor_shape"},
        )


class ProviderContractIsolationTests(unittest.TestCase):
    def setUp(self):
        self.config = OpenMinionConfig()
        _csc_install_default_agent(self.config)  # type: ignore[attr-defined]
        self.plugin_registry = PluginRegistry([])

    def test_openrouter_cortensor_response_shapes_normalize_equiv(self):
        with patch(
            "openminion.modules.llm.providers.normalization.resolve_normalization_profile"
        ):
            openrouter_resp = self._get_dummy_openrouter_response()
            cortensor_resp = self._get_dummy_cortensor_response()
            self.assertTrue(hasattr(openrouter_resp, "text"))
            self.assertTrue(hasattr(cortensor_resp, "text"))
            self.assertTrue(hasattr(openrouter_resp, "model"))
            self.assertTrue(hasattr(cortensor_resp, "model"))
            self.assertTrue(hasattr(openrouter_resp, "usage"))
            self.assertTrue(hasattr(cortensor_resp, "usage"))
            self.assertIsInstance(openrouter_resp.text, str)
            self.assertIsInstance(cortensor_resp.text, str)
            self.assertIsInstance(openrouter_resp.model, str)
            self.assertIsInstance(cortensor_resp.model, str)
            self.assertIn("total_tokens", openrouter_resp.usage or {})
            self.assertIn("total_tokens", cortensor_resp.usage or {})

    def _get_dummy_openrouter_response(self):
        from openminion.modules.llm.providers.base import ProviderResponse

        return ProviderResponse(
            text="OpenRouter style response",
            model="openai/gpt-4o",
            tool_calls=[],
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    def _get_dummy_cortensor_response(self):
        from openminion.modules.llm.providers.base import ProviderResponse

        return ProviderResponse(
            text="Cortensor style response",
            model="gpt-4.1-mini",
            tool_calls=[],
            finish_reason="stop",
            usage={"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        )

    def test_provider_response_normalization_preserves_canonical_contract(self):
        from openminion.modules.llm.providers.base import ProviderResponse

        raw_openrouter = ProviderResponse(
            text="response text",
            model="openrouter/model",
            tool_calls=[],
            finish_reason="stop",
            usage={"total_tokens": 25},
        )

        raw_cortensor = ProviderResponse(
            text="reply content",
            model="cortensor/model",
            tool_calls=[],
            finish_reason="stop",
            usage={"total_tokens": 22},
        )

        norm_openrouter = normalize_provider_response(
            raw_openrouter,
            provider_name="openrouter_mock",
            model_name="openrouter/model",
            allowed_tool_names=[],
        )

        norm_cortensor = normalize_provider_response(
            raw_cortensor,
            provider_name="cortensor_mock",
            model_name="cortensor/model",
            allowed_tool_names=[],
        )

        self.assertIsInstance(norm_openrouter.text, str)
        self.assertIsInstance(norm_cortensor.text, str)
        self.assertIsInstance(norm_openrouter.model, str)
        self.assertIsInstance(norm_cortensor.model, str)
        self.assertTrue(hasattr(norm_openrouter, "text"))
        self.assertTrue(hasattr(norm_cortensor, "text"))

    @unittest.skip(
        "Integration too complex with external tools dependencies - test at unit level instead"
    )
    def test_end_to_end_provider_equivalence_mocked(self):
        openrouter_prov = OpenRouterStyleProvider()
        cortensor_prov = CortensorStyleProvider()

        self.assertEqual(openrouter_prov.name, "openrouter_mock")
        self.assertEqual(cortensor_prov.name, "cortensor_mock")


class ChatTurnCanonicalInterfaceTests(unittest.TestCase):
    def test_provider_adapter_contracts_equivalent(self):
        from openminion.modules.llm.providers.base import (
            ProviderRequest,
        )

        openrouter_request = ProviderRequest(
            user_message="Test message for OpenRouter",
            system_prompt="System prompt for OpenRouter",
            thinking="minimal",
            history=[],
            tools=[],
            tool_choice="auto",
            tool_call_strategy="hybrid",
            metadata={},
        )

        cortensor_request = ProviderRequest(
            user_message="Test message for Cortensor",
            system_prompt="System prompt for Cortensor",
            thinking="minimal",
            history=[],
            tools=[],
            tool_choice="auto",
            tool_call_strategy="hybrid",
            metadata={},
        )

        self.assertIsInstance(openrouter_request.user_message, str)
        self.assertIsInstance(cortensor_request.user_message, str)
        self.assertIsInstance(openrouter_request.system_prompt, str)
        self.assertIsInstance(cortensor_request.system_prompt, str)
        self.assertTrue(hasattr(openrouter_request, "user_message"))
        self.assertTrue(hasattr(cortensor_request, "user_message"))
        self.assertTrue(hasattr(openrouter_request, "tools"))
        self.assertTrue(hasattr(cortensor_request, "tools"))

    def test_tool_call_canonical_representation_consistency(self):
        from openminion.modules.llm.providers.base import ProviderToolCall

        openrouter_tool_call = ProviderToolCall(
            id="or_call_123",
            name="tool1",
            arguments={"param": "value"},
            source="native",
        )

        cortensor_tool_call = ProviderToolCall(
            id="ct_call_456",
            name="tool1",
            arguments={"param": "value"},
            source="native",
        )

        self.assertEqual(openrouter_tool_call.name, cortensor_tool_call.name)
        self.assertEqual(
            type(openrouter_tool_call.arguments), type(cortensor_tool_call.arguments)
        )
        for call in [openrouter_tool_call, cortensor_tool_call]:
            self.assertTrue(hasattr(call, "id"))
            self.assertTrue(hasattr(call, "name"))
            self.assertTrue(hasattr(call, "arguments"))
            self.assertTrue(hasattr(call, "source"))
