from pathlib import Path
from unittest import TestCase

from agentforge.core.models import ActionRequest
from agentforge.credentials import CredentialConfigurationError, HostCredentialBroker
from agentforge.policy.engine import SecurityPolicy


class HostCredentialBrokerTests(TestCase):
    def test_separates_host_secrets_from_public_settings(self) -> None:
        broker = HostCredentialBroker(
            {"HF_TOKEN": "hf_example", "AGENTFORGE_MODEL": "example/model"}
        )
        self.assertEqual(broker.require_secret("HF_TOKEN"), "hf_example")
        self.assertEqual(
            broker.require_setting("AGENTFORGE_MODEL"), "example/model"
        )
        with self.assertRaises(CredentialConfigurationError):
            broker.require_setting("HF_TOKEN")

    def test_missing_secret_reports_name_but_not_a_value(self) -> None:
        broker = HostCredentialBroker({})
        with self.assertRaisesRegex(CredentialConfigurationError, "HF_TOKEN"):
            broker.require_secret("HF_TOKEN")

    def test_policy_denies_credentials_even_if_name_was_allowlisted(self) -> None:
        policy = SecurityPolicy(allowed_environment_keys=frozenset({"HF_TOKEN"}))
        decision = policy.evaluate(
            ActionRequest("python", environment={"HF_TOKEN": "never-delegate"}),
            Path.cwd(),
        )
        self.assertFalse(decision.allowed)
        self.assertIn("Credentials", " ".join(decision.reasons))
