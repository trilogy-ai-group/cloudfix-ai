"""Exceptions shared by the CLI commands."""
from __future__ import annotations


class InputError(Exception):
    """The user supplied invalid input."""


class MissingEvidence(Exception):
    """Replay mode has no recorded response for an AWS call."""

    def __init__(self, service: str, operation: str, input: dict):
        self.request = {
            "status": "needs_evidence",
            "service": service,
            "operation": operation,
            "input": input,
        }
        super().__init__(f"missing evidence for {service}.{operation}")


class AwsCallError(Exception):
    """An AWS call failed."""

    def __init__(self, service: str, operation: str, code: str, message: str):
        self.service = service
        self.operation = operation
        self.code = code
        self.message = message
        super().__init__(f"{service}.{operation} failed: {code}: {message}")
