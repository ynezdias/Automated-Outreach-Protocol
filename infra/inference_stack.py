"""Inference stack: the AWS-side API surface.

Two independent endpoints:

- Suppression mirror (``POST /suppressions``, IAM SigV4 auth — signed by the
  ``AWS_Suppression`` Named Credential, the settled Salesforce->AWS pattern).
- The rules-v1 classify service (ADR-023): a plain-zip Lambda behind a
  Function URL with app-level bearer auth. It classifies and returns JSON,
  nothing else — its asset ships only ``classifier`` + ``guardrails``, and
  its role reads exactly one secret (the bearer token). No Salesforce
  wiring, no send capability.
"""

from pathlib import Path
from typing import Any

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from infra.layers import dependencies_layer

SUPPRESSION_HANDLER = "suppression.api.handler"
CLASSIFY_HANDLER = "classifier.lambda_api.handler"
CLASSIFY_TOKEN_SECRET_NAME = "outreach/classify/token"  # pragma: allowlist secret
DEFAULT_HANDOFF_INTENTS = "Interested,Call_Request,Amount_Given,Question"

_SRC_PATH = str(Path(__file__).resolve().parent.parent / "src")

# Everything the classify service must NOT ship: the send path, Salesforce
# clients, and pipeline code. The zip contains classifier/ and guardrails/.
_CLASSIFY_EXCLUDES = [
    "cleansing",
    "salesforce",
    "suppression",
    "texttorrent",
    "**/__pycache__",
]


class InferenceStack(Stack):
    """Suppression-mirror API; classifier serving joins later."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        data_bucket: s3.IBucket,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        suppression_fn = lambda_.Function(
            self,
            "SuppressionApiFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            layers=[dependencies_layer(self, "DependenciesLayer")],
            code=lambda_.Code.from_asset(_SRC_PATH),
            handler=SUPPRESSION_HANDLER,
            timeout=Duration.seconds(30),
            memory_size=512,
            environment={"DATA_BUCKET": data_bucket.bucket_name},
        )
        data_bucket.grant_read_write(suppression_fn)

        api = apigateway.RestApi(self, "OutreachApi", rest_api_name="outreach-api")
        api.root.add_resource("suppressions").add_method(
            "POST",
            apigateway.LambdaIntegration(suppression_fn),
            authorization_type=apigateway.AuthorizationType.IAM,
        )

        # The identity Salesforce signs as (AWS_Suppression Named Credential).
        # Scoped to invoking exactly this one method; access keys are minted
        # manually and live only inside the Named Credential (see runbook).
        caller = iam.User(
            self, "SalesforceSuppressionCaller", user_name="salesforce-suppression-caller"
        )
        caller.attach_inline_policy(
            iam.Policy(
                self,
                "InvokeSuppressionApi",
                statements=[
                    iam.PolicyStatement(
                        actions=["execute-api:Invoke"],
                        resources=[api.arn_for_execute_api("POST", "/suppressions")],
                    )
                ],
            )
        )

        # --- rules-v1 classify service (ADR-023) ---
        # The bearer token is generated server-side by Secrets Manager: it
        # never appears in code, CDK context, or a terminal.
        classify_token = secretsmanager.Secret(
            self,
            "ClassifyToken",
            secret_name=CLASSIFY_TOKEN_SECRET_NAME,
            description="Bearer token for the rules-v1 classify Function URL",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=48, exclude_punctuation=True
            ),
        )
        classify_fn = lambda_.Function(
            self,
            "ClassifyFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            # Plain zip, no dependency layer: rules-only, stdlib + runtime boto3.
            code=lambda_.Code.from_asset(_SRC_PATH, exclude=_CLASSIFY_EXCLUDES),
            handler=CLASSIFY_HANDLER,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment={
                "CLASSIFY_TOKEN_SECRET_ID": CLASSIFY_TOKEN_SECRET_NAME,
                # Handoff list changes without a redeploy: edit this env var.
                "CLASSIFY_HANDOFF_INTENTS": DEFAULT_HANDOFF_INTENTS,
            },
        )
        classify_token.grant_read(classify_fn)
        classify_url = classify_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE  # bearer auth is in-function
        )
        CfnOutput(self, "ClassifyUrl", value=classify_url.url)
