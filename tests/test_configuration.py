import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from agentforge.configuration import credential_status, load_project_environment


class ProjectEnvironmentTests(TestCase):
    def test_credential_status_reports_presence_without_values(self) -> None:
        environment = {
            "HF_TOKEN": "hf_private-test-value",
            "AGENTFORGE_MODEL": "example/model",
            "E2B_API_KEY": "private-e2b-value",
        }
        with patch.dict(os.environ, environment, clear=True):
            status = credential_status()

        self.assertTrue(status.ready)
        self.assertTrue(status.hugging_face_token)
        self.assertTrue(status.hugging_face_token_format)
        self.assertTrue(status.hugging_face_model)
        self.assertTrue(status.e2b_api_key)

    def test_loads_values_from_explicit_dotenv_directory(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / ".env").write_text(
                "AGENTFORGE_TEST_DOTENV=loaded\n", encoding="utf-8"
            )
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AGENTFORGE_TEST_DOTENV", None)
                loaded = load_project_environment(root)
                value = os.environ.get("AGENTFORGE_TEST_DOTENV")
                os.environ.pop("AGENTFORGE_TEST_DOTENV", None)

        self.assertTrue(loaded)
        self.assertEqual(value, "loaded")

    def test_existing_process_value_wins_over_dotenv(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / ".env").write_text(
                "AGENTFORGE_TEST_DOTENV=from-file\n", encoding="utf-8"
            )
            with patch.dict(
                os.environ, {"AGENTFORGE_TEST_DOTENV": "from-process"}, clear=False
            ):
                load_project_environment(root)
                value = os.environ["AGENTFORGE_TEST_DOTENV"]

        self.assertEqual(value, "from-process")
