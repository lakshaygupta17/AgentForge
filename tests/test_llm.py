import json
from collections.abc import Callable
from dataclasses import dataclass, field
from unittest import TestCase
from unittest.mock import patch

from agentforge.llm.budget import BudgetedProvider, ModelPricing, SharedInferenceBudget
from agentforge.llm.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderConnectionError,
    ProviderResponseError,
)
from agentforge.llm.fallback import TransientFallbackProvider
from agentforge.llm.huggingface import HuggingFaceProvider
from agentforge.llm.mock import MockLLMProvider
from agentforge.llm.models import (
    ChatMessage,
    ChatResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from agentforge.llm.openai_compatible import OpenAICompatibleProvider, RetryPolicy
from agentforge.llm.transport import HttpResponse


@dataclass
class FakeHttpTransport:
    outcomes: list[HttpResponse | BaseException]
    calls: list[dict[str, object]] = field(default_factory=list)

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HttpResponse:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def response(document: dict[str, object], status: int = 200) -> HttpResponse:
    return HttpResponse(status, json.dumps(document).encode(), {})


def successful_document(message: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "id": "request-1",
        "model": "test-model",
        "choices": [
            {
                "message": message or {"role": "assistant", "content": "done"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
    }


class OpenAICompatibleProviderTests(TestCase):
    def provider(
        self,
        transport: FakeHttpTransport,
        sleeper: Callable[[float], None] = lambda _: None,
    ) -> OpenAICompatibleProvider:
        return OpenAICompatibleProvider(
            base_url="https://models.example/v1/",
            api_key="secret-token",
            model="test-model",
            transport=transport,
            retry_policy=RetryPolicy(maximum_attempts=3, initial_delay_seconds=0),
            sleeper=sleeper,
        )

    def test_parses_text_and_usage(self) -> None:
        transport = FakeHttpTransport([response(successful_document())])

        result = self.provider(transport).chat([ChatMessage("user", "Fix the bug")])

        self.assertEqual(result.message.content, "done")
        self.assertEqual(result.usage.total_tokens, 13)
        self.assertEqual(transport.calls[0]["url"], "https://models.example/v1/chat/completions")
        headers = transport.calls[0]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer secret-token")  # type: ignore[index]

    def test_serializes_definitions_and_parses_tool_calls(self) -> None:
        tool_message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"src/app.py"}',
                    },
                }
            ],
        }
        transport = FakeHttpTransport([response(successful_document(tool_message))])
        tool = ToolDefinition(
            "read_file",
            "Read a repository file",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )

        result = self.provider(transport).chat(
            [ChatMessage("user", "Inspect the app")],
            tools=[tool],
            tool_choice="required",
        )

        self.assertEqual(
            result.message.tool_calls,
            (ToolCall("call-1", "read_file", {"path": "src/app.py"}),),
        )
        sent = json.loads(transport.calls[0]["body"])
        self.assertEqual(sent["tool_choice"], "required")
        self.assertEqual(sent["tools"][0]["function"]["name"], "read_file")

    def test_serializes_the_configured_output_limit(self) -> None:
        transport = FakeHttpTransport([response(successful_document())])
        provider = OpenAICompatibleProvider(
            base_url="https://models.example/v1",
            api_key="secret-token",
            model="test-model",
            maximum_output_tokens=1234,
            transport=transport,
        )

        provider.chat([ChatMessage("user", "hello")])

        sent = json.loads(transport.calls[0]["body"])
        self.assertEqual(sent["max_tokens"], 1234)

    def test_retries_rate_limit_then_returns_success(self) -> None:
        transport = FakeHttpTransport(
            [
                response({"error": {"message": "slow down"}}, status=429),
                response(successful_document()),
            ]
        )

        result = self.provider(transport).chat([ChatMessage("user", "hello")])

        self.assertEqual(result.message.content, "done")
        self.assertEqual(len(transport.calls), 2)

    def test_authentication_errors_are_not_retried(self) -> None:
        transport = FakeHttpTransport([response({"error": {"message": "bad token"}}, status=401)])

        with self.assertRaisesRegex(ProviderAuthenticationError, "bad token"):
            self.provider(transport).chat([ChatMessage("user", "hello")])

        self.assertEqual(len(transport.calls), 1)

    def test_rejects_malformed_tool_arguments(self) -> None:
        document = successful_document(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {"name": "read_file", "arguments": "not-json"},
                    }
                ],
            }
        )
        transport = FakeHttpTransport([response(document), response(document)])

        with self.assertRaisesRegex(ProviderResponseError, "malformed tool call"):
            self.provider(transport).chat([ChatMessage("user", "hello")])

        self.assertEqual(len(transport.calls), 2)

    def test_retries_one_malformed_tool_call_then_returns_success(self) -> None:
        malformed = successful_document(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "bad-call",
                        "function": {"name": "read_file", "arguments": "{"},
                    }
                ],
            }
        )
        valid = successful_document(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "good-call",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"app.py"}',
                        },
                    }
                ],
            }
        )
        transport = FakeHttpTransport([response(malformed), response(valid)])
        tool = ToolDefinition(
            "read_file",
            "Read a repository file",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )

        result = self.provider(transport).chat(
            [ChatMessage("user", "inspect")],
            tools=(tool,),
            tool_choice="required",
        )

        self.assertEqual(result.message.tool_calls[0].arguments, {"path": "app.py"})
        self.assertEqual(len(transport.calls), 2)


