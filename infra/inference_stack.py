"""Inference stack: the AWS-side API surface Salesforce calls out to.

v1 exposes only the suppression-mirror endpoint (``POST /suppressions``, IAM
SigV4 auth — signed by the ``AWS_Suppression`` Named Credential, the same auth
pattern settled for Salesforce->AWS callouts). The classifier API will be
added to this stack later.
"""

from pathlib import Path
from typing import Any

from aws_cdk import Duration, Stack
from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from constructs import Construct

from infra.layers import dependencies_layer

SUPPRESSION_HANDLER = "suppression.api.handler"

_SRC_PATH = str(Path(__file__).resolve().parent.parent / "src")


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
