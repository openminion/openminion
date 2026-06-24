from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from openminion.modules.llm import LLMCTL
from openminion.modules.llm.schemas import LLMRequest, LLMResponse, Message, UsageInfo


class _BudgetProvider:
    name = "budget_provider"
    contract_version = "v1"

    def __init__(self, *, cost_usd: Optional[float]) -> None:
        self._cost_usd = cost_usd

    def complete(self, request: LLMRequest, config: Dict[str, Any]) -> LLMResponse:
        del config
        return LLMResponse(
            ok=True,
            provider=self.name,
            model=request.model or "budget-model",
            output_text="ok",
            assistant_messages=[Message(role="assistant", content="ok")],
            tool_calls=[],
            usage=UsageInfo(input_tokens=1000, output_tokens=500, total_tokens=1500),
            latency_ms=0,
            cost_usd=self._cost_usd,
            provider_raw={},
            error=None,
        )

    def list_models(self, config: Dict[str, Any]) -> list[str]:
        del config
        return ["budget-model"]

    def healthcheck(self, config: Dict[str, Any]) -> Dict[str, Any]:
        del config
        return {"ok": True}


def test_client_cost_budget_uses_estimated_cost_when_provider_cost_missing() -> None:
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {
                "default_provider": "budget_provider",
                "default_model": "budget-model",
            },
            "providers": {
                "budget_provider": {
                    "cost_hint": {
                        "input_per_1k": 0.01,
                        "output_per_1k": 0.02,
                    }
                }
            },
            "agents": {
                "default": {
                    "default_provider": "budget_provider",
                    "default_model": "budget-model",
                    "budgets": {"max_cost_usd": 0.01},
                }
            },
        }
    )
    runtime.registry.add(_BudgetProvider(cost_usd=None))
    client = runtime.client(agent_name="default")

    response = client.complete(messages=[{"role": "user", "content": "hello"}])

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "POLICY_DENIED"
    assert response.error.details.get("cost_source") == "estimated"
    assert response.error.details.get("estimated_cost_usd") == 0.02


def test_client_cost_budget_prefers_provider_supplied_cost_over_estimate() -> None:
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {
                "default_provider": "budget_provider",
                "default_model": "budget-model",
            },
            "providers": {
                "budget_provider": {
                    "cost_hint": {
                        "input_per_1k": 100.0,
                        "output_per_1k": 100.0,
                    }
                }
            },
            "agents": {
                "default": {
                    "default_provider": "budget_provider",
                    "default_model": "budget-model",
                    "budgets": {"max_cost_usd": 0.01},
                }
            },
        }
    )
    runtime.registry.add(_BudgetProvider(cost_usd=0.005))
    client = runtime.client(agent_name="default")

    response = client.complete(messages=[{"role": "user", "content": "hello"}])

    assert response.ok is True


def test_client_cost_budget_logs_warning_when_cost_unassessable(caplog: Any) -> None:
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {
                "default_provider": "budget_provider",
                "default_model": "budget-model",
            },
            "providers": {"budget_provider": {}},
            "agents": {
                "default": {
                    "default_provider": "budget_provider",
                    "default_model": "budget-model",
                    "budgets": {"max_cost_usd": 0.01},
                }
            },
        }
    )
    runtime.registry.add(_BudgetProvider(cost_usd=None))
    client = runtime.client(agent_name="default")

    with caplog.at_level(logging.WARNING):
        response = client.complete(messages=[{"role": "user", "content": "hello"}])

    assert response.ok is True
    assert "llm.cost_budget.unassessable" in caplog.text