class MockLLMProviderTests(TestCase):
    def test_returns_scripted_responses_and_records_requests(self) -> None:
        scripted = ChatResponse(ChatMessage("assistant", "a plan"), "mock-model")
        provider = MockLLMProvider([scripted])

        result = provider.chat([ChatMessage("user", "plan this")])

        self.assertIs(result, scripted)
        self.assertEqual(provider.requests[0].messages[0].content, "plan this")


class RaisingProvider:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def chat(
        self,
        messages: object,
        *,
        tools: object = (),
        tool_choice: object = "auto",
    ) -> ChatResponse:
        self.calls += 1
        raise self.error


class TransientFallbackProviderTests(TestCase):
    def test_connection_failure_opens_circuit_and_uses_fallback(self) -> None:
        primary = RaisingProvider(ProviderConnectionError("timed out"))
        fallback = MockLLMProvider(
            [
                ChatResponse(ChatMessage("assistant", "first"), "deepseek"),
                ChatResponse(ChatMessage("assistant", "second"), "deepseek"),
            ]
        )
        provider = TransientFallbackProvider(primary, fallback)

        first = provider.chat([ChatMessage("user", "test")])
        second = provider.chat([ChatMessage("user", "test again")])

        self.assertEqual(first.model, "deepseek")
        self.assertEqual(second.model, "deepseek")
        self.assertEqual(primary.calls, 1)
        self.assertTrue(provider.circuit_open)
        self.assertEqual(provider.fallback_count, 2)

    def test_malformed_response_does_not_trigger_fallback(self) -> None:
        primary = RaisingProvider(ProviderResponseError("bad response"))
        fallback = MockLLMProvider([ChatResponse(ChatMessage("assistant", "unused"), "deepseek")])
        provider = TransientFallbackProvider(primary, fallback)

        with self.assertRaises(ProviderResponseError):
            provider.chat([ChatMessage("user", "test")])

        self.assertFalse(provider.circuit_open)
        self.assertEqual(fallback.requests, [])

    def test_probe_can_disable_primary_before_a_real_agent_request(self) -> None:
        primary = RaisingProvider(ProviderConnectionError("unavailable"))
        fallback = MockLLMProvider(
            [ChatResponse(ChatMessage("assistant", "tester report"), "deepseek")]
        )
        provider = TransientFallbackProvider(primary, fallback)

        available = provider.probe_primary([ChatMessage("user", "health check")])
        response = provider.chat([ChatMessage("user", "real request")])

        self.assertFalse(available)
        self.assertEqual(response.model, "deepseek")
        self.assertEqual(primary.calls, 1)


class HuggingFaceProviderTests(TestCase):
    def test_loads_credentials_and_model_from_environment(self) -> None:
        with patch.dict(
            "os.environ",
            {"HF_TOKEN": "hf_test", "AGENTFORGE_MODEL": "org/model:cheapest"},
            clear=True,
        ):
            provider = HuggingFaceProvider.from_environment()

        self.assertEqual(provider.base_url, "https://router.huggingface.co/v1")
        self.assertEqual(provider.model, "org/model:cheapest")

    def test_reports_all_missing_environment_settings(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ProviderConfigurationError) as raised:
                HuggingFaceProvider.from_environment()

        self.assertIn("HF_TOKEN", str(raised.exception))
        self.assertIn("AGENTFORGE_MODEL", str(raised.exception))


class BudgetedProviderTests(TestCase):
    def test_shared_request_limit_blocks_a_second_provider(self) -> None:
        budget = SharedInferenceBudget(
            maximum_requests=1,
            maximum_tokens=100_000,
            maximum_cost_usd=1.0,
        )
        first = BudgetedProvider(
            MockLLMProvider([ChatResponse(ChatMessage("assistant", "first"), "fast")]),
            budget,
            pricing=ModelPricing(0.1, 0.2),
            maximum_output_tokens=100,
        )
        second = BudgetedProvider(
            MockLLMProvider([ChatResponse(ChatMessage("assistant", "second"), "strong")]),
            budget,
            pricing=ModelPricing(0.2, 0.6),
            maximum_output_tokens=100,
        )

        first.chat([ChatMessage("user", "hello")])

        with self.assertRaisesRegex(Exception, "request limit 1 reached"):
            second.chat([ChatMessage("user", "hello")])
        self.assertTrue(budget.snapshot().exhausted)

    def test_cost_is_recorded_from_provider_usage(self) -> None:
        raw = MockLLMProvider(
            [
                ChatResponse(
                    ChatMessage("assistant", "done"),
                    "model",
                    usage=TokenUsage(10, 3, 13),
                )
            ]
        )
        budget = SharedInferenceBudget(
            maximum_requests=2,
            maximum_tokens=100_000,
            maximum_cost_usd=1.0,
        )
        provider = BudgetedProvider(
            raw,
            budget,
            pricing=ModelPricing(0.2, 0.6),
            maximum_output_tokens=100,
        )

        provider.chat([ChatMessage("user", "hello")])

        self.assertAlmostEqual(budget.snapshot().estimated_cost_usd, 0.0000038)
