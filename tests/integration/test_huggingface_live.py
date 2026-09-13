"""Opt-in smoke test for one minimal Hugging Face inference request."""

import os

import pytest

from agentforge.configuration import load_project_environment
from agentforge.llm.huggingface import HuggingFaceProvider
from agentforge.llm.models import ChatMessage
from agentforge.llm.openai_compatible import RetryPolicy


@pytest.mark.integration
def test_real_hugging_face_chat_completion() -> None:
    load_project_environment()
    if os.environ.get("AGENTFORGE_RUN_HF_TESTS") != "1":
        pytest.skip("set AGENTFORGE_RUN_HF_TESTS=1 for one real inference request")

    provider = HuggingFaceProvider.from_environment()
    provider.retry_policy = RetryPolicy(maximum_attempts=1)
    response = provider.chat(
        [
            ChatMessage(
                "user",
                "Reply with exactly AGENTFORGE_OK. Do not explain or use tools.",
            )
        ]
    )

    assert response.message.content
    assert response.model
