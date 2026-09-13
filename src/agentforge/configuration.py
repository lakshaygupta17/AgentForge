"""Local configuration loading without exposing secret values."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    hugging_face_token: bool
    hugging_face_token_format: bool
    hugging_face_model: bool
    e2b_api_key: bool

    @property
    def ready(self) -> bool:
        return (
            self.hugging_face_token
            and self.hugging_face_token_format
            and self.hugging_face_model
            and self.e2b_api_key
        )


def load_project_environment(directory: Path | None = None) -> bool:
    """Load ``.env`` from one explicit directory without overriding the process."""

    root = (directory or Path.cwd()).resolve()
    return load_dotenv(dotenv_path=root / ".env", override=False)


def credential_status() -> CredentialStatus:
    """Report presence and safe format metadata without returning secret values."""

    hugging_face_token = os.environ.get("HF_TOKEN", "").strip()
    return CredentialStatus(
        hugging_face_token=bool(hugging_face_token),
        hugging_face_token_format=hugging_face_token.startswith("hf_"),
        hugging_face_model=bool(os.environ.get("AGENTFORGE_MODEL", "").strip()),
        e2b_api_key=bool(os.environ.get("E2B_API_KEY", "").strip()),
    )
