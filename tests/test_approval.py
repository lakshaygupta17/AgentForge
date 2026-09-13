from unittest import TestCase

from agentforge.core.approval import (
    ApprovalCapability,
    ApprovalCheckpoint,
    ApprovalRequiredError,
)


class ApprovalCheckpointTests(TestCase):
    def test_approvals_are_independent_capabilities(self) -> None:
        checkpoint = ApprovalCheckpoint(code_transmission=True)

        checkpoint.require(ApprovalCapability.CODE_TRANSMISSION)
        with self.assertRaises(ApprovalRequiredError) as raised:
            checkpoint.require(ApprovalCapability.SANDBOX_EXECUTION)

        self.assertIs(
            raised.exception.capability, ApprovalCapability.SANDBOX_EXECUTION
        )
        self.assertEqual(checkpoint.granted_capabilities(), ("code_transmission",))

    def test_empty_checkpoint_fails_closed(self) -> None:
        checkpoint = ApprovalCheckpoint()

        with self.assertRaises(ApprovalRequiredError):
            checkpoint.require(ApprovalCapability.CODE_TRANSMISSION)
        with self.assertRaises(ApprovalRequiredError):
            checkpoint.require(ApprovalCapability.SANDBOX_EXECUTION)

        self.assertEqual(checkpoint.granted_capabilities(), ())

    def test_both_capabilities_can_be_approved_explicitly(self) -> None:
        checkpoint = ApprovalCheckpoint(
            code_transmission=True,
            sandbox_execution=True,
        )

        checkpoint.require(ApprovalCapability.CODE_TRANSMISSION)
        checkpoint.require(ApprovalCapability.SANDBOX_EXECUTION)

        self.assertEqual(
            checkpoint.granted_capabilities(),
            ("code_transmission", "sandbox_execution"),
        )
