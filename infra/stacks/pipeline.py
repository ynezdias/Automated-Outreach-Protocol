"""Pipeline stack: orchestration and processing resources."""

from typing import Any

from aws_cdk import Stack
from constructs import Construct


class PipelineStack(Stack):
    """Empty skeleton for pipeline resources."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)
