"""Failures caused by an agent violating its expected response contract."""


class AgentProtocolError(RuntimeError):
    """A model response could not be safely converted into agent state."""

