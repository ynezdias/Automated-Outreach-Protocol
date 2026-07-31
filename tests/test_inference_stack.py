"""Unit tests for the inference stack (suppression API + classify service)."""

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from infra.data_stack import DataStack
from infra.inference_stack import (
    CLASSIFY_HANDLER,
    CLASSIFY_TOKEN_SECRET_NAME,
    SUPPRESSION_HANDLER,
    InferenceStack,
)


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App()
    data = DataStack(app, "TestDataStack")
    return Template.from_stack(
        InferenceStack(app, "TestInferenceStack", data_bucket=data.data_bucket)
    )


def test_suppression_lambda_exists(template: Template) -> None:
    template.resource_count_is("AWS::Lambda::Function", 2)
    template.has_resource_properties(
        "AWS::Lambda::Function", Match.object_like({"Handler": SUPPRESSION_HANDLER})
    )


def test_post_suppressions_requires_iam_signature(template: Template) -> None:
    # SigV4 at the method: an unsigned caller never reaches the Lambda.
    template.has_resource_properties(
        "AWS::ApiGateway::Method",
        Match.object_like({"HttpMethod": "POST", "AuthorizationType": "AWS_IAM"}),
    )


def test_classify_lambda_reads_token_from_secrets_manager(template: Template) -> None:
    template.has_resource_properties(
        "AWS::Lambda::Function",
        Match.object_like(
            {
                "Handler": CLASSIFY_HANDLER,
                "Environment": {
                    "Variables": {
                        "CLASSIFY_TOKEN_SECRET_ID": CLASSIFY_TOKEN_SECRET_NAME,
                        "CLASSIFY_HANDOFF_INTENTS": (
                            "Interested,Call_Request,Amount_Given,Question,Process_Update"
                        ),
                    }
                },
            }
        ),
    )
    template.has_resource_properties(
        "AWS::SecretsManager::Secret",
        Match.object_like(
            {
                "Name": CLASSIFY_TOKEN_SECRET_NAME,
                "GenerateSecretString": Match.object_like({"PasswordLength": 48}),
            }
        ),
    )


def test_classify_has_a_function_url_with_in_function_auth(template: Template) -> None:
    template.has_resource_properties("AWS::Lambda::Url", Match.object_like({"AuthType": "NONE"}))
