"""Data stack: storage and data platform resources."""

from typing import Any

from aws_cdk import Stack
from constructs import Construct


class DataStack(Stack):
    """Empty skeleton for data platform resources."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)
