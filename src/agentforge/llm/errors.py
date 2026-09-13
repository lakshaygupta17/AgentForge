"""Errors that agents can handle without depending on an HTTP library."""


class LLMProviderError(RuntimeError):
    """Base class for model-provider failures."""


class ProviderConfigurationError(LLMProviderError):
    """Required provider settings are absent or invalid."""


class ProviderAuthenticationError(LLMProviderError):
    """The provider rejected the configured credential."""


class ProviderRateLimitError(LLMProviderError):
    """The provider remained rate-limited after retry attempts."""


class ProviderConnectionError(LLMProviderError):
    """The provider could not be reached after retry attempts."""


class ProviderResponseError(LLMProviderError):
    """The provider returned an invalid or unsupported response."""

