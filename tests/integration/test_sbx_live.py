"""Opt-in smoke test for a real Docker Sandboxes microVM."""

import os
from pathlib import Path

import pytest

from agentforge.core.models import ActionRequest
from agentforge.policy.engine import SecurityPolicy
from agentforge.sandbox.sbx import SbxSandbox, _resolve_sbx_executable

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("AGENTFORGE_RUN_SBX_TESTS") != "1"
        or _resolve_sbx_executable() is None,
        reason="set AGENTFORGE_RUN_SBX_TESTS=1 with sbx configured to run",
    ),
]


def test_import_execute_export_in_a_real_microvm(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    (source / "note.txt").write_text("isolated input\n", encoding="utf-8")

    with SbxSandbox() as sandbox:
        sandbox.import_workspace(source)
        sandbox.start(SecurityPolicy())
        result = sandbox.execute(
            ActionRequest("echo", ("hello from sbx",)), SecurityPolicy()
        )
        sandbox.export_workspace(output)

    assert result.succeeded
    assert result.stdout.strip() == "hello from sbx"
    assert (output / "note.txt").is_file()
