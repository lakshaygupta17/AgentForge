"""Hugging Face Inference Providers configuration."""

from __future__ import annotations

from collections.abc import Callable

from agentforge.credentials import CredentialConfigurationError, HostCredentialBroker
from agentforge.llm.errors import ProviderConfigurationError
from agentforge.llm.openai_compatible import OpenAICompatibleProvider, RetryPolicy
from agentforge.llm.transport import HttpTransport


class HuggingFaceProvider(OpenAICompatibleProvider):
    DEFAULT_BASE_URL = "https://router.huggingface.co/v1"

    def __init__(
        self,
        *,
        token: str,
        model: str,
        timeout_seconds: float = 60.0,
        maximum_output_tokens: int | None = None,
        retry_policy: RetryPolicy | None = None,
        transport: HttpTransport | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if sleeper is None:
            super().__init__(
                base_url=self.DEFAULT_BASE_URL,
                api_key=token,
                model=model,
                timeout_seconds=timeout_seconds,
                maximum_output_tokens=maximum_output_tokens,
                retry_policy=retry_policy,
                transport=transport,
            )
        else:
            super().__init__(
                base_url=self.DEFAULT_BASE_URL,
                api_key=token,
                model=model,
                timeout_seconds=timeout_seconds,
                maximum_output_tokens=maximum_output_tokens,
                retry_policy=retry_policy,
                transport=transport,
                sleeper=sleeper,
            )

    @classmethod
    def from_environment(
        cls,
        *,
        token_variable: str = "HF_TOKEN",
        model_variable: str = "AGENTFORGE_MODEL",
    ) -> HuggingFaceProvider:
        broker = HostCredentialBroker.from_process()
        missing = [
            variable
            for variable in (token_variable, model_variable)
            if not broker.is_configured(variable)
        ]
        if missing:
            raise ProviderConfigurationError(
                "Missing required environment variables: " + ", ".join(missing)
            )
        try:
            token = broker.require_secret(token_variable)
            model = broker.require_setting(model_variable)
        except CredentialConfigurationError as error:
            raise ProviderConfigurationError(str(error)) from error
        return cls(token=token, model=model)
