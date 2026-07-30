"""Inference stack: model serving and inference resources."""

from typing import Any

from aws_cdk import Stack
from constructs import Construct


class InferenceStack(Stack):
    """Empty skeleton for inference resources."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)
