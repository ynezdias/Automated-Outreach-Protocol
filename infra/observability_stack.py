"""Observability stack: monitoring, logging, and alerting resources."""

from typing import Any

from aws_cdk import Stack
from constructs import Construct


class ObservabilityStack(Stack):
    """Empty skeleton for observability resources."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)
