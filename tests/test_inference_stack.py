"""Unit tests for the inference stack (suppression-mirror API)."""

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from infra.data_stack import DataStack
from infra.inference_stack import SUPPRESSION_HANDLER, InferenceStack


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App()
    data = DataStack(app, "TestDataStack")
    return Template.from_stack(
        InferenceStack(app, "TestInferenceStack", data_bucket=data.data_bucket)
    )


def test_suppression_lambda_exists(template: Template) -> None:
    template.resource_count_is("AWS::Lambda::Function", 1)
    template.has_resource_properties(
        "AWS::Lambda::Function", Match.object_like({"Handler": SUPPRESSION_HANDLER})
    )


def test_post_suppressions_requires_iam_signature(template: Template) -> None:
    # SigV4 at the method: an unsigned caller never reaches the Lambda.
    template.has_resource_properties(
        "AWS::ApiGateway::Method",
        Match.object_like({"HttpMethod": "POST", "AuthorizationType": "AWS_IAM"}),
    )
