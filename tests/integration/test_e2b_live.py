"""Opt-in smoke test that creates a billable E2B cloud microVM."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from agentforge.configuration import load_project_environment
from agentforge.core.models import ActionRequest
from agentforge.policy.engine import SecurityPolicy
from agentforge.sandbox.e2b import E2BSandbox


@pytest.mark.integration
def test_real_e2b_microvm_round_trip() -> None:
    load_project_environment()
    if os.environ.get("AGENTFORGE_RUN_E2B_TESTS") != "1":
        pytest.skip("set AGENTFORGE_RUN_E2B_TESTS=1 to create a real E2B microVM")
    if not os.environ.get("E2B_API_KEY"):
        pytest.skip("E2B_API_KEY is required for the real E2B test")

    with TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        source = root / "source"
        output = root / "output"
        source.mkdir()
        (source / "message.txt").write_text("AgentForge", encoding="utf-8")
        with E2BSandbox() as sandbox:
            sandbox.import_workspace(source)
            result = sandbox.execute(
                ActionRequest("cat", ("message.txt",)), SecurityPolicy()
            )
            sandbox.export_workspace(output)

        assert result.succeeded
        assert result.stdout == "AgentForge"
        assert (output / "message.txt").read_text(encoding="utf-8") == "AgentForge"
